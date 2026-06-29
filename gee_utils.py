# gee_utils.py
"""
Google Earth Engine 호출 전담 모듈.

변경 사항:
1. scale=10 하드코딩 제거 → mode_cfg['native_resolution_m'] 사용.
   S5P(5500m), Landsat(30m)에 잘못된 해상도가 적용되던 버그 수정.
2. except Exception 일괄 처리 → GEE 오류 유형별 분기 + logging.
3. print(traceback...) 제거 → logging.exception() 으로 교체.
   Streamlit Cloud에서도 stdout 대신 로그 스트림으로 수집된다.
4. init_gee: GEE_PROJECT_ID를 mode_config 상수에서 가져온다.
"""
from __future__ import annotations

import logging

import ee
import streamlit as st

from exceptions import GEEAuthenticationError, GEEQuotaError, GEETimeoutError
from mode_config import GEE_PROJECT_ID

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# GEE 초기화
# ─────────────────────────────────────────────

@st.cache_resource
def init_gee() -> bool:
    """GEE 인증 및 초기화. 성공 시 True, 실패 시 False."""
    try:
        has_cloud_secrets = "gee_credentials" in st.secrets
    except Exception:
        has_cloud_secrets = False

    if has_cloud_secrets:
        try:
            cred_info = st.secrets["gee_credentials"]
            credentials = ee.ServiceAccountCredentials(
                cred_info["client_email"],
                key_data=cred_info["private_key"],
            )
            ee.Initialize(credentials, project=GEE_PROJECT_ID)
            logger.info("GEE initialized via service account.")
            return True
        except Exception:
            logger.exception("GEE service account initialization failed.")
            return False
    else:
        try:
            ee.Initialize(project=GEE_PROJECT_ID)
            logger.info("GEE initialized via local credentials.")
            return True
        except Exception:
            try:
                ee.Authenticate()
                ee.Initialize(project=GEE_PROJECT_ID)
                logger.info("GEE initialized after interactive authentication.")
                return True
            except Exception:
                logger.exception("GEE interactive authentication failed.")
                return False


# ─────────────────────────────────────────────
# 내부 헬퍼 — ee 객체 조작 (lazy, 네트워크 미발생)
# ─────────────────────────────────────────────

def _compute_index_from_image(image: ee.Image, mode_cfg: dict) -> ee.Image:
    """
    단일 ee.Image에서 분석 지수를 계산하는 공통 로직.

    median 합성 이미지든 시계열 개별 이미지든 항상 이 함수를 거쳐
    합성 결과와 시계열 점 계산식이 일치하도록 보장한다.
    """
    calc_type: str = mode_cfg["calc_type"]
    index_name: str = mode_cfg["index_name"]

    if calc_type == "normalized_diff":
        return image.normalizedDifference(mode_cfg["bands"]).rename(index_name)
    if calc_type == "single_band":
        return image.select(mode_cfg["band"]).rename(index_name)
    if calc_type == "thermal_celsius":
        return (
            image.select(mode_cfg["band"])
            .multiply(0.00341802)
            .add(149.0)
            .subtract(273.15)
            .rename(index_name)
        )
    if calc_type == "sar_backscatter":
        # Sentinel-1 GRD 후방산란계수 처리
        # VV 편파 선택 → 선형값(파워)을 dB로 변환: 10 * log10(VV)
        # GEE S1 컬렉션은 이미 dB 단위이므로 select만 수행
        # mode_cfg['band']로 VV 또는 VH 선택
        band = mode_cfg.get("band", "VV")
        return image.select(band).rename(index_name)
    if calc_type == "precipitation_sum":
        # CHIRPS 일강수량(precipitation) 밴드를 그대로 선택.
        # 컬렉션을 sum()으로 합산한 뒤 이 함수가 호출되므로
        # image는 이미 기간 합계 이미지다.
        return image.select(mode_cfg["band"]).rename(index_name)
    raise ValueError(f"알 수 없는 calc_type: {calc_type}")


