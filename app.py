# app.py
import streamlit as st
import ee
import folium
from streamlit_folium import st_folium
from datetime import date, timedelta
import pandas as pd
import plotly.graph_objects as go
import traceback

# 핵심 커스텀 모듈 임포트
from mode_config import mode_config, preset_coords
from gee_utils import init_gee, get_satellite_index_for_period, get_cached_stats, get_ee_tile_url
from geocoding import geocode_place
from report_builder import generate_excel_report

# 1. 페이지 테마 설정 (오픈 데이터 포털 느낌)
st.set_page_config(page_title="K-Sat 오픈 탐색기", page_icon="🌍", layout="wide")

# 상단 헤더 영역
st.title("🌍 K-Sat 위성 데이터 오픈 탐색기")
st.caption("누구나 자유롭게 분석하고 활용하는 Sentinel-2 위성 기반 환경·재해 모니터링 플랫폼")

# 환영 메시지 및 사용 안내
st.info("💡 **[안내]** 본 플랫폼은 오픈 위성 데이터를 활용하여 누구나 무료로 특정 지역의 농업 생육, 수자원, 산림 상태를 관측할 수 있도록 지원합니다. 아래 검색창에 궁금한 지역을 입력해 보세요!")

st.markdown("---")

# GEE 인증
gee_ready = init_gee()
if not gee_ready:
    st.error("🚨 위성 데이터 서버(GEE) 인증에 실패했습니다. 환경 설정을 확인해주세요.")
    st.stop()

# -----------------------------------------------------------------
# 2. 사이드바 컨트롤 시스템
# -----------------------------------------------------------------
st.sidebar.header("🛠️ 탐색 설정")
analysis_mode = st.sidebar.selectbox(
    "🔎 관측 모드 선택", 
    ["🌾 농작물 생육 분석 (NDVI)", "🌊 저수지 및 홍수 모니터링 (NDWI)", "🔥 산불 재해 및 산림 진단 (NBR)"]
)
cfg = mode_config[analysis_mode]

st.sidebar.markdown("---")
st.sidebar.caption("Powered by Google Earth Engine & K-Sat Team")

# -----------------------------------------------------------------
# 3. 메인 화면 - 중앙 검색창 및 지역 설정 (Form 최적화)
# -----------------------------------------------------------------
st.subheader("📍 관측 지역 검색")

# 검색창과 프리셋 선택창을 나란히 배치
col1, col2 = st.columns([2, 1])

with col1:
    # [핵심 장점 1] Form을 사용하여 "검색" 버튼 누를 때만 동작하도록 제어
    with st.form("search_form"):
        search_col, btn_col = st.columns([4, 1])
        with search_col:
            search_query = st.text_input(
                "🔍 지명 또는 주소를 입력하세요",
                placeholder="예: 춘천시 소양강, 새만금, 지리산 (입력 후 우측 검색 버튼 클릭)",
                label_visibility="collapsed"
            )
        with btn_col:
            search_submitted = st.form_submit_button("🔍 검색", use_container_width=True)

with col2:
    preset_choice = st.selectbox("📌 주요 관심 지역 빠르게 이동", ["직접 검색"] + list(preset_coords.keys()))

# 초기 좌표 설정
if 'lat' not in st.session_state:
    if preset_coords:
        first_preset = list(preset_coords.keys())[0]
        st.session_state.lat, st.session_state.lon = preset_coords[first_preset]
        st.session_state.region_name = first_preset
    else:
        st.session_state.lat, st.session_state.lon = 37.5665, 126.9780
        st.session_state.region_name = "기본 위치 (서울)"

# 지역 변경 로직 (Form 제출 시에만 검색 반응)
if search_submitted and search_query:
    results = geocode_place(search_query)
    if results:
        st.session_state.lat, st.session_state.lon, st.session_state.region_name = results[0]
        st.success(f"✅ '{st.session_state.region_name}'(으)로 좌표를 설정했습니다. 아래 버튼을 눌러주세요.")
    else:
        st.warning("⚠️ 검색 결과가 없습니다. 다른 검색어나 조금 더 넓은 지명을 입력해보세요.")
elif preset_choice != "직접 검색":
    if preset_choice in preset_coords:
        st.session_state.lat, st.session_state.lon = preset_coords[preset_choice]
        st.session_state.region_name = preset_choice

# -----------------------------------------------------------------
# 4. 상세 조건 설정 및 실행 버튼
# -----------------------------------------------------------------
st.markdown("---")
col_d1, col_d2, col_d3, col_d4 = st.columns(4)
with col_d1:
    e_date = st.date_input("📅 관측 종료일 (최근)", date.today())
