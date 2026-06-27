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


# ─────────────────────────────────────────────
# [신규] 변화 탐지 — 두 기간 차이 이미지
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def get_change_detection_tile_url(
    lat: float,
    lon: float,
    buffer_m: int,
    before_start: str,
    before_end: str,
    after_start: str,
    after_end: str,
    cloud_threshold: int,
    mode_cfg: dict,
) -> tuple[str | None, float | None, float | None]:
    """
    두 기간(before / after)의 지수 차이 이미지를 계산해 타일 URL을 반환한다.

    반환: (타일URL, before평균, after평균)
    차이 이미지 = after - before
      양수(+) → 지수 증가 (NDVI라면 식생 회복, NDBI라면 개발 진행)
      음수(-) → 지수 감소 (NDVI라면 식생 소실, NDBI라면 녹지 회복)
    """
    region = ee.Geometry.Point([lon, lat]).buffer(buffer_m)
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
        before_mean = before_stats.get(f"{idx}_mean") if before_stats else None
        after_mean = after_stats.get(f"{idx}_mean") if after_stats else None

        # reduceRegion 단일 reducer는 키가 index_name 그대로 반환됨
        if before_mean is None and before_stats:
            before_mean = before_stats.get(idx)
        if after_mean is None and after_stats:
            after_mean = after_stats.get(idx)

        return tile_url, (
            float(before_mean) if isinstance(before_mean, (int, float)) else None
        ), (
            float(after_mean) if isinstance(after_mean, (int, float)) else None
        )

    except Exception:
        logger.exception("변화 탐지 실패 | mode=%s", mode_cfg.get("index_name"))
        return None, None, None


# ─────────────────────────────────────────────
# [신규] 계절별 트렌드 — 월 단위 평균 시계열
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def get_seasonal_trend(
    lat: float,
    lon: float,
    buffer_m: int,
    year: int,
    cloud_threshold: int,
    mode_cfg: dict,
) -> list[tuple[str, float]]:
    """
    지정 연도의 월별 평균값을 계산해 반환한다.

    반환: [("2024-01", 0.35), ("2024-02", 0.41), ...]
    데이터 없는 달은 결과에서 제외된다.

    기존 get_time_series()는 짧은 기간 내 개별 촬영분을 반환하지만
    이 함수는 월 단위로 묶어서 계절 패턴을 보여준다.
    """
    region = ee.Geometry.Point([lon, lat]).buffer(buffer_m)
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
    lat: float,
    lon: float,
    buffer_m: int,
    start_date: str,
    end_date: str,
    cloud_threshold: int,
    mode_cfg: dict,
    n_points: int = 5,
) -> dict[str, list[tuple[float, float, float]]]:
    """
    구역 내에서 지수가 가장 높은/낮은 픽셀 위치를 반환한다.

    반환:
      {
        "high": [(lat, lon, value), ...],  # 상위 n_points개
        "low":  [(lat, lon, value), ...],  # 하위 n_points개
      }

    higher_is_worse 방향에 따라 "주의 지점"과 "양호 지점"으로 해석이 달라진다.
    GEE sample()을 사용해 픽셀 단위 좌표를 추출한다.
    """
    region = ee.Geometry.Point([lon, lat]).buffer(buffer_m)
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
    buffer_m: int,
    start_date: str,
    end_date: str,
    cloud_threshold: int,
    mode_cfg: dict,
) -> list[dict]:
    """
    여러 지점에 대해 같은 기간·모드로 통계를 한꺼번에 계산한다.

    points는 tuple로 받는다 — st.cache_data가 list를 해시하지 못하기 때문.
    반환: [
        {
          "name": "지점명",
          "lat": float, "lon": float,
          "mean": float | None,
          "min_val": float | None,
          "max_val": float | None,
          "std_dev": float | None,
          "count": int,
          "tile_url": str | None,
        },
        ...
    ]
    """
    results = []
    scale = mode_cfg.get("native_resolution_m", 10)
    index_name = mode_cfg["index_name"]
    vis_params = {
        "min": mode_cfg["min"],
        "max": mode_cfg["max"],
        "palette": mode_cfg["palette"],
    }

    for lat, lon, name in points:
        region = ee.Geometry.Point([lon, lat]).buffer(buffer_m)
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