def _filtered_collection(
    region: ee.Geometry,
    start_date: str,
    end_date: str,
    cloud_threshold: int,
    mode_cfg: dict,
) -> ee.ImageCollection:
    """공통 컬렉션 필터링 (위치 / 기간 / 구름 기준)."""
    collection = (
        ee.ImageCollection(mode_cfg["collection"])
        .filterBounds(region)
        .filterDate(start_date, end_date)
    )
    cloud_prop = mode_cfg.get("cloud_filter_prop")
    if cloud_prop:
        collection = collection.filter(ee.Filter.lt(cloud_prop, cloud_threshold))

    # SAR 전용 필터 — Sentinel-1은 구름 필터 대신 편파/궤도 방향 필터 적용
    # instrumentMode: IW (Interferometric Wide — 한국 대부분 지역 커버)
    # transmitterReceiverPolarisation: VV+VH 듀얼 편파
    # orbitProperties_pass: DESCENDING (한국 기준 야간 촬영, 더 안정적)
    if mode_cfg.get("calc_type") == "sar_backscatter":
        collection = (
            collection
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains(
                "transmitterReceiverPolarisation",
                mode_cfg.get("band", "VV"),
            ))
            .filter(ee.Filter.eq(
                "orbitProperties_pass",
                mode_cfg.get("orbit_pass", "DESCENDING"),
            ))
        )
    return collection


def _build_index_image(
    region: ee.Geometry,
    start_date: str,
    end_date: str,
    cloud_threshold: int,
    mode_cfg: dict,
) -> tuple[ee.ImageCollection, ee.Image, ee.Image]:
    """공통 합성 이미지 및 지수 빌더 (lazy — 네트워크 미발생).

    landcover_mask가 정의된 모드는 ESA WorldCover 10m 기준으로
    해당 토지 유형의 픽셀만 남기고 나머지를 마스킹한다.
    → 산림 모드면 산림 픽셀만, 수체 모드면 강·호수 픽셀만 분석됨.
    """
    collection = _filtered_collection(region, start_date, end_date, cloud_threshold, mode_cfg)
    image = collection.median()
    calculated_index = _compute_index_from_image(image, mode_cfg)

    # ── 토지피복 이중 마스킹 (ESA WorldCover OR Dynamic World) ───────────────
    # ESA WorldCover v200: 연간 업데이트(2020/2021), 안정적
    # Dynamic World v1: 매주 업데이트, 최신이지만 노이즈 있음
    # 둘을 OR로 합치면 서로의 단점을 보완 → 더 정밀한 경계
    #
    # ESA WorldCover 클래스:
    #   10=수목  20=관목  30=초지  40=경작지  50=도시  80=수체
    # Dynamic World 클래스:
    #   0=수체  1=수목  2=초지  4=농경지  6=건물
    lc_classes = mode_cfg.get("landcover_mask")
    dw_classes = mode_cfg.get("dw_mask")

    if lc_classes or dw_classes:
        # ESA WorldCover 마스크
        esa_mask = None
        if lc_classes:
            worldcover = (
                ee.ImageCollection("ESA/WorldCover/v200")
                .filterBounds(region)
                .first()
                .select("Map")
            )
            esa_mask = worldcover.eq(lc_classes[0])
            for cls in lc_classes[1:]:
                esa_mask = esa_mask.Or(worldcover.eq(cls))

        # Dynamic World 마스크 (해당 기간 최빈값 사용)
        dw_mask = None
        if dw_classes:
            dw_collection = (
                ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
                .filterBounds(region)
                .filterDate(start_date, end_date)
                .select("label")
            )
            # 기간 내 최빈값(mode)으로 집계 → 노이즈 감소
            dw_image = dw_collection.mode()
            dw_mask = dw_image.eq(dw_classes[0])
            for cls in dw_classes[1:]:
                dw_mask = dw_mask.Or(dw_image.eq(cls))

        # 두 마스크 OR 합성
        if esa_mask is not None and dw_mask is not None:
            combined_mask = esa_mask.Or(dw_mask)
        elif esa_mask is not None:
            combined_mask = esa_mask
        else:
            combined_mask = dw_mask

        calculated_index = calculated_index.updateMask(combined_mask)

    return collection, image, calculated_index