with col_d2:
    s_date = st.date_input("📅 관측 시작일", e_date - timedelta(days=30))
with col_d3:
    cloud_threshold = st.slider("☁️ 구름 허용률 (%)", 0, 100, 20, 5, help="수치를 높이면 흐린 날의 데이터도 가져옵니다.")
with col_d4:
    st.markdown("<br>", unsafe_allow_html=True)
    run_btn = st.button("🚀 위성 데이터 불러오기", use_container_width=True, type="primary")

# -----------------------------------------------------------------
# 5. 위성 데이터 처리 및 렌더링 (예외 처리 및 스냅샷 캐싱)
# -----------------------------------------------------------------
if "analysis_done" not in st.session_state:
    st.session_state.analysis_done = False

# [핵심 장점 2] 버튼을 누른 순간에만 GEE 서버 호출 후 스냅샷으로 저장
if run_btn:
    if s_date >= e_date:
        st.warning("⚠️ 관측 시작일은 종료일보다 빨라야 합니다. 날짜를 다시 확인해주세요.")
    else:
        with st.spinner("🛰️ 우주에서 위성 이미지를 렌더링하고 있습니다. 잠시만 기다려주세요..."):
            # [핵심 장점 3] 완벽한 에러 방어막 (try-except)
            try:
                region = ee.Geometry.Point([st.session_state.lon, st.session_state.lat]).buffer(3000)

                image, calculated_index = get_satellite_index_for_period(
                    region, str(s_date), str(e_date), cloud_threshold, cfg['bands'], cfg['index_name']
                )

                count, stats = get_cached_stats(
                    st.session_state.lat, st.session_state.lon, 3000,
                    str(s_date), str(e_date), cloud_threshold, cfg['bands'], cfg['index_name']
                )

                if count == 0:
                    st.session_state.analysis_done = False
                    st.warning("⚠️ 지정한 기간과 지역에 유효한 위성 사진이 없습니다. 구름 허용률을 높이거나 기간을 늘려보세요.")
                else:
                    ly_start = s_date.replace(year=s_date.year - 1)
                    ly_end = e_date.replace(year=e_date.year - 1)
                    last_count, last_stats = get_cached_stats(
                        st.session_state.lat, st.session_state.lon, 3000,
                        str(ly_start), str(ly_end), cloud_threshold, cfg['bands'], cfg['index_name']
                    )

                    idx_name = cfg['index_name']

                    # [핵심 장점 4] 정확한 평균(mean) 값만 추출하여 오류 원천 차단
                    def get_safe_value(stat_dict, stat_key):
                        if not stat_dict or not isinstance(stat_dict, dict):
                            return None
                        val = stat_dict.get(stat_key)
                        return val if isinstance(val, (int, float)) else None

                    avg_val = get_safe_value(stats, f"{idx_name}_mean") or 0.0
                    last_avg = get_safe_value(last_stats, f"{idx_name}_mean") if last_count > 0 else None

                    vis_params = {'min': cfg['min'], 'max': cfg['max'], 'palette': cfg['palette']}
                    tile_url = get_ee_tile_url(calculated_index, vis_params)

                    change_rate = ((avg_val - last_avg) / abs(last_avg) * 100) if (last_avg is not None and last_avg != 0) else None
                    reliability_score = "우수 (95%)" if cloud_threshold <= 25 else "보통 (80%)"

                    # 분석 결과를 세션에 완전히 저장
                    st.session_state.analysis_done = True
                    st.session_state.result = {
                        "idx_name": idx_name,
                        "mode": analysis_mode,
                        "cfg": cfg,
                        "region_name": st.session_state.region_name,
                        "map_lat": st.session_state.lat,
                        "map_lon": st.session_state.lon,
                        "tile_url": tile_url,
                        "avg_val": avg_val,
                        "last_avg": last_avg,
                        "change_rate": change_rate,
                        "count": count,
                        "reliability_score": reliability_score,
                        "e_date": e_date,
                    }

            except Exception as e:
                st.session_state.analysis_done = False
                print(f"GEE Error: {traceback.format_exc()}")
                st.error("🚨 서버 통신 또는 데이터 렌더링 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

# -----------------------------------------------------------------
# 6. 화면 출력부 (세션에 저장된 결과 스냅샷만 사용하여 고속 렌더링)
# -----------------------------------------------------------------
if st.session_state.analysis_done:
    r = st.session_state.result
    idx_name, cfg_r = r["idx_name"], r["cfg"]
    avg_val, last_avg, change_rate = r["avg_val"], r["last_avg"], r["change_rate"]

    st.markdown("---")

    # [A] 지도 시각화
    st.subheader(f"🗺️ {r['region_name']} 위성 지도 ({idx_name})")
    m = folium.Map(location=[r["map_lat"], r["map_lon"]], zoom_start=13)
    folium.TileLayer(
        tiles=r["tile_url"],
        attr='Google Earth Engine',
        name=f'{idx_name} Index',
        overlay=True,
        control=True
    ).add_to(m)

    st_folium(m, width="100%", height=500, returned_objects=[], key="result_map")

    # [B] 3단 대시보드 시각화
    st.markdown("---")
    st.subheader("📊 데이터 분석 결과")

    col_m1, col_m2, col_m3 = st.columns([1, 1, 1])

    with col_m1:
        st.markdown("#### 🧭 현재 지수 상태")
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=round(avg_val, 4),
            title={'text': f"올해 {idx_name} 실측치", 'font': {'size': 14}},
            gauge={
                'axis': {'range': [cfg_r['min'], cfg_r['max']]},
                'bar': {'color': "#2ecc71" if avg_val >= cfg_r['threshold'] else "#e74c3c"},
                'threshold': {
                    'line': {'color': "red", 'width': 3},
                    'thickness': 0.75,
                    'value': cfg_r['threshold']
                }
            }
        ))
        fig_gauge.update_layout(height=250, margin=dict(l=20, r=20, t=40, b=20))
        st.plotly_chart(fig_gauge, use_container_width=True)

    with col_m2:
        st.markdown("#### 📅 전년 대비 비교")
        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(
            x=["전년 동기", "올해 실측"],
            y=[last_avg if last_avg is not None else 0, avg_val],
            marker_color=['#bdc3c7', '#3498db' if avg_val >= cfg_r['threshold'] else '#e74c3c'],
            text=[f"{last_avg:.4f}" if last_avg is not None else "데이터 없음", f"{avg_val:.4f}"],
            textposition='auto'
        ))
        fig_bar.update_layout(height=250, margin=dict(l=20, r=20, t=40, b=20), yaxis=dict(range=[cfg_r['min'] - 0.1, cfg_r['max'] + 0.1]))
        st.plotly_chart(fig_bar, use_container_width=True)

    with col_m3:
        st.markdown("#### 💡 종합 진단 요약")
        st.markdown("<br>", unsafe_allow_html=True)

        if change_rate is not None:
            st.metric(label=f"🎯 올해 평균 {idx_name}", value=f"{avg_val:.4f}", delta=f"{change_rate:+.2f}% (전년 동기 대비)")
        else:
            st.metric(label=f"🎯 올해 평균 {idx_name}", value=f"{avg_val:.4f}", delta="비교 불가 (전년 데이터 없음)", delta_color="off")

        st.markdown("---")
        if avg_val >= cfg_r['threshold']:
            st.success(f"**🟢 상태 양호**\n\n{cfg_r['desc_good']}")
        else:
            st.error(f"**🔴 주의 요망**\n\n{cfg_r['desc_bad']}")

    # [C] 엑셀 보고서 생성 및 다운로드 (아코디언 토글)
    st.markdown("<br>", unsafe_allow_html=True)
    with st.expander("🔽 상세 분석 보고서 다운로드 (Excel)"):
        st.markdown("현재 조회하신 데이터를 바탕으로 문서 첨부용 정식 보고서를 생성합니다.")

        change_rate_display = f"{change_rate:+.2f}%" if change_rate is not None else "비교 불가 (전년 데이터 없음)"

        df_report = pd.DataFrame({
            "관측 시점": ["전년 동기 평균 (대조군)" if last_avg is not None else "전년 동기 데이터 없음", f"올해 실측 평균 ({r['e_date'].strftime('%m/%d')})"],
            f"원격 탐사 지수 ({idx_name})": [round(last_avg, 4) if last_avg is not None else None, round(avg_val, 4)],
            "행정 정보 및 안전 진단 통계": [
                f"관제 지자체: {r['region_name']} / 플랫폼 모드: {r['mode']}",
                f"전년 대비 변화율: {change_rate_display} / 위성 데이터 신뢰도: {r['reliability_score']}"
            ]
        })
        st.dataframe(df_report, hide_index=True)

        excel_data = generate_excel_report(
            df_report, idx_name, r['region_name'], r['mode'],
            change_rate if change_rate is not None else 0, r['reliability_score'], r['count']
        )

        st.download_button(
            label="📥 엑셀 보고서 다운로드",
            data=excel_data,
            file_name=f"KSat_Report_{r['e_date'].strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary"
        )