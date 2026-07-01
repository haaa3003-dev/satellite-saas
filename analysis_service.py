# analysis_service.py
"""
분석 서비스 레이어.

기존 app.py의 if run_btn: 블록 안에 흩어져 있던
비즈니스 로직을 이 모듈로 집중한다.

책임:
- AnalysisRequest를 받아 GEE 호출을 조율하고 AnalysisResult를 반환
- 현재/전년 통계 조회 및 SatelliteStatistics 변환
- 시계열 조회
- 교차 진단 실행
- 타일 URL 생성

UI(Streamlit) 의존성을 갖지 않는다.
→ 단위 테스트 시 GEE를 mock으로 대체해 이 레이어만 테스트할 수 있다.
"""
from __future__ import annotations

import logging
from datetime import date

import ee
import streamlit as st

from cross_diagnosis import find_cross_pairs_for_mode
from exceptions import GEEAuthenticationError, GEENoDataError, GEEQuotaError, GEETimeoutError
from gee_utils import (
    get_cached_stats,
    get_ee_tile_url,
    get_satellite_index_for_period,
    get_time_series,
)
from models import (
    AnalysisRequest,
    AnalysisResult,
    CrossDiagnosisResult,
    SatelliteStatistics,
)
from mode_config import mode_config

logger = logging.getLogger(__name__)


@st.cache_data(ttl=3600, show_spinner=False)
def _reverse_geocode_sigungu(cx: float, cy: float) -> tuple[str, str]:
    """
    bbox 중심 좌표 → Nominatim 역지오코딩 → (시군구명, 시/도명) 추출.

    우선순위: county(군) > city(시) > city_district(구/동)
    "구"가 있는 광역시는 city_district가 구 단위(예: "중구")라 정상이지만,
    일반 시는 city_district가 동 단위(예: "증포동")로 나와 Vworld 시군구
    레이어와 매칭 실패하므로, county/city를 우선한다.

    st.cache_data로 세션 동안 캐싱 — 같은 지역 반복 조회 시 즉시 반환.
    """
    import requests as _req
    try:
        r = _req.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": cy, "lon": cx, "format": "json", "accept-language": "ko"},
            headers={"User-Agent": "ksat-open-explorer/1.0"},
            timeout=5,
        )
        addr = r.json().get("address", {})
        province = addr.get("state") or addr.get("province") or ""

        # county(군/시) 우선, 없으면 city(특별시/광역시 산하 구의 상위 시),
        # 그래도 없으면 city_district(구 단위 — 광역시인 경우만 유효)
        sigungu = addr.get("county") or addr.get("city") or addr.get("city_district") or ""

        # "동/읍/면"으로 끝나면 읍면동 단위이므로 시군구가 아님 → city로 재시도
        if sigungu.endswith(("동", "읍", "면")) and addr.get("city"):
            sigungu = addr["city"]

        return (sigungu, province)
    except Exception as e:
        logger.warning("역지오코딩 실패 | error=%s", e)
        return ("", "")


@st.cache_data(ttl=3600, show_spinner=False)
def _get_sigungu_code(api_key: str, sigungu_name: str, province: str = "") -> str:
    """
    시군구명으로 Vworld 행정구역 DB에서 법정동코드(5자리, sig_cd)를 조회한다.
    PNU코드 검색에 필요 — 연속지적도는 시군구코드 기반으로만 일괄 조회 가능.

    "중구"처럼 전국에 여러 곳 있는 이름은 province로 full_nm을 검증해 동명이인을 가른다.
    """
    from vworld import _data_api_request
    attr_filter = f"sig_kor_nm:=:{sigungu_name}"
    geojson = _data_api_request(api_key, "LT_C_ADSIGG_INFO", attr_filter=attr_filter, max_features=10)
    if not geojson or not geojson.get("features"):
        return ""

    features = geojson["features"]
    if len(features) == 1 or not province:
        return features[0].get("properties", {}).get("sig_cd", "")

    # 동명이인 시군구 — province(시/도명)가 full_nm에 포함되는 것을 우선 선택
    for feat in features:
        props = feat.get("properties", {})
        full_nm = props.get("full_nm", "")
        if province in full_nm:
            return props.get("sig_cd", "")

    return features[0].get("properties", {}).get("sig_cd", "")


# 시군구명으로 단일 검색 가능한 행정구역 레이어 (전략 1 전용)
_ADMIN_LAYERS = {"LT_C_ADSIGG_INFO", "LT_C_ADSIDO_INFO"}