# ─────────────────────────────────────────────
# GEE getInfo() 안전 래퍼 — 예외 유형별 분기
# ─────────────────────────────────────────────

def _safe_get_info(ee_object: object, context: str = "") -> object:
    """
    .getInfo() 호출을 래핑해 GEE 오류를 유형별 커스텀 예외로 변환한다.

    기존: except Exception: return []  → 오류 유형 구분 불가
    개선: GEEQuotaError / GEEAuthenticationError / GEETimeoutError 분기
    """
    try:
        return ee_object.getInfo()
    except ee.EEException as exc:
        msg = str(exc).lower()
        logger.exception("GEE EEException | context=%s", context)
        if "quota" in msg or "rate limit" in msg or "too many requests" in msg:
            raise GEEQuotaError(f"GEE 쿼터 초과 [{context}]") from exc
        if "permission" in msg or "not found" in msg or "invalid" in msg:
            raise GEEAuthenticationError(f"GEE 권한/인증 오류 [{context}]") from exc
        raise  # 기타 GEE 오류는 원본 그대로 전파
    except TimeoutError as exc:
        logger.exception("GEE timeout | context=%s", context)
        raise GEETimeoutError(f"GEE 응답 시간 초과 [{context}]") from exc


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def get_cached_stats(
    bbox: tuple[float, float, float, float],  # (west, south, east, north)
    start_date: str,
    end_date: str,
    cloud_threshold: int,
    mode_cfg: dict,
) -> tuple[int, dict | None]:
    """
    바운딩 박스/기간/모드가 동일하면 GEE 재호출 없이 캐시된 통계를 반환한다.

    bbox: (서쪽경도, 남쪽위도, 동쪽경도, 북쪽위도) — WGS84 decimal degrees
    반환: (이미지 개수, GEE reduceRegion raw dict 또는 None)
    """
    west, south, east, north = bbox
    region = ee.Geometry.BBox(west, south, east, north)
    collection, _, calculated_index = _build_index_image(
        region, start_date, end_date, cloud_threshold, mode_cfg
    )

    count_result = _safe_get_info(collection.size(), context="collection.size")
    count = int(count_result) if count_result is not None else 0
    if count == 0:
        return 0, None

    scale = mode_cfg.get("native_resolution_m", 10)

    try:
        combined_reducer = (
            ee.Reducer.mean()
            .combine(ee.Reducer.minMax(), sharedInputs=True)
            .combine(ee.Reducer.stdDev(), sharedInputs=True)
        )
        raw_stats = _safe_get_info(
            calculated_index.reduceRegion(
                reducer=combined_reducer,
                geometry=region,
                scale=scale,
                maxPixels=1e9,
            ),
            context=f"reduceRegion scale={scale}",
        )
        return count, (raw_stats if isinstance(raw_stats, dict) else {})
    except (GEEQuotaError, GEEAuthenticationError, GEETimeoutError):
        raise
    except Exception:
        logger.exception("reduceRegion 실패 | mode=%s", mode_cfg.get("index_name"))
        return count, {}


def get_satellite_index_for_period(
    region: ee.Geometry,
    start_date: str,
    end_date: str,
    cloud_threshold: int,
    mode_cfg: dict,
) -> tuple[ee.Image, ee.Image]:
    """타일 렌더링용 ee 이미지/지수 객체 반환 (캐싱 불가, 매번 새로 빌드)."""
    _, image, calculated_index = _build_index_image(
        region, start_date, end_date, cloud_threshold, mode_cfg
    )
    return image, calculated_index


