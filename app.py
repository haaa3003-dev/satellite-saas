# app.py
"""
K-Sat 오픈 탐색기 — Streamlit 진입점.

이 파일의 역할:
- 페이지 설정, 사이드바, 검색 UI
- 버튼 클릭 시 analysis_service.run_analysis() 호출
- AnalysisResult를 세션에 저장하고 화면에 렌더링

이 파일이 하지 않는 것:
- GEE 직접 호출 (→ gee_utils.py)
- 통계 계산 (→ analysis_service.py)
- 교차 진단 로직 (→ analysis_service.py)
- 예외 분류 (→ exceptions.py)
- 보고서 데이터 조립 (→ report_builder.py)

변경 요약:
1. get_safe_value(), is_good_value() 인라인 정의 제거
   → models.SatelliteStatistics.extract_from_gee_dict()
   → analysis_service.is_good_value() 로 이동
2. change_rate 계산 인라인 제거 → AnalysisResult.change_rate 프로퍼티
3. reliability 문자열 인라인 제거 → AnalysisResult.reliability_label 프로퍼티
4. except Exception: print(traceback) → 유형별 오류 메시지 분기 + logging
5. buffer_m=3000 리터럴 → ANALYSIS_BUFFER_M 상수
6. GEE 인증 실패 메시지 개선
"""
from __future__ import annotations

import logging
import logging.config
from datetime import date, timedelta

import folium
import plotly.graph_objects as go
import streamlit as st
from streamlit_folium import st_folium

from analysis_service import is_good_value, run_analysis
from exceptions import (
    GEEAuthenticationError,
    GEENoDataError,
    GEEQuotaError,
    GEETimeoutError,
    NetworkError,
)
from geocoding import geocode_place
from gee_utils import (
    get_change_detection_tile_url,
    get_hotspots,
    get_seasonal_trend,
    init_gee,
)
from mode_config import ANALYSIS_BUFFER_M, mode_config, preset_coords
from models import AnalysisRequest, AnalysisResult, RegionInfo
from report_builder import generate_excel_report

# ─────────────────────────────────────────────
# 로깅 설정 (Streamlit Cloud 포함 어디서나 작동)
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 1. 페이지 설정
# ─────────────────────────────────────────────
st.set_page_config(page_title="K-Sat 오픈 탐색기", page_icon="🌍", layout="wide")

st.title("🌍 K-Sat 위성 데이터 오픈 탐색기")
st.caption("누구나 자유롭게 분석하고 활용하는 Sentinel-2 위성 기반 환경·재해 모니터링 플랫폼")
st.info(
    "💡 **[안내]** 본 플랫폼은 오픈 위성 데이터를 활용하여 누구나 무료로 특정 지역의 "
    "농업 생육, 수자원, 산림 상태를 관측할 수 있도록 지원합니다. "
    "아래 검색창에 궁금한 지역을 입력해 보세요!"
)
st.markdown("---")

# ─────────────────────────────────────────────
# GEE 초기화
# ─────────────────────────────────────────────
if not init_gee():
    st.error(
        "🚨 위성 데이터 서버(GEE) 인증에 실패했습니다. "
        "Streamlit Cloud라면 st.secrets의 gee_credentials를, "
        "로컬이라면 `earthengine authenticate`를 먼저 실행해주세요."
    )
    st.stop()

# ─────────────────────────────────────────────
# 2. 사이드바
# ─────────────────────────────────────────────
st.sidebar.header("🛠️ 탐색 설정")
analysis_mode: str = st.sidebar.selectbox(
    "🔎 관측 모드 선택",
    list(mode_config.keys()),
)
st.sidebar.markdown("---")
st.sidebar.caption("Powered by Google Earth Engine & K-Sat Team")

# ─────────────────────────────────────────────
# 3. 지역 검색 — 콜백 기반 상호 배타 전환
# ─────────────────────────────────────────────
st.subheader("📍 관측 지역 검색")


def _clear_preset() -> None:
    st.session_state.preset_select = "직접 검색"