@st.cache_data(ttl=3600, show_spinner=False)
def _get_vworld_geojson(
    bbox: tuple[float, float, float, float],
    index_name: str,
    api_key: str,
) -> dict | None:
    """
    분석 모드에 맞는 Vworld 폴리곤을 GeoJSON dict로 반환한다 (캐싱 가능).
    ee.FeatureCollection은 직렬화가 안 되므로 dict 단계까지만 캐싱하고,
    호출부(_get_vworld_feature_collection)에서 ee 객체로 변환한다.

    전략 분기:
    - 행정구역 모드(LST, NDBI): bbox 중심 → 시군구명 → attrFilter 단일 검색
    - 농경지 모드(NDVI, NDRE): bbox 중심 → 시군구코드(5자리) → PNU 기반 연속지적도 조회
      (LP_PA_CBND_BUBUN은 WFS가 아닌 DATA API 전용 레이어)
    - 수계·산림 모드(NDWI, NBR, SAR_VV, SAR_VH): bbox + DATA API 직접 검색
    - NO2: 마스킹 없음
    """
    from vworld import VWORLD_LAYER_MAP, _data_api_request, get_vworld_boundary

    layer_cfg = VWORLD_LAYER_MAP.get(index_name)
    if layer_cfg is None:
        return None

    layer, _unused_filter = layer_cfg
    west, south, east, north = bbox
    cx = round((west + east) / 2, 2)  # 약 1km 단위 — 캐시 히트율 향상
    cy = round((south + north) / 2, 2)

    # ── 전략 2: 농경지 필지 (NDVI, NDRE) — PNU코드 기반 연속지적도 조회 ──────
    if layer == "LP_PA_CBND_BUBUN":
        sigungu, province = _reverse_geocode_sigungu(cx, cy)
        if not sigungu:
            logger.warning("시군구명 조회 실패 | bbox=%s", bbox)
            return None

        sig_cd = _get_sigungu_code(api_key, sigungu, province)
        if not sig_cd:
            logger.warning("시군구코드 조회 실패 | sigungu=%s province=%s", sigungu, province)
            return None

        attr_filter = f"pnu:like:{sig_cd}"
        geojson = _data_api_request(
            api_key, layer, attr_filter=attr_filter, max_features=1000
        )
        if geojson and geojson.get("features"):
            w, s, e, n = bbox

            def _in_bbox(coords: list) -> bool:
                """좌표 중 하나라도 bbox 안에 있으면 True."""
                return any(w <= c[0] <= e and s <= c[1] <= n for c in coords)

            filtered = []
            for feat in geojson["features"]:
                # 지목 필터
                jibun = feat.get("properties", {}).get("jibun", "").strip()
                if not jibun.endswith(("전", "답", "과", "목")):
                    continue
                # bbox 교차 필터
                geom = feat.get("geometry") or {}
                coords_flat: list = []
                if geom.get("type") == "Polygon":
                    for ring in geom.get("coordinates", []):
                        coords_flat.extend(ring)
                elif geom.get("type") == "MultiPolygon":
                    for poly in geom.get("coordinates", []):
                        for ring in poly:
                            coords_flat.extend(ring)
                if coords_flat and _in_bbox(coords_flat):
                    filtered.append(feat)

            feat_count = len(filtered)
            logger.info(
                "Vworld 연속지적도 OK | %s(%s) raw=%d farmland_in_bbox=%d",
                sigungu, sig_cd, len(geojson["features"]), feat_count,
            )
            if feat_count == 0:
                logger.warning("bbox 내 농경지 필지 없음 — ESA+DW fallback | sigungu=%s", sigungu)
                return None
            return {"type": "FeatureCollection", "features": filtered}
        logger.warning("Vworld 연속지적도 없음 | sigungu=%s sig_cd=%s", sigungu, sig_cd)
        return None

    # ── 전략 1: 행정구역 단일 레이어 (LST, NDBI) ────────────────────────────
    if layer in _ADMIN_LAYERS:
        sigungu, province = _reverse_geocode_sigungu(cx, cy)
        if not sigungu:
            logger.warning("시군구명 조회 실패 | bbox=%s", bbox)
            return None

        attr_filter = f"sig_kor_nm:=:{sigungu}"
        geojson = _data_api_request(api_key, layer, attr_filter=attr_filter, max_features=10)
        if geojson and geojson.get("features"):
            features = geojson["features"]
            # 동명이인 시군구 처리 — province로 검증
            if len(features) > 1 and province:
                for feat in features:
                    full_nm = feat.get("properties", {}).get("full_nm", "")
                    if province in full_nm:
                        geojson = {"type": "FeatureCollection", "features": [feat]}
                        break
            logger.info("Vworld 행정구역 OK | %s features=%d", sigungu, len(geojson["features"]))
            return geojson
        logger.warning("Vworld 행정구역 없음 | sigungu=%s", sigungu)
        return None

    # ── 전략 3: 수계·산림 (NDWI, NBR, SAR_VV, SAR_VH) — bbox 직접 검색 ───────
    geojson = get_vworld_boundary(bbox, index_name, api_key, max_features=1000)
    if geojson and geojson.get("features"):
        logger.info("Vworld OK | index=%s features=%d", index_name, len(geojson["features"]))
        return geojson

    logger.warning("Vworld 응답 없음 | index=%s bbox=%s", index_name, bbox)
    return None


