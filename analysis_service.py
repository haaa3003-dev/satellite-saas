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

    # 토지피복 이중 마스킹 — ESA WorldCover OR Dynamic World
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

        calculated_index = calculated_index.updateMask(combined_mask)

    vis_params = {"min": cfg["min"], "max": cfg["max"], "palette": cfg["palette"]}
    tile_url = get_ee_tile_url(calculated_index.clip(geo_region), vis_params)

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