def _clear_search() -> None:
    st.session_state.search_input = ""


# 세션 초기화
_first_preset_key = next(iter(preset_coords))
_first_coords = preset_coords[_first_preset_key]

if "search_input" not in st.session_state:
    st.session_state.search_input = ""
if "preset_select" not in st.session_state:
    st.session_state.preset_select = "직접 검색"
if "lat" not in st.session_state:
    st.session_state.lat = _first_coords[0]
    st.session_state.lon = _first_coords[1]
    st.session_state.region_name = _first_preset_key

col_s1, col_s2 = st.columns([2, 1])
with col_s1:
    st.text_input(
        "🔍 지명 또는 주소를 입력하세요",
        placeholder="예: 춘천시 소양강, 새만금, 지리산 (입력 후 Enter)",
        key="search_input",
        on_change=_clear_preset,
    )
with col_s2:
    st.selectbox(
        "📌 주요 관심 지역 빠르게 이동",
        ["직접 검색"] + list(preset_coords.keys()),
        key="preset_select",
        on_change=_clear_search,
    )

# 지역 좌표 업데이트
if st.session_state.search_input:
    try:
        results = geocode_place(st.session_state.search_input)
        if results:
            st.session_state.lat, st.session_state.lon, st.session_state.region_name = results[0]
            st.success(f"✅ '{st.session_state.region_name}'(으)로 좌표를 설정했습니다. 아래 버튼을 눌러주세요.")
        else:
            st.warning("⚠️ 검색 결과가 없습니다. 다른 검색어나 조금 더 넓은 지명을 입력해보세요.")
    except NetworkError as exc:
        st.warning(f"⚠️ 지명 검색 중 오류가 발생했습니다: {exc}")

elif st.session_state.preset_select != "직접 검색":
    preset = st.session_state.preset_select
    if preset in preset_coords:
        st.session_state.lat, st.session_state.lon = preset_coords[preset]
        st.session_state.region_name = preset

# ─────────────────────────────────────────────
# 4. 분석 조건 설정
# ─────────────────────────────────────────────
st.markdown("---")
col_d1, col_d2, col_d3, col_d4 = st.columns(4)
with col_d1:
    e_date: date = st.date_input("📅 관측 종료일 (최근)", date.today())
with col_d2:
    s_date: date = st.date_input("📅 관측 시작일", e_date - timedelta(days=30))
with col_d3:
    cloud_threshold: int = st.slider(
        "☁️ 구름 허용률 (%)", 0, 100, 20, 5,
        help="수치를 높이면 흐린 날의 사진도 포함하여 더 많은 데이터를 가져옵니다.",
    )
with col_d4:
    st.markdown("<br>", unsafe_allow_html=True)
    run_btn: bool = st.button("🚀 위성 데이터 불러오기", use_container_width=True, type="primary")

