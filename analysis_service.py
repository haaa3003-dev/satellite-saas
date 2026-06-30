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


# ── Vworld 시군구 역지오코딩 캐시 ─────────────────────────────────────────────
_sigungu_cache: dict[tuple[float, float], str] = {}


def _reverse_geocode_sigungu(cx: float, cy: float) -> str:
    """
    bbox 중심 좌표 → Nominatim 역지오코딩 → 시군구명 추출.
    같은 좌표 반복 호출을 막기 위해 모듈 레벨 캐시 사용.
    """
    cache_key = (round(cx, 3), round(cy, 3))
    if cache_key in _sigungu_cache:
        return _sigungu_cache[cache_key]

    import requests as _req
    try:
        r = _req.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": cy, "lon": cx, "format": "json", "accept-language": "ko"},
            headers={"User-Agent": "ksat-open-explorer/1.0"},
            timeout=5,
        )
        addr = r.json().get("address", {})
        sigungu = (
            addr.get("city_district") or
            addr.get("borough") or
            addr.get("county") or
            addr.get("city") or ""
        )
        _sigungu_cache[cache_key] = sigungu
        return sigungu
    except Exception as e:
        logger.warning("역지오코딩 실패 | error=%s", e)
        return ""


def _get_vworld_feature_collection(
    bbox: tuple[float, float, float, float],
    index_name: str,
    api_key: str,
):
    """
    분석 모드에 맞는 Vworld 폴리곤을 가져와 ee.FeatureCollection으로 반환한다.

    전략 분기:
    - 행정구역 모드(LST, NDBI): bbox 중심 → 시군구명 → attrFilter 단일 검색
    - 농경지 모드(NDVI, NDRE): bbox 중심 → 시군구명 + 지목 → CQL_FILTER 검색
      (연속지적도는 전국 단위 단일검색이 안 되므로 행정구역명으로 필지를 좁힌다)
    - 수계·산림 모드(NDWI, NBR, SAR_VV, SAR_VH): bbox + CQL_FILTER 직접 검색
    - NO2: 마스킹 없음

    반환: ee.FeatureCollection 또는 None (실패 시 호출부에서 격자 마스킹 fallback)
    """
    from vworld import VWORLD_LAYER_MAP, DATA_API_LAYERS, _data_api_request, get_vworld_boundary

    layer_cfg = VWORLD_LAYER_MAP.get(index_name)
    if layer_cfg is None:
        return None

    layer, base_cql_filter = layer_cfg
    west, south, east, north = bbox
    cx = round((west + east) / 2, 4)
    cy = round((south + north) / 2, 4)

    # ── 전략 1: 행정구역 단일 레이어 (LST, NDBI) ────────────────────────────
    if layer in DATA_API_LAYERS and base_cql_filter is None:
        sigungu = _reverse_geocode_sigungu(cx, cy)
        if not sigungu:
            logger.warning("시군구명 조회 실패 | bbox=%s", bbox)
            return None

        attr_filter = f"sig_kor_nm:=:{sigungu}"
        geojson = _data_api_request(api_key, layer, attr_filter=attr_filter, max_features=1)
        if geojson and geojson.get("features"):
            logger.info("Vworld 행정구역 OK | %s features=%d", sigungu, len(geojson["features"]))
            return ee.FeatureCollection(geojson)
        logger.warning("Vworld 행정구역 없음 | sigungu=%s", sigungu)
        return None

    # ── 전략 2: 농경지 필지 (NDVI, NDRE) — 행정구역명 + 지목으로 필지 검색 ──
    if layer == "lt_c_landinfobasemap" and base_cql_filter is not None:
        sigungu = _reverse_geocode_sigungu(cx, cy)
        if not sigungu:
            logger.warning("시군구명 조회 실패 | bbox=%s", bbox)
            return None

        # 시군구명 + 지목 필터를 동시에 건 CQL_FILTER
        combined_filter = f"sigg_nm='{sigungu}' AND {base_cql_filter}"
        from vworld import _wfs_api_request
        geojson = _wfs_api_request(
            api_key, layer,
            cql_filter=combined_filter,
            max_features=2000,
        )
        if geojson and geojson.get("features"):
            feat_count = len(geojson["features"])
            logger.info("Vworld 농경지 필지 OK | %s features=%d", sigungu, feat_count)
            return ee.FeatureCollection(geojson)
        logger.warning("Vworld 농경지 필지 없음 | sigungu=%s", sigungu)
        return None

    # ── 전략 3: 수계·산림 (NDWI, NBR, SAR_VV, SAR_VH) — bbox + CQL 직접 검색 ─
    geojson = get_vworld_boundary(bbox, index_name, api_key)
    if geojson and geojson.get("features"):
        logger.info("Vworld OK | index=%s features=%d", index_name, len(geojson["features"]))
        return ee.FeatureCollection(geojson)

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
    count, raw_stats = get_cached_stats(bbox, s_date, e_date, cloud, cfg)
    current = SatelliteStatistics.extract_from_gee_dict(raw_stats, cfg["index_name"], count)

    if not current.has_data:
        logger.info("No satellite data | mode=%s period=%s~%s", request.mode_key, s_date, e_date)
        raise GEENoDataError(
            "지정한 기간과 지역에 유효한 위성 영상이 없습니다. "
            "구름 허용률을 높이거나 기간을 넓혀보세요."
        )

    # ── 2. 전년 동기 통계 ─────────────────────────────────────────────
    ly_start = request.start_date.replace(year=request.start_date.year - 1)
    ly_end = request.end_date.replace(year=request.end_date.year - 1)
    last_count, last_raw = get_cached_stats(bbox, str(ly_start), str(ly_end), cloud, cfg)
    last_year = SatelliteStatistics.extract_from_gee_dict(
        last_raw, cfg["index_name"], last_count
    )

    # ── 3. 시계열 ─────────────────────────────────────────────────────
    time_series = get_time_series(bbox, s_date, e_date, cloud, cfg)

    # ── 4. 교차 진단 ──────────────────────────────────────────────────
    cross_results = _run_cross_diagnosis(request, bbox, cloud, current)

    # ── 5. 타일 URL ───────────────────────────────────────────────────
    west, south, east, north = bbox
    geo_region = ee.Geometry.BBox(west, south, east, north)
    _, calculated_index = get_satellite_index_for_period(
        geo_region, s_date, e_date, cloud, cfg
    )

    # ── 토지피복 마스킹 (Vworld 폴리곤 우선 → ESA+DW 격자 fallback) ──────────
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

    logger.info("Analysis completed | mode=%s count=%d", request.mode_key, count)

    return AnalysisResult(
        request=request,
        current=current,
        last_year=last_year,
        time_series=time_series,
        cross_results=cross_results,
        tile_url=tile_url,
    )


def _run_cross_diagnosis(
    request: AnalysisRequest,
    bbox: tuple[float, float, float, float],
    cloud: int,
    current: SatelliteStatistics,
) -> list[CrossDiagnosisResult]:
    """교차 진단 쌍을 찾아 짝 지수 통계를 조회하고 해석 결과를 반환한다."""
    pairs = find_cross_pairs_for_mode(request.mode_key)
    results: list[CrossDiagnosisResult] = []

    for _current_key, partner_key, label, interpret_fn in pairs:
        partner_cfg = mode_config[partner_key]
        s_date, e_date = str(request.start_date), str(request.end_date)

        try:
            p_count, p_raw = get_cached_stats(bbox, s_date, e_date, cloud, partner_cfg)
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

            cfg_current = mode_config[request.mode_key]
            title, desc = interpret_fn(
                current.mean, cfg_current,
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
