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
    return collection


def _build_index_image(
    region: ee.Geometry,
    start_date: str,
    end_date: str,
    cloud_threshold: int,
    mode_cfg: dict,
) -> tuple[ee.ImageCollection, ee.Image, ee.Image]:
    """공통 합성 이미지 및 지수 빌더 (lazy — 네트워크 미발생)."""
    collection = _filtered_collection(region, start_date, end_date, cloud_threshold, mode_cfg)
    image = collection.median()
    calculated_index = _compute_index_from_image(image, mode_cfg)
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
    lat: float,
    lon: float,
    buffer_m: int,
    start_date: str,
    end_date: str,
    cloud_threshold: int,
    mode_cfg: dict,
) -> tuple[int, dict | None]:
    """
    지역/기간/모드가 동일하면 GEE 재호출 없이 캐시된 통계를 반환한다.

    반환: (이미지 개수, GEE reduceRegion raw dict 또는 None)
    raw dict 파싱은 models.SatelliteStatistics.extract_from_gee_dict()가 담당한다.

    [수정] scale을 mode_cfg['native_resolution_m']에서 가져온다.
    기존 scale=10 고정은 S5P(5500m), Landsat(30m)에 부정확한 해상도였다.
    """
    region = ee.Geometry.Point([lon, lat]).buffer(buffer_m)
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
            ),
            context=f"reduceRegion scale={scale}",
        )
        return count, (raw_stats if isinstance(raw_stats, dict) else {})
    except (GEEQuotaError, GEEAuthenticationError, GEETimeoutError):
        raise  # 호출부(analysis_service)에서 처리
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
    lat: float,
    lon: float,
    buffer_m: int,
    start_date: str,
    end_date: str,
    cloud_threshold: int,
    mode_cfg: dict,
) -> list[tuple[str, float]]:
    """
    선택 기간 내 개별 위성 촬영분마다 (날짜, 평균값)을 계산해 반환한다.

    ImageCollection.map()으로 서버에서 집계한 뒤 getInfo()를 한 번만 호출한다.
    (이미지 개수만큼 왕복하지 않음 — GEE 비용 절약)

    [수정] scale 동적화, 예외를 빈 리스트 대신 로깅 후 전파.
    """
    region = ee.Geometry.Point([lon, lat]).buffer(buffer_m)
    collection = _filtered_collection(region, start_date, end_date, cloud_threshold, mode_cfg)
    index_name: str = mode_cfg["index_name"]
    scale: int = mode_cfg.get("native_resolution_m", 10)

    def _reduce_single(image: ee.Image) -> ee.Feature:
        idx_img = _compute_index_from_image(image, mode_cfg)
        mean_val = idx_img.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=region,
            scale=scale,
        ).get(index_name)
        return ee.Feature(None, {
            "date": image.date().format("YYYY-MM-dd"),
            "value": mean_val,
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