# ─────────────────────────────────────────────
# 5. 분석 실행 (버튼 클릭 시에만)
# ─────────────────────────────────────────────
if run_btn:
    # 입력 검증은 AnalysisRequest.__post_init__이 담당하지만
    # 사용자 안내는 UI 레이어에서 먼저 처리한다.
    if s_date >= e_date:
        st.warning("⚠️ 관측 시작일은 종료일보다 빨라야 합니다.")
    else:
        if "analysis_res" in st.session_state:
            del st.session_state["analysis_res"]

        with st.spinner("🛰️ 위성 데이터를 렌더링하고 있습니다. 잠시만 기다려주세요..."):
            try:
                request = AnalysisRequest(
                    region=RegionInfo(
                        lat=st.session_state.lat,
                        lon=st.session_state.lon,
                        name=st.session_state.region_name,
                        buffer_m=ANALYSIS_BUFFER_M,
                    ),
                    mode_key=analysis_mode,
                    start_date=s_date,
                    end_date=e_date,
                    cloud_threshold=cloud_threshold,
                )
                st.session_state.analysis_res = run_analysis(request)

            except GEENoDataError as exc:
                st.warning(f"⚠️ {exc}")

            except GEEQuotaError:
                st.error("🚨 GEE API 사용량이 한도에 도달했습니다. 잠시 후 다시 시도해주세요.")
                logger.warning("GEE quota exceeded during analysis.")

            except GEETimeoutError:
                st.error("🚨 위성 데이터 서버가 응답하지 않습니다. 잠시 후 다시 시도해주세요.")
                logger.warning("GEE timeout during analysis.")

            except GEEAuthenticationError:
                st.error("🚨 위성 데이터 서버 인증이 만료되었습니다. 관리자에게 문의해주세요.")
                logger.error("GEE authentication error during analysis.")

            except Exception:
                logger.exception("Unexpected error during analysis.")
                st.error("🚨 예기치 못한 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

# ─────────────────────────────────────────────
# 6. 결과 렌더링
# ─────────────────────────────────────────────
if "analysis_res" not in st.session_state:
    st.stop()

res: AnalysisResult = st.session_state.analysis_res
cfg: dict = mode_config[res.request.mode_key]
lat = res.request.region.lat
lon = res.request.region.lon
buffer_m = res.request.region.buffer_m
region_name = res.request.region.name
s_date = res.request.start_date
e_date = res.request.end_date
cloud = res.request.cloud_threshold
cur = res.current
good = cur.mean is not None and is_good_value(cur.mean, cfg)
avg_display = cur.mean if cur.mean is not None else 0.0

st.markdown("---")

# ── 탭 구성 ───────────────────────────────────────────────────────────────────
tab_main, tab_change, tab_seasonal, tab_hotspot = st.tabs([
    "📊 기본 분석",
    "🔄 변화 탐지",
    "📅 계절 트렌드",
    "🎯 핫스팟",
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — 기본 분석 (기존 화면 그대로)
# ══════════════════════════════════════════════════════════════════════════════
with tab_main:

    # ── A. 지도 ───────────────────────────────────────────────────────────────
    st.subheader(f"🗺️ {region_name} 위성 지도 ({cfg['index_name']})")
    m = folium.Map(location=[lat, lon], zoom_start=13)
    if res.tile_url:
        folium.TileLayer(
            tiles=res.tile_url,
            attr="Google Earth Engine",
            name=f"{cfg['index_name']} Index",
            overlay=True,
            control=True,
        ).add_to(m)
    st_folium(m, width="100%", height=500, returned_objects=[])

    # ── B. 차트 ───────────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("📊 데이터 분석 결과")
    col_m1, col_m2, col_m3 = st.columns(3)

    with col_m1:
        st.markdown("#### 🧭 현재 지수 상태")
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=round(avg_display, 4),
            title={"text": f"올해 {cfg['index_name']} 실측치", "font": {"size": 14}},
            gauge={
                "axis": {"range": [cfg["min"], cfg["max"]]},
                "bar": {"color": "#2ecc71" if good else "#e74c3c"},
                "threshold": {
                    "line": {"color": "red", "width": 3},
                    "thickness": 0.75,
                    "value": cfg["threshold"],
                },
            },
        ))
        fig_gauge.update_layout(height=250, margin=dict(l=20, r=20, t=40, b=20))
        st.plotly_chart(fig_gauge, use_container_width=True)

    with col_m2:
        st.markdown("#### 📅 전년 대비 비교")
        last_mean = res.last_year.mean
        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(
            x=["전년 동기", "올해 실측"],
            y=[last_mean if last_mean is not None else 0, avg_display],
            marker_color=["#bdc3c7", "#3498db" if good else "#e74c3c"],
            text=[
                f"{last_mean:.4f}" if last_mean is not None else "데이터 없음",
                f"{avg_display:.4f}",
            ],
            textposition="auto",
        ))
        fig_bar.update_layout(
            height=250,
            margin=dict(l=20, r=20, t=40, b=20),
            yaxis=dict(range=[cfg["min"] - 0.1, cfg["max"] + 0.1]),
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    with col_m3:
        st.markdown("#### 💡 종합 진단 요약")
        st.markdown("<br>", unsafe_allow_html=True)
        change_rate = res.change_rate
        if change_rate is not None:
            st.metric(
                label=f"🎯 올해 평균 {cfg['index_name']}",
                value=f"{avg_display:.4f}",
                delta=f"{change_rate:+.2f}% (전년 동기 대비)",
            )
        else:
            st.metric(
                label=f"🎯 올해 평균 {cfg['index_name']}",
                value=f"{avg_display:.4f}",
                delta="비교 불가 (전년 데이터 없음)",
                delta_color="off",
            )
        st.markdown("---")
        if good:
            st.success(f"**🟢 상태 양호**\n\n{cfg['desc_good']}")
        else:
            st.error(f"**🔴 주의 요망**\n\n{cfg['desc_bad']}")

    # ── C. 분포 통계 ──────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("📐 구역 내 분포 통계")
    col_s1, col_s2, col_s3, col_s4 = st.columns(4)
    col_s1.metric("평균", f"{avg_display:.4f}")
    col_s2.metric("최솟값", f"{cur.min_val:.4f}" if cur.min_val is not None else "—")
    col_s3.metric("최댓값", f"{cur.max_val:.4f}" if cur.max_val is not None else "—")
    col_s4.metric("표준편차", f"{cur.std_dev:.4f}" if cur.std_dev is not None else "—")

    cv = cur.coefficient_of_variation
    if cv is not None:
        if cv < 0.15:
            st.caption(f"📊 구역 내 수치가 비교적 균일합니다 (변동계수 {cv:.2f}).")
        elif cv < 0.4:
            st.caption(f"📊 구역 내 수치 편차가 다소 있습니다 (변동계수 {cv:.2f}). 일부 구간에서 평균과 다른 상태가 섞여 있을 수 있습니다.")
        else:
            st.caption(f"📊 구역 내 수치 편차가 큽니다 (변동계수 {cv:.2f}). 지도에서 구체적 위치를 함께 확인하는 걸 권장합니다.")

    # ── D. 시계열 ─────────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("📈 기간 내 실측 추이")
    time_series = res.time_series
    if len(time_series) < 2:
        st.caption("ℹ️ 선택한 기간 내 유효한 위성 촬영분이 2건 미만이라 추이 그래프를 표시할 수 없습니다. 기간을 넓혀보세요.")
    else:
        ts_dates = [d for d, _ in time_series]
        ts_values = [v for _, v in time_series]
        fig_ts = go.Figure()
        fig_ts.add_trace(go.Scatter(
            x=ts_dates, y=ts_values,
            mode="lines+markers",
            name=f"{cfg['index_name']} 실측치",
            line=dict(color="#3498db", width=2),
            marker=dict(size=8),
        ))
        fig_ts.add_hline(
            y=cfg["threshold"], line_dash="dash", line_color="red",
            annotation_text="기준선", annotation_position="top right",
        )
        fig_ts.update_layout(
            height=300,
            margin=dict(l=20, r=20, t=30, b=20),
            yaxis_title=f"{cfg['index_name']} 지수",
            xaxis_title="촬영일",
            yaxis=dict(range=[cfg["min"] - 0.1, cfg["max"] + 0.1]),
        )
        st.plotly_chart(fig_ts, use_container_width=True)
        st.caption(
            f"ℹ️ 선택 기간 내 유효 촬영일 {len(time_series)}개 날짜의 실측 평균값입니다. "
            "(같은 날짜 중복 촬영분은 평균으로 합침 / 예측·추정 없음, 100% 실측 데이터)"
        )

    # ── E. 교차 진단 ──────────────────────────────────────────────────────────
    if res.cross_results:
        st.markdown("---")
        st.subheader("🔬 교차 진단")
        for cr in res.cross_results:
            with st.container(border=True):
                st.markdown(f"**{cr.label}** · 짝 지표: {cr.partner_mode_key}")
                if not cr.available:
                    st.caption("⚠️ 짝 지표의 위성 데이터를 가져올 수 없어 교차 진단을 표시할 수 없습니다.")
                else:
                    st.markdown(f"#### {cr.title}")
                    st.caption(cr.description)
                    if cr.partner_mean is not None:
                        st.caption(f"(짝 지표 실측 평균: {cr.partner_mean:.4f})")
        st.caption("ℹ️ 교차 진단은 두 지수의 경향을 함께 본 참고용 해석이며, 현장 확인을 대체하지 않습니다.")

    # ── F. 엑셀 보고서 ────────────────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    with st.expander("🔽 상세 분석 보고서 다운로드 (Excel)"):
        st.markdown("현재 조회하신 데이터를 바탕으로 문서 첨부용 정식 보고서를 생성합니다.")
        try:
            excel_data = generate_excel_report(res)
            st.download_button(
                label="📥 엑셀 보고서 다운로드",
                data=excel_data,
                file_name=f"KSat_Report_{res.request.end_date.strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )
        except Exception:
            logger.exception("Excel report generation failed.")
            st.error("보고서 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — 변화 탐지
# ══════════════════════════════════════════════════════════════════════════════
with tab_change:
    st.subheader("🔄 두 기간 비교 — 변화 탐지")
    st.caption(
        "두 기간의 위성 지수를 비교해 어디가 얼마나 변했는지 지도로 보여줍니다. "
        "파란색은 지수 증가, 붉은색은 지수 감소를 나타냅니다."
    )

    col_b1, col_b2 = st.columns(2)
    with col_b1:
        st.markdown("**📅 비교 기준 기간 (Before)**")
        before_e = st.date_input("종료일", s_date, key="before_e")
        before_s = st.date_input("시작일", before_e - timedelta(days=30), key="before_s")
    with col_b2:
        st.markdown("**📅 비교 대상 기간 (After)**")
        after_s = st.date_input("시작일", s_date, key="after_s")
        after_e = st.date_input("종료일", e_date, key="after_e")

    change_btn = st.button("🔄 변화 탐지 실행", type="primary", key="change_btn")

    if change_btn:
        with st.spinner("두 기간 위성 이미지를 비교하는 중..."):
            tile_url, before_mean, after_mean = get_change_detection_tile_url(
                lat, lon, buffer_m,
                str(before_s), str(before_e),
                str(after_s), str(after_e),
                cloud, cfg,
            )

        if tile_url is None:
            st.warning("⚠️ 변화 탐지 이미지를 생성할 수 없습니다. 기간을 조정하거나 구름 허용률을 높여보세요.")
        else:
            # 수치 요약
            col_c1, col_c2, col_c3 = st.columns(3)
            with col_c1:
                st.metric("Before 평균", f"{before_mean:.4f}" if before_mean is not None else "—")
            with col_c2:
                st.metric("After 평균", f"{after_mean:.4f}" if after_mean is not None else "—")
            with col_c3:
                if before_mean and after_mean:
                    diff = after_mean - before_mean
                    direction = "증가 ▲" if diff > 0 else "감소 ▼"
                    st.metric("변화량", f"{diff:+.4f}", delta=direction)

            # 변화 탐지 지도
            m_change = folium.Map(location=[lat, lon], zoom_start=13)
            folium.TileLayer(
                tiles=tile_url,
                attr="Google Earth Engine",
                name="변화량 (After - Before)",
                overlay=True,
                control=True,
            ).add_to(m_change)
            st_folium(m_change, width="100%", height=500, returned_objects=[])

            st.caption(
                "🔵 파란색: 지수 증가 (식생 회복 / 수분 증가 / 불투수면 증가 등) | "
                "🔴 붉은색: 지수 감소 (식생 소실 / 건조화 / 녹지 감소 등) | "
                "⚪ 흰색: 변화 없음"
            )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — 계절 트렌드
# ══════════════════════════════════════════════════════════════════════════════
with tab_seasonal:
    st.subheader("📅 월별 · 계절별 트렌드")
    st.caption(
        "선택한 연도의 월별 평균값을 계산합니다. "
        "작물 생육 주기, 계절별 수분 변화, 열섬 강도 패턴을 파악할 수 있습니다."
    )

    col_y1, col_y2 = st.columns([1, 3])
    with col_y1:
        from datetime import date as date_cls
        current_year = date_cls.today().year
        trend_year = st.selectbox(
            "📆 분석 연도",
            list(range(current_year, current_year - 5, -1)),
            key="trend_year",
        )
        # 비교 연도 (선택)
        compare_year = st.selectbox(
            "📆 비교 연도 (선택)",
            ["없음"] + [str(y) for y in range(current_year - 1, current_year - 6, -1)],
            key="compare_year",
        )

    seasonal_btn = st.button("📅 계절 트렌드 조회", type="primary", key="seasonal_btn")

    if seasonal_btn:
        with st.spinner(f"{trend_year}년 월별 데이터 계산 중... (최대 1~2분 소요)"):
            monthly_data = get_seasonal_trend(
                lat, lon, buffer_m, trend_year, cloud, cfg
            )

        compare_data = []
        if compare_year != "없음":
            with st.spinner(f"{compare_year}년 비교 데이터 계산 중..."):
                compare_data = get_seasonal_trend(
                    lat, lon, buffer_m, int(compare_year), cloud, cfg
                )

        if not monthly_data:
            st.warning(f"⚠️ {trend_year}년 데이터가 없습니다. 다른 연도를 선택하거나 구름 허용률을 높여보세요.")
        else:
            fig_seasonal = go.Figure()

            # 메인 연도
            months = [d for d, _ in monthly_data]
            values = [v for _, v in monthly_data]
            fig_seasonal.add_trace(go.Scatter(
                x=months, y=values,
                mode="lines+markers",
                name=f"{trend_year}년",
                line=dict(color="#3498db", width=2.5),
                marker=dict(size=9),
            ))

            # 비교 연도
            if compare_data:
                c_months = [d for d, _ in compare_data]
                c_values = [v for _, v in compare_data]
                fig_seasonal.add_trace(go.Scatter(
                    x=c_months, y=c_values,
                    mode="lines+markers",
                    name=f"{compare_year}년",
                    line=dict(color="#e67e22", width=2, dash="dash"),
                    marker=dict(size=7, symbol="diamond"),
                ))

            fig_seasonal.add_hline(
                y=cfg["threshold"], line_dash="dot", line_color="red",
                annotation_text="기준선", annotation_position="top right",
            )
            fig_seasonal.update_layout(
                height=380,
                margin=dict(l=20, r=20, t=30, b=20),
                yaxis_title=f"{cfg['index_name']} 지수",
                xaxis_title="월",
                yaxis=dict(range=[cfg["min"] - 0.1, cfg["max"] + 0.1]),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig_seasonal, use_container_width=True)

            # 월별 수치 테이블
            import pandas as pd
            df_seasonal = pd.DataFrame({
                "월": months,
                f"{cfg['index_name']} ({trend_year}년)": [round(v, 4) for v in values],
            })
            if compare_data:
                compare_dict = dict(compare_data)
                df_seasonal[f"{cfg['index_name']} ({compare_year}년)"] = [
                    round(compare_dict.get(m, float("nan")), 4) for m in months
                ]
            st.dataframe(df_seasonal, hide_index=True, use_container_width=True)
            st.caption(f"ℹ️ {trend_year}년 유효 데이터 {len(monthly_data)}개월 / 데이터 없는 달은 표에서 제외됩니다.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — 핫스팟
# ══════════════════════════════════════════════════════════════════════════════
with tab_hotspot:
    st.subheader("🎯 구역 내 핫스팟 위치")
    st.caption(
        "분석 구역 안에서 지수가 가장 높거나 낮은 지점을 지도 위에 표시합니다. "
        "문제 지점을 빠르게 좁혀 현장 확인 우선순위를 정하는 데 활용하세요."
    )

    n_points = st.slider("📍 표시할 지점 수", 3, 10, 5, key="hotspot_n")
    hotspot_btn = st.button("🎯 핫스팟 찾기", type="primary", key="hotspot_btn")

    if hotspot_btn:
        with st.spinner("구역 내 픽셀을 분석하는 중..."):
            hotspots = get_hotspots(
                lat, lon, buffer_m,
                str(s_date), str(e_date),
                cloud, cfg, n_points,
            )

        high_pts = hotspots.get("high", [])
        low_pts = hotspots.get("low", [])

        if not high_pts and not low_pts:
            st.warning("⚠️ 핫스팟 데이터를 가져올 수 없습니다. 기간이나 구름 허용률을 조정해보세요.")
        else:
            higher_is_worse = cfg.get("higher_is_worse", False)

            # higher_is_worse에 따라 레이블 방향 결정
            if higher_is_worse:
                warn_label = "⚠️ 주의 지점 (값 높음)"
                good_label = "✅ 양호 지점 (값 낮음)"
                warn_pts = high_pts
                safe_pts = low_pts
                warn_color = "red"
                safe_color = "blue"
            else:
                warn_label = "⚠️ 주의 지점 (값 낮음)"
                good_label = "✅ 양호 지점 (값 높음)"
                warn_pts = low_pts
                safe_pts = high_pts
                warn_color = "red"
                safe_color = "green"

            # 핫스팟 지도
            m_hot = folium.Map(location=[lat, lon], zoom_start=14)

            if res.tile_url:
                folium.TileLayer(
                    tiles=res.tile_url,
                    attr="Google Earth Engine",
                    name=f"{cfg['index_name']}",
                    overlay=True,
                    control=True,
                ).add_to(m_hot)

            for pt_lat, pt_lon, val in warn_pts:
                folium.CircleMarker(
                    location=[pt_lat, pt_lon],
                    radius=10,
                    color=warn_color,
                    fill=True,
                    fill_color=warn_color,
                    fill_opacity=0.7,
                    popup=f"{warn_label}\n{cfg['index_name']}: {val:.4f}",
                    tooltip=f"⚠️ {val:.4f}",
                ).add_to(m_hot)

            for pt_lat, pt_lon, val in safe_pts:
                folium.CircleMarker(
                    location=[pt_lat, pt_lon],
                    radius=8,
                    color=safe_color,
                    fill=True,
                    fill_color=safe_color,
                    fill_opacity=0.6,
                    popup=f"{good_label}\n{cfg['index_name']}: {val:.4f}",
                    tooltip=f"✅ {val:.4f}",
                ).add_to(m_hot)

            # 분석 중심 표시
            folium.Marker(
                location=[lat, lon],
                popup=f"분석 중심: {region_name}",
                icon=folium.Icon(color="gray", icon="crosshairs", prefix="fa"),
            ).add_to(m_hot)

            st_folium(m_hot, width="100%", height=500, returned_objects=[])

            # 수치 테이블
            col_h1, col_h2 = st.columns(2)
            with col_h1:
                st.markdown(f"**{warn_label}**")
                import pandas as pd
                if warn_pts:
                    df_warn = pd.DataFrame(warn_pts, columns=["위도", "경도", cfg["index_name"]])
                    df_warn[cfg["index_name"]] = df_warn[cfg["index_name"]].round(4)
                    df_warn["위도"] = df_warn["위도"].round(5)
                    df_warn["경도"] = df_warn["경도"].round(5)
                    st.dataframe(df_warn, hide_index=True, use_container_width=True)

            with col_h2:
                st.markdown(f"**{good_label}**")
                if safe_pts:
                    df_safe = pd.DataFrame(safe_pts, columns=["위도", "경도", cfg["index_name"]])
                    df_safe[cfg["index_name"]] = df_safe[cfg["index_name"]].round(4)
                    df_safe["위도"] = df_safe["위도"].round(5)
                    df_safe["경도"] = df_safe["경도"].round(5)
                    st.dataframe(df_safe, hide_index=True, use_container_width=True)

            st.caption(
                "ℹ️ 핫스팟은 구역 내 샘플링된 픽셀 중 상위/하위 지점입니다. "
                "현장 확인의 우선순위 참고용이며 정밀 위치는 직접 검증이 필요합니다."
            )