@st.cache_data(ttl=3600, show_spinner=False)
def get_time_series(
    bbox: tuple[float, float, float, float],  # (west, south, east, north)
    start_date: str,
    end_date: str,
    cloud_threshold: int,
    mode_cfg: dict,
) -> list[tuple[str, float]]:
    """
    선택 기간 내 개별 위성 촬영분마다 (날짜, 평균값)을 계산해 반환한다.
    bbox: (서쪽경도, 남쪽위도, 동쪽경도, 북쪽위도)
    """
    west, south, east, north = bbox
    region = ee.Geometry.BBox(west, south, east, north)
    collection = _filtered_collection(region, start_date, end_date, cloud_threshold, mode_cfg)
    index_name: str = mode_cfg["index_name"]
    scale: int = mode_cfg.get("native_resolution_m", 10)
    is_precip = mode_cfg.get("calc_type") == "precipitation_sum"

    def _reduce_single(image: ee.Image) -> ee.Feature:
        idx_img = _compute_index_from_image(image, mode_cfg)
        # 강수량은 일별 합계, 나머지는 평균
        reducer = ee.Reducer.sum() if is_precip else ee.Reducer.mean()
        val = idx_img.reduceRegion(
            reducer=reducer,
            geometry=region,
            scale=scale,
        ).get(index_name)
        return ee.Feature(None, {
            "date": image.date().format("YYYY-MM-dd"),
            "value": val,
        })

    try:
        raw = _safe_get_info(
            collection.map(_reduce_single),
            context=f"time_series {index_name}",
        )
        features = raw.get("features", []) if isinstance(raw, dict) else []
    except (GEEQuotaError, GEEAuthenticationError, GEETimeoutError):
        raise
    except Exception:
        logger.exception("시계열 조회 실패 | mode=%s", index_name)
        return []

    raw_series: list[tuple[str, float]] = []
    for f in features:
        props = f.get("properties", {})
        val = props.get("value")
        date_str = props.get("date")
        if val is not None and date_str is not None and isinstance(val, (int, float)):
            raw_series.append((date_str, val))

    # 같은 날짜 중복 촬영분(인접 궤도 등) → 날짜별 평균으로 합산
    by_date: dict[str, list[float]] = {}
    for date_str, val in raw_series:
        by_date.setdefault(date_str, []).append(val)

    merged = [
        (d, sum(vals) / len(vals))
        for d, vals in by_date.items()
    ]
    merged.sort(key=lambda x: x[0])
    return merged


def get_ee_tile_url(ee_image_object: ee.Image, vis_params: dict) -> str:
    """GEE 지도 레이어 타일 URL 반환."""
    map_id_dict = ee.Image(ee_image_object).getMapId(vis_params)
    return map_id_dict["tile_fetcher"].url_format