def _get_vworld_feature_collection(
    bbox: tuple[float, float, float, float],
    index_name: str,
    api_key: str,
):
    """
    Vworld 폴리곤을 ee.FeatureCollection으로 반환한다.
    GeoJSON 조회는 _get_vworld_geojson에서 캐싱 처리.
    실패 시 None 반환 → 호출부에서 격자 마스킹 fallback.
    """
    geojson = _get_vworld_geojson(bbox, index_name, api_key)
    if geojson is None:
        return None
    try:
        return ee.FeatureCollection(geojson)
    except Exception as e:
        logger.warning("ee.FeatureCollection 변환 실패 | error=%s", e)
        return None


def is_good_value(val: float, cfg: dict) -> bool:
    """
    higher_is_worse 플래그를 보고 값이 '양호' 방향인지 판단한다.

    기존: app.py의 렌더링 블록 안에 정의돼 있어
          is_good_value()가 차트 생성 중간에 존재했다.
    개선: 비즈니스 판단 로직을 서비스 레이어로 이동.
    """
    if cfg.get("higher_is_worse", False):
        return val < cfg["threshold"]
    return val >= cfg["threshold"]


def run_analysis(request: AnalysisRequest) -> AnalysisResult:
    """분석 요청을 받아 전체 분석을 실행하고 AnalysisResult를 반환한다."""
    import time as _time
    _t_start = _time.time()

    region = request.region
    cfg = mode_config[request.mode_key]
    bbox = region.bbox  # (west, south, east, north)
    s_date, e_date = str(request.start_date), str(request.end_date)
    cloud = request.cloud_threshold

    logger.info(
        "Analysis started | mode=%s bbox=%s period=%s~%s",
        request.mode_key, bbox, s_date, e_date,
    )

    # ── 1. 현재 기간 통계 ─────────────────────────────────────────────
    _t = _time.time()
    count, raw_stats = get_cached_stats(bbox, s_date, e_date, cloud, cfg)
    current = SatelliteStatistics.extract_from_gee_dict(raw_stats, cfg["index_name"], count)
    logger.info("[TIMING] 현재 통계 %.2fs", _time.time() - _t)

    if not current.has_data:
        logger.info("No satellite data | mode=%s period=%s~%s", request.mode_key, s_date, e_date)
        raise GEENoDataError(
            "지정한 기간과 지역에 유효한 위성 영상이 없습니다. "
            "구름 허용률을 높이거나 기간을 넓혀보세요."
        )

    # ── 2. 전년 동기 통계 ─────────────────────────────────────────────
    _t = _time.time()
    ly_start = request.start_date.replace(year=request.start_date.year - 1)
    ly_end = request.end_date.replace(year=request.end_date.year - 1)
    last_count, last_raw = get_cached_stats(bbox, str(ly_start), str(ly_end), cloud, cfg)
    last_year = SatelliteStatistics.extract_from_gee_dict(
        last_raw, cfg["index_name"], last_count
    )
    logger.info("[TIMING] 전년 통계 %.2fs", _time.time() - _t)

    # ── 3. 시계열 ─────────────────────────────────────────────────────
    _t = _time.time()
    time_series = get_time_series(bbox, s_date, e_date, cloud, cfg)
    logger.info("[TIMING] 시계열 %.2fs", _time.time() - _t)

    # ── 4. 교차 진단 — 지연 로딩 (기본 분석에서는 계산하지 않음) ────────────
    # 탭/expander를 펼칠 때 compute_cross_diagnosis()를 별도 호출한다.
    cross_results: list[CrossDiagnosisResult] = []

    # ── 5. 타일 URL ───────────────────────────────────────────────────
    _t = _time.time()
    west, south, east, north = bbox
    geo_region = ee.Geometry.BBox(west, south, east, north)
    _, calculated_index = get_satellite_index_for_period(
        geo_region, s_date, e_date, cloud, cfg
    )
    logger.info("[TIMING] 타일 인덱스 계산 %.2fs", _time.time() - _t)

    # ── 토지피복 마스킹 (Vworld 폴리곤 우선 → ESA+DW 격자 fallback) ──────────
    _t = _time.time()
    vworld_key = ""
    try:
        import streamlit as _st  # noqa: PLC0415
        vworld_key = _st.secrets.get("vworld_api_key", "")
    except Exception:
        pass

    vworld_fc = None
    if vworld_key:
        vworld_fc = _get_vworld_feature_collection(bbox, cfg["index_name"], vworld_key)

    if vworld_fc is not None:
        # Vworld 폴리곤 모양 그대로 clip — 행정구역/수계도/임야도/농경지 경계대로 표시됨
        # simplify(10m)로 좌표점을 단순화해 clip 연산 속도 개선.
        # 필지 경계의 미세한 굴곡은 사라지지만 육안상 차이는 거의 없다.
        try:
            error_margin = ee.ErrorMargin(10, "meters")
            simplified_fc = vworld_fc.map(
                lambda f: f.simplify(error_margin)
            )
            clipped_index = calculated_index.clip(simplified_fc)
        except Exception as e:
            logger.warning("simplify 실패, 원본 폴리곤 사용 | error=%s", e)
            clipped_index = calculated_index.clip(vworld_fc)
        logger.info("Vworld 폴리곤 clip 적용 | mode=%s", request.mode_key)
    else:
        # fallback: bbox clip + ESA WorldCover OR Dynamic World 격자 마스킹
        clipped_index = calculated_index.clip(geo_region)
        lc_classes = cfg.get("landcover_mask")
        dw_classes = cfg.get("dw_mask")

        if lc_classes or dw_classes:
            esa_mask = None
            if lc_classes:
                worldcover = (
                    ee.ImageCollection("ESA/WorldCover/v200")
                    .filterBounds(geo_region)
                    .first()
                    .select("Map")
                )
                esa_mask = worldcover.eq(lc_classes[0])
                for cls in lc_classes[1:]:
                    esa_mask = esa_mask.Or(worldcover.eq(cls))

            dw_mask = None
            if dw_classes:
                dw_image = (
                    ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
                    .filterBounds(geo_region)
                    .filterDate(s_date, e_date)
                    .select("label")
                    .mode()
                )
                dw_mask = dw_image.eq(dw_classes[0])
                for cls in dw_classes[1:]:
                    dw_mask = dw_mask.Or(dw_image.eq(cls))

            if esa_mask is not None and dw_mask is not None:
                combined_mask = esa_mask.Or(dw_mask)
            elif esa_mask is not None:
                combined_mask = esa_mask
            else:
                combined_mask = dw_mask

            clipped_index = clipped_index.updateMask(combined_mask)

    vis_params = {"min": cfg["min"], "max": cfg["max"], "palette": cfg["palette"]}
    tile_url = get_ee_tile_url(clipped_index, vis_params)
    logger.info("[TIMING] 마스킹+타일URL %.2fs", _time.time() - _t)
    logger.info("[TIMING] === 전체 소요시간 %.2fs ===", _time.time() - _t_start)

    logger.info("Analysis completed | mode=%s count=%d", request.mode_key, count)

    return AnalysisResult(
        request=request,
        current=current,
        last_year=last_year,
        time_series=time_series,
        cross_results=cross_results,
        tile_url=tile_url,
    )


