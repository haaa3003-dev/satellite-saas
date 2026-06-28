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
    get_multi_point_stats,
    get_seasonal_trend,
    init_gee,
)
from mode_config import domain_config, mode_config, preset_coords
from models import AnalysisRequest, AnalysisResult, RegionInfo
from report_builder import generate_excel_report

# ─────────────────────────────────────────────
# 로깅 설정
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
st.caption("누구나 자유롭게 분석하고 활용하는 위성 기반 환경·재해 모니터링 플랫폼")
st.info(
    "💡 **[사용법]** ① 도메인과 분석 모드를 선택하세요. "
    "② 지도를 원하는 구역으로 줌인하세요. 화면 범위가 분석 구역이 됩니다. "
    "③ 날짜를 설정하고 버튼을 누르세요."
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
# 2. 사이드바 — 도메인 → 모드 2단계 선택
# ─────────────────────────────────────────────
st.sidebar.header("🛠️ 탐색 설정")

# ① 도메인 선택
selected_domain: str = st.sidebar.selectbox(
    "🗂️ 분석 도메인",
    list(domain_config.keys()),
    help="분석 목적에 맞는 도메인을 먼저 선택하세요.",
)
domain_cfg = domain_config[selected_domain]
st.sidebar.caption(domain_cfg["description"])
st.sidebar.markdown("---")

# ② 해당 도메인의 모드만 표시
available_modes = domain_cfg["modes"]
analysis_mode: str = st.sidebar.selectbox(
    "🔎 관측 모드",
    available_modes,
)
st.sidebar.markdown("---")
st.sidebar.caption("Powered by Google Earth Engine & K-Sat Team")

# 도메인별 활성 탭 목록
active_tabs: list[str] = domain_cfg["tabs"]

# 도메인이 바뀌면 프리셋 초기화
if st.session_state.get("last_domain") != selected_domain:
    st.session_state.last_domain = selected_domain
    st.session_state.preset_select = "직접 검색"

# ─────────────────────────────────────────────
# 3. 지역 선택 — 지도 이동 + 현재 화면 범위가 분석 구역
# ─────────────────────────────────────────────
st.subheader("📍 관측 지역 선택")


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
if "map_center" not in st.session_state:
    st.session_state.map_center = [_first_coords[0], _first_coords[1]]
if "map_zoom" not in st.session_state:
    st.session_state.map_zoom = 12
if "region_name" not in st.session_state:
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
    # 도메인에 맞는 프리셋만 필터링
    domain_preset_keys = domain_cfg.get("preset_keys", [])
    domain_presets = {k: preset_coords[k] for k in domain_preset_keys if k in preset_coords}
    st.selectbox(
        "📌 추천 지역",
        ["직접 검색"] + list(domain_presets.keys()),
        key="preset_select",
        on_change=_clear_search,
    )

# 지역 좌표 업데이트 (지도 중심점 이동)
if st.session_state.search_input:
    try:
        geo_results = geocode_place(st.session_state.search_input)
        if geo_results:
            found_lat, found_lon, found_name = geo_results[0]
            st.session_state.map_center = [found_lat, found_lon]
            st.session_state.map_zoom = 13
            st.session_state.region_name = found_name[:30]
            st.success(f"✅ '{found_name[:30]}'로 지도를 이동했습니다.")
        else:
            st.warning("⚠️ 검색 결과가 없습니다.")
    except NetworkError as exc:
        st.warning(f"⚠️ 지명 검색 중 오류: {exc}")

elif st.session_state.preset_select != "직접 검색":
    preset = st.session_state.preset_select
    if preset in domain_presets:
        st.session_state.map_center = list(domain_presets[preset])
        st.session_state.map_zoom = 13
        st.session_state.region_name = preset

# ── 지도 (현재 화면 범위 = 분석 구역) ──────────────────────────────────────
st.caption("🗺️ 지도를 이동·줌인하여 분석할 구역을 화면에 맞춰주세요. 화면에 보이는 사각형 범위가 분석 구역입니다.")

selector_map = folium.Map(
    location=st.session_state.map_center,
    zoom_start=st.session_state.map_zoom,
)
map_data = st_folium(
    selector_map,
    width="100%",
    height=320,
    returned_objects=["bounds"],
    key="selector_map",
)

# 지도에서 현재 화면 범위(bbox) 추출
current_bbox: tuple[float, float, float, float] | None = None
if map_data and map_data.get("bounds"):
    b = map_data["bounds"]
    try:
        west  = b["_southWest"]["lng"]
        south = b["_southWest"]["lat"]
        east  = b["_northEast"]["lng"]
        north = b["_northEast"]["lat"]
        current_bbox = (west, south, east, north)
        # 지도 중심 업데이트 (다음 렌더링 시 위치 유지)
        st.session_state.map_center = [(south + north) / 2, (west + east) / 2]
        st.session_state.map_zoom = map_data.get("zoom", st.session_state.map_zoom)
    except (KeyError, TypeError):
        current_bbox = None

if current_bbox:
    w, s, e, n = current_bbox
    st.caption(
        f"📐 현재 분석 구역: 위도 {s:.4f}°~{n:.4f}°, 경도 {w:.4f}°~{e:.4f}° "
        f"(약 {abs(e-w)*111:.1f}km × {abs(n-s)*111:.1f}km)"
    )

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
    if s_date >= e_date:
        st.warning("⚠️ 관측 시작일은 종료일보다 빨라야 합니다.")
    elif current_bbox is None:
        st.warning("⚠️ 지도가 로드되지 않았습니다. 잠시 후 다시 시도해주세요.")
    else:
        if "analysis_res" in st.session_state:
            del st.session_state["analysis_res"]

        w, s, e, n = current_bbox
        with st.spinner("🛰️ 위성 데이터를 렌더링하고 있습니다. 잠시만 기다려주세요..."):
            try:
                request = AnalysisRequest(
                    region=RegionInfo(
                        west=w, south=s, east=e, north=n,
                        name=st.session_state.region_name,
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
lat = res.request.region.center_lat
lon = res.request.region.center_lon
bbox = res.request.region.bbox
region_name = res.request.region.name
s_date = res.request.start_date
e_date = res.request.end_date
cloud = res.request.cloud_threshold
cur = res.current
good = cur.mean is not None and is_good_value(cur.mean, cfg)
avg_display = cur.mean if cur.mean is not None else 0.0

# 줌 레벨 — bbox 크기 기반 자동 계산
def _bbox_to_zoom(w: float, s: float, e: float, n: float) -> int:
    span_deg = max(abs(e - w), abs(n - s))
    if span_deg < 0.02:   return 15
    if span_deg < 0.05:   return 14
    if span_deg < 0.1:    return 13
    if span_deg < 0.3:    return 12
    if span_deg < 0.8:    return 11
    if span_deg < 2.0:    return 10
    return 9

map_zoom = _bbox_to_zoom(*bbox)

st.markdown("---")

# ── 탭 구성 — 도메인별 active_tabs만 표시 ────────────────────────────────────
ALL_TABS = {
    "📊 기본 분석":      None,
    "🔄 변화 탐지":      None,
    "📅 계절 트렌드":    None,
    "🎯 핫스팟":         None,
    "📍 다중 지점 비교": None,
}

# active_tabs에 기본 분석은 항상 포함
if "📊 기본 분석" not in active_tabs:
    active_tabs = ["📊 기본 분석"] + active_tabs

tab_objects = st.tabs(active_tabs)
tab_map = dict(zip(active_tabs, tab_objects))

# 편의 변수 — 없는 탭은 None
tab_main     = tab_map.get("📊 기본 분석")
tab_change   = tab_map.get("🔄 변화 탐지")
tab_seasonal = tab_map.get("📅 계절 트렌드")
tab_hotspot  = tab_map.get("🎯 핫스팟")
tab_multi    = tab_map.get("📍 다중 지점 비교")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — 기본 분석 (기존 화면 그대로)
# ══════════════════════════════════════════════════════════════════════════════
with tab_main:

    # ── A. 지도 ───────────────────────────────────────────────────────────────
    st.subheader(f"🗺️ {region_name} 위성 지도 ({cfg['index_name']})")
    m = folium.Map(location=[lat, lon], zoom_start=map_zoom)
    if res.tile_url:
        folium.TileLayer(
            tiles=res.tile_url,
            attr="Google Earth Engine",
            name=f"{cfg['index_name']} Index",
            overlay=True,
            control=True,
        ).add_to(m)
    st_folium(m, width="100%", height=500, returned_objects=[], key="main_map")

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
if tab_change:
  with tab_change:
    st.subheader("🔄 두 기간 비교 — 변화 탐지")
    st.caption(
        "두 기간의 위성 지수를 비교해 어디가 얼마나 변했는지 지도로 보여줍니다. "
        "파란색은 지수 증가, 붉은색은 지수 감소를 나타냅니다."
    )

    col_b1, col_b2 = st.columns(2)
    with col_b1:
        st.markdown("**📅 비교 기준 기간 (Before)**")
        before_s = st.date_input("시작일", s_date - timedelta(days=365), key="before_s")
        before_e = st.date_input("종료일", s_date - timedelta(days=335), key="before_e")
    with col_b2:
        st.markdown("**📅 비교 대상 기간 (After)**")
        after_s = st.date_input("시작일", s_date, key="after_s")
        after_e = st.date_input("종료일", e_date, key="after_e")

    change_btn = st.button("🔄 변화 탐지 실행", type="primary", key="change_btn")

    if change_btn:
        # 날짜 역전 체크
        if before_s >= before_e:
            st.warning("⚠️ Before 시작일이 종료일보다 늦습니다. 날짜를 다시 확인해주세요.")
        elif after_s >= after_e:
            st.warning("⚠️ After 시작일이 종료일보다 늦습니다. 날짜를 다시 확인해주세요.")
        elif before_e > after_s:
            st.warning("⚠️ Before 종료일이 After 시작일보다 늦습니다. 두 기간이 겹치지 않게 설정해주세요.")
        else:
            with st.spinner("두 기간 위성 이미지를 비교하는 중..."):
                tile_url, before_mean, after_mean = get_change_detection_tile_url(
                    bbox,
                    str(before_s), str(before_e),
                    str(after_s), str(after_e),
                    cloud, cfg,
                )

            if tile_url is None:
                err_msg = after_mean if isinstance(after_mean, str) else ""
                st.warning("⚠️ 변화 탐지 이미지를 생성할 수 없습니다. 기간을 조정하거나 구름 허용률을 높여보세요.")
                if err_msg:
                    with st.expander("🔍 상세 오류 내용 (디버깅용)"):
                        st.code(err_msg)
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
                st_folium(m_change, width="100%", height=500, returned_objects=[], key="change_map")

                st.caption(
                    "🔵 파란색: 지수 증가 (식생 회복 / 수분 증가 / 불투수면 증가 등) | "
                    "🔴 붉은색: 지수 감소 (식생 소실 / 건조화 / 녹지 감소 등) | "
                    "⚪ 흰색: 변화 없음"
                )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — 계절 트렌드
# ══════════════════════════════════════════════════════════════════════════════
if tab_seasonal:
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
                bbox, trend_year, cloud, cfg
            )

        compare_data = []
        if compare_year != "없음":
            with st.spinner(f"{compare_year}년 비교 데이터 계산 중..."):
                compare_data = get_seasonal_trend(
                    bbox, int(compare_year), cloud, cfg
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
if tab_hotspot:
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
                bbox,
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
            m_hot = folium.Map(location=[lat, lon], zoom_start=map_zoom + 1)

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

            st_folium(m_hot, width="100%", height=500, returned_objects=[], key="hotspot_map")

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


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — 다중 지점 동시 비교
# ══════════════════════════════════════════════════════════════════════════════
if tab_multi:
  with tab_multi:
    import pandas as pd

    st.subheader("📍 다중 지점 동시 비교")
    st.caption(
        "최대 5개 지점을 등록하고 같은 기간·같은 모드로 나란히 비교합니다. "
        "지자체 관할 구역 여러 곳을 한 번에 모니터링하거나 "
        "개발 전·후 여러 필지를 동시에 추적할 때 활용하세요."
    )

    # ── 지점 등록 UI ──────────────────────────────────────────────────────────
    st.markdown("#### 📌 비교 지점 등록 (최대 5개)")

    # 세션에 지점 목록 유지
    if "multi_points" not in st.session_state:
        # 현재 분석 중인 지점을 기본값으로 등록
        st.session_state.multi_points = [
            {"name": region_name[:20], "lat": lat, "lon": lon}
        ]

    # 지점 추가 폼
    with st.expander("➕ 새 지점 추가", expanded=len(st.session_state.multi_points) < 2):
        col_add1, col_add2 = st.columns([2, 1])
        with col_add1:
            new_name = st.text_input(
                "지점 이름",
                placeholder="예: 전북 김제시 부량면",
                key="multi_new_name",
            )
            new_search = st.text_input(
                "지명 검색 (입력 후 Enter)",
                placeholder="예: 새만금, 당진평야",
                key="multi_search",
            )
        with col_add2:
            new_lat = st.number_input("위도", value=37.5665, format="%.4f", key="multi_lat")
            new_lon = st.number_input("경도", value=126.9780, format="%.4f", key="multi_lon")

        # 지명 검색으로 좌표 자동 입력
        if new_search:
            try:
                geo_results = geocode_place(new_search)
                if geo_results:
                    found_lat, found_lon, found_name = geo_results[0]
                    st.info(f"✅ 검색 결과: {found_name[:40]}")
                    st.session_state["multi_lat"] = found_lat
                    st.session_state["multi_lon"] = found_lon
                    if not new_name:
                        st.session_state["multi_new_name"] = found_name[:20]
            except NetworkError:
                st.warning("⚠️ 지명 검색에 실패했습니다. 직접 좌표를 입력해주세요.")

        add_btn = st.button("➕ 지점 추가", key="multi_add_btn")
        if add_btn:
            if len(st.session_state.multi_points) >= 5:
                st.warning("⚠️ 최대 5개까지 등록할 수 있습니다.")
            elif not new_name.strip():
                st.warning("⚠️ 지점 이름을 입력해주세요.")
            else:
                st.session_state.multi_points.append({
                    "name": new_name.strip()[:20],
                    "lat": new_lat,
                    "lon": new_lon,
                })
                st.rerun()

    # ── 등록된 지점 목록 ──────────────────────────────────────────────────────
    st.markdown(f"**현재 등록된 지점 ({len(st.session_state.multi_points)}개)**")

    to_delete = None
    for i, pt in enumerate(st.session_state.multi_points):
        col_pt1, col_pt2, col_pt3, col_pt4 = st.columns([3, 2, 2, 1])
        col_pt1.markdown(f"**{i+1}. {pt['name']}**")
        col_pt2.caption(f"위도 {pt['lat']:.4f}")
        col_pt3.caption(f"경도 {pt['lon']:.4f}")
        if col_pt4.button("🗑️", key=f"del_{i}", help="지점 삭제"):
            to_delete = i

    if to_delete is not None:
        st.session_state.multi_points.pop(to_delete)
        st.rerun()

    # ── 비교 실행 ─────────────────────────────────────────────────────────────
    st.markdown("---")
    if len(st.session_state.multi_points) < 2:
        st.info("ℹ️ 비교하려면 지점이 2개 이상 필요합니다. 위에서 지점을 추가해주세요.")
    else:
        multi_btn = st.button(
            f"📊 {len(st.session_state.multi_points)}개 지점 동시 비교 실행",
            type="primary",
            key="multi_run_btn",
        )

        if multi_btn:
            points_tuple = tuple(
                (pt["lat"], pt["lon"], pt["name"])
                for pt in st.session_state.multi_points
            )
            with st.spinner(f"{len(st.session_state.multi_points)}개 지점 분석 중... 잠시만 기다려주세요."):
                multi_results = get_multi_point_stats(
                    points=points_tuple,
                    start_date=str(s_date),
                    end_date=str(e_date),
                    cloud_threshold=cloud,
                    mode_cfg=cfg,
                )
            st.session_state.multi_results = multi_results

    # ── 결과 렌더링 ───────────────────────────────────────────────────────────
    if "multi_results" in st.session_state:
        multi_results = st.session_state.multi_results
        valid = [r for r in multi_results if r["mean"] is not None]

        if not valid:
            st.warning("⚠️ 유효한 데이터가 있는 지점이 없습니다. 기간이나 구름 허용률을 조정해보세요.")
        else:
            # ── 수치 비교 바 차트 ─────────────────────────────────────────────
            st.markdown("---")
            st.subheader(f"📊 {cfg['index_name']} 지점별 비교")

            names = [r["name"] for r in valid]
            means = [r["mean"] for r in valid]
            colors = [
                "#2ecc71" if is_good_value(m, cfg) else "#e74c3c"
                for m in means
            ]

            fig_multi = go.Figure()
            fig_multi.add_trace(go.Bar(
                x=names,
                y=means,
                marker_color=colors,
                text=[f"{v:.4f}" for v in means],
                textposition="outside",
            ))
            fig_multi.add_hline(
                y=cfg["threshold"],
                line_dash="dash",
                line_color="red",
                annotation_text="기준선",
                annotation_position="top right",
            )
            fig_multi.update_layout(
                height=350,
                margin=dict(l=20, r=20, t=30, b=20),
                yaxis_title=f"{cfg['index_name']} 평균값",
                yaxis=dict(range=[cfg["min"] - 0.1, cfg["max"] + 0.1]),
                showlegend=False,
            )
            st.plotly_chart(fig_multi, use_container_width=True)

            # ── 레이더 차트 (정규화 비교) ─────────────────────────────────────
            if len(valid) >= 3:
                st.markdown("#### 🕸️ 지점별 지수 분포 비교 (레이더)")
                fig_radar = go.Figure()
                for r in valid:
                    # min~max 범위로 0~1 정규화
                    rng = cfg["max"] - cfg["min"]
                    norm_mean  = (r["mean"]    - cfg["min"]) / rng if r["mean"]    is not None else 0
                    norm_min   = (r["min_val"] - cfg["min"]) / rng if r["min_val"] is not None else 0
                    norm_max   = (r["max_val"] - cfg["min"]) / rng if r["max_val"] is not None else 0
                    norm_std   = min((r["std_dev"] or 0) / (rng / 2), 1.0)

                    fig_radar.add_trace(go.Scatterpolar(
                        r=[norm_mean, norm_max, norm_min, 1 - norm_std, norm_mean],
                        theta=["평균", "최댓값", "최솟값", "균일성", "평균"],
                        fill="toself",
                        name=r["name"],
                        opacity=0.65,
                    ))
                fig_radar.update_layout(
                    polar=dict(radialaxis=dict(visible=True, range=[0, 1])),
                    height=380,
                    margin=dict(l=40, r=40, t=40, b=40),
                    legend=dict(orientation="h", yanchor="bottom", y=-0.2),
                )
                st.plotly_chart(fig_radar, use_container_width=True)
                st.caption("ℹ️ 모든 값은 해당 모드의 min~max 범위로 0~1 정규화한 값입니다. 균일성은 표준편차가 낮을수록 높게 표시됩니다.")

            # ── 수치 요약 테이블 ──────────────────────────────────────────────
            st.markdown("---")
            st.subheader("📋 지점별 통계 요약")
            df_multi = pd.DataFrame([
                {
                    "지점명": r["name"],
                    "위도": round(r["lat"], 4),
                    "경도": round(r["lon"], 4),
                    f"평균 ({cfg['index_name']})": round(r["mean"], 4) if r["mean"] is not None else None,
                    "최솟값": round(r["min_val"], 4) if r["min_val"] is not None else None,
                    "최댓값": round(r["max_val"], 4) if r["max_val"] is not None else None,
                    "표준편차": round(r["std_dev"], 4) if r["std_dev"] is not None else None,
                    "영상 수": r["count"],
                    "상태": "🟢 양호" if r["mean"] is not None and is_good_value(r["mean"], cfg) else "🔴 주의",
                }
                for r in multi_results
            ])
            st.dataframe(df_multi, hide_index=True, use_container_width=True)

            # ── 지점별 위성 지도 (나란히) ─────────────────────────────────────
            st.markdown("---")
            st.subheader("🗺️ 지점별 위성 지도")
            st.caption("각 지점의 위성 영상 레이어를 개별 지도로 표시합니다.")

            cols_map = st.columns(min(len(valid), 3))
            for i, r in enumerate(valid):
                col_idx = i % 3
                with cols_map[col_idx]:
                    st.markdown(f"**{r['name']}**")
                    st.caption(
                        f"{cfg['index_name']}: {r['mean']:.4f} "
                        f"{'🟢' if is_good_value(r['mean'], cfg) else '🔴'}"
                    )
                    m_pt = folium.Map(
                        location=[r["lat"], r["lon"]],
                        zoom_start=13,
                    )
                    if r["tile_url"]:
                        folium.TileLayer(
                            tiles=r["tile_url"],
                            attr="Google Earth Engine",
                            name=cfg["index_name"],
                            overlay=True,
                            control=True,
                        ).add_to(m_pt)
                    folium.Marker(
                        location=[r["lat"], r["lon"]],
                        popup=r["name"],
                        icon=folium.Icon(color="blue", icon="info-sign"),
                    ).add_to(m_pt)
                    st_folium(
                        m_pt,
                        width="100%",
                        height=280,
                        returned_objects=[],
                        key=f"multi_map_{i}",
                    )