# ─────────────────────────────────────────────
# [신규] 변화 탐지 — 두 기간 차이 이미지
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def get_change_detection_tile_url(
    bbox: tuple[float, float, float, float],  # (west, south, east, north)
    before_start: str,
    before_end: str,
    after_start: str,
    after_end: str,
    cloud_threshold: int,
    mode_cfg: dict,
) -> tuple[str | None, float | None, float | None]:
    """두 기간(before/after) 지수 차이 이미지를 계산해 타일 URL 반환."""
    west, south, east, north = bbox
    region = ee.Geometry.BBox(west, south, east, north)
    scale = mode_cfg.get("native_resolution_m", 10)

    try:
        # before 이미지
        _, _, before_index = _build_index_image(
            region, before_start, before_end, cloud_threshold, mode_cfg
        )
        # after 이미지
        _, _, after_index = _build_index_image(
            region, after_start, after_end, cloud_threshold, mode_cfg
        )

        # 차이 이미지 (after - before)
        diff_image = after_index.subtract(before_index).rename("diff")

        # 차이 시각화 — 빨강(감소) ~ 흰색(변화없음) ~ 파랑(증가)
        diff_vis = {
            "min": -0.3, "max": 0.3,
            "palette": ["#d73027", "#f46d43", "#fdae61", "#ffffff",
                        "#74add1", "#4575b4", "#313695"],
        }
        tile_url = get_ee_tile_url(diff_image.clip(region), diff_vis)

        # before/after 평균값 (수치 비교용)
        # 단일 Reducer.mean() → 키가 "{index_name}" 그대로 반환됨
        reducer = ee.Reducer.mean()
        before_stats = _safe_get_info(
            before_index.reduceRegion(reducer=reducer, geometry=region, scale=scale),
            context="change_before_mean",
        )
        after_stats = _safe_get_info(
            after_index.reduceRegion(reducer=reducer, geometry=region, scale=scale),
            context="change_after_mean",
        )

        idx = mode_cfg["index_name"]
        logger.info("변화탐지 before_stats=%s after_stats=%s", before_stats, after_stats)

        # 단일 reducer: 키가 index_name 그대로
        before_mean = before_stats.get(idx) if isinstance(before_stats, dict) else None
        after_mean  = after_stats.get(idx)  if isinstance(after_stats,  dict) else None

        return tile_url, (
            float(before_mean) if isinstance(before_mean, (int, float)) else None
        ), (
            float(after_mean) if isinstance(after_mean, (int, float)) else None
        )

    except Exception as exc:
        logger.exception("변화 탐지 실패 | mode=%s", mode_cfg.get("index_name"))
        return None, None, str(exc)


# ─────────────────────────────────────────────
# [신규] 계절별 트렌드 — 월 단위 평균 시계열
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def get_seasonal_trend(
    bbox: tuple[float, float, float, float],
    year: int,
    cloud_threshold: int,
    mode_cfg: dict,
) -> list[tuple[str, float]]:
    """지정 연도의 월별 평균값을 계산해 반환한다."""
    west, south, east, north = bbox
    region = ee.Geometry.BBox(west, south, east, north)
    scale = mode_cfg.get("native_resolution_m", 10)
    index_name = mode_cfg["index_name"]
    results: list[tuple[str, float]] = []

    for month in range(1, 13):
        start = f"{year}-{month:02d}-01"
        # 각 월의 마지막 날 계산
        if month == 12:
            end = f"{year}-12-31"
        else:
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            end = f"{year}-{month:02d}-{last_day}"

        try:
            collection = _filtered_collection(
                region, start, end, cloud_threshold, mode_cfg
            )
            count_raw = _safe_get_info(collection.size(), context=f"seasonal_{month}")
            count = int(count_raw) if count_raw is not None else 0
            if count == 0:
                continue

            monthly_index = _compute_index_from_image(collection.median(), mode_cfg)
            stats = _safe_get_info(
                monthly_index.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=region,
                    scale=scale,
                ),
                context=f"seasonal_mean_{month}",
            )
            val = stats.get(index_name) if stats else None
            if val is not None and isinstance(val, (int, float)):
                results.append((f"{year}-{month:02d}", float(val)))

        except Exception:
            logger.warning("계절 트렌드 월 계산 실패 | year=%d month=%d", year, month)
            continue

    return results