def compute_cross_diagnosis(
    mode_key: str,
    bbox: tuple[float, float, float, float],
    start_date: date,
    end_date: date,
    cloud: int,
    current_mean: float,
) -> list[CrossDiagnosisResult]:
    """
    교차 진단을 지연 계산한다 (기본 분석에는 포함되지 않음).

    app.py에서 "교차 진단 보기" 같은 액션을 트리거할 때 호출한다.
    내부적으로 get_cached_stats가 @st.cache_data이므로,
    같은 bbox/기간으로 이미 호출된 적 있으면 즉시 반환된다.
    """
    pairs = find_cross_pairs_for_mode(mode_key)
    cfg_current = mode_config[mode_key]
    results: list[CrossDiagnosisResult] = []

    for _current_key, partner_key, label, interpret_fn in pairs:
        partner_cfg = mode_config[partner_key]

        try:
            p_count, p_raw = get_cached_stats(
                bbox, str(start_date), str(end_date), cloud, partner_cfg
            )
            partner_stats = SatelliteStatistics.extract_from_gee_dict(
                p_raw, partner_cfg["index_name"], p_count
            )

            if not partner_stats.has_data or partner_stats.mean is None:
                results.append(CrossDiagnosisResult(
                    label=label,
                    partner_mode_key=partner_key,
                    available=False,
                ))
                continue

            title, desc = interpret_fn(
                current_mean, cfg_current,
                partner_stats.mean, partner_cfg,
            )
            results.append(CrossDiagnosisResult(
                label=label,
                partner_mode_key=partner_key,
                available=True,
                title=title,
                description=desc,
                partner_mean=partner_stats.mean,
            ))
        except Exception:
            logger.exception("교차 진단 실패 | partner=%s", partner_key)
            results.append(CrossDiagnosisResult(
                label=label,
                partner_mode_key=partner_key,
                available=False,
            ))

    return results