# ─────────────────────────────────────────────
# [신규] 핫스팟 — 구역 내 상위/하위 픽셀 위치
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def get_hotspots(
    bbox: tuple[float, float, float, float],
    start_date: str,
    end_date: str,
    cloud_threshold: int,
    mode_cfg: dict,
    n_points: int = 5,
) -> dict[str, list[tuple[float, float, float]]]:
    """구역 내에서 지수가 가장 높은/낮은 픽셀 위치를 반환한다."""
    west, south, east, north = bbox
    region = ee.Geometry.BBox(west, south, east, north)
    scale = mode_cfg.get("native_resolution_m", 10)
    index_name = mode_cfg["index_name"]

    try:
        _, _, calculated_index = _build_index_image(
            region, start_date, end_date, cloud_threshold, mode_cfg
        )
        clipped = calculated_index.clip(region)

        # GEE sample()로 픽셀 좌표 + 값 추출 (최대 500개 샘플)
        samples = clipped.sample(
            region=region,
            scale=scale,
            numPixels=500,
            geometries=True,  # 좌표 포함
        )
        raw = _safe_get_info(samples, context="hotspot_sample")
        features = raw.get("features", []) if isinstance(raw, dict) else []

        points: list[tuple[float, float, float]] = []
        for f in features:
            props = f.get("properties", {})
            geom = f.get("geometry", {})
            val = props.get(index_name)
            coords = geom.get("coordinates", [])
            if (val is not None
                    and isinstance(val, (int, float))
                    and len(coords) == 2):
                points.append((coords[1], coords[0], float(val)))  # lat, lon, val

        if not points:
            return {"high": [], "low": []}

        points.sort(key=lambda x: x[2])
        return {
            "low": points[:n_points],            # 값이 낮은 지점
            "high": points[-n_points:][::-1],    # 값이 높은 지점
        }

    except Exception:
        logger.exception("핫스팟 계산 실패 | mode=%s", mode_cfg.get("index_name"))
        return {"high": [], "low": []}


# ─────────────────────────────────────────────
# [신규] 다중 지점 동시 비교
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def get_multi_point_stats(
    points: tuple[tuple[float, float, str], ...],  # ((lat, lon, name), ...)
    start_date: str,
    end_date: str,
    cloud_threshold: int,
    mode_cfg: dict,
) -> list[dict]:
    """여러 지점에 대해 같은 기간·모드로 통계를 한꺼번에 계산한다."""
    results = []
    scale = mode_cfg.get("native_resolution_m", 10)
    index_name = mode_cfg["index_name"]
    vis_params = {
        "min": mode_cfg["min"],
        "max": mode_cfg["max"],
        "palette": mode_cfg["palette"],
    }

    for lat, lon, name in points:
        # 각 지점 주변 0.05도(약 5km) bbox 생성
        delta = 0.025
        region = ee.Geometry.BBox(lon - delta, lat - delta, lon + delta, lat + delta)
        entry: dict = {
            "name": name,
            "lat": lat,
            "lon": lon,
            "mean": None,
            "min_val": None,
            "max_val": None,
            "std_dev": None,
            "count": 0,
            "tile_url": None,
        }

        try:
            collection, _, calculated_index = _build_index_image(
                region, start_date, end_date, cloud_threshold, mode_cfg
            )

            count_raw = _safe_get_info(collection.size(), context=f"multi_count_{name}")
            count = int(count_raw) if count_raw is not None else 0
            entry["count"] = count

            if count == 0:
                results.append(entry)
                continue

            # 통계 (mean / min / max / stdDev 한 번에)
            combined_reducer = (
                ee.Reducer.mean()
                .combine(ee.Reducer.minMax(), sharedInputs=True)
                .combine(ee.Reducer.stdDev(), sharedInputs=True)
            )
            raw_stats = _safe_get_info(
                calculated_index.reduceRegion(
                    reducer=combined_reducer,
                    geometry=region,
                    scale=scale,
                ),
                context=f"multi_stats_{name}",
            )

            if raw_stats and isinstance(raw_stats, dict):
                def _pick(suffix: str) -> float | None:
                    val = raw_stats.get(f"{index_name}_{suffix}")
                    return float(val) if isinstance(val, (int, float)) else None

                entry["mean"]    = _pick("mean")
                entry["min_val"] = _pick("min")
                entry["max_val"] = _pick("max")
                entry["std_dev"] = _pick("stdDev")

            # 타일 URL (지도 레이어용)
            try:
                entry["tile_url"] = get_ee_tile_url(
                    calculated_index.clip(region), vis_params
                )
            except Exception:
                logger.warning("타일 URL 생성 실패 | point=%s", name)

        except Exception:
            logger.exception("다중 지점 통계 실패 | point=%s", name)

        results.append(entry)

    return results
