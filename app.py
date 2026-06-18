# app.py
import streamlit as st
import ee
import folium
from streamlit_folium import st_folium
from datetime import date, timedelta
import pandas as pd
import plotly.graph_objects as go

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
# 2. 사이드바 컨트롤 시스템 (모드 선택만 남기고 간소화)
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
# 3. 메인 화면 - 중앙 검색창 및 지역 설정
# -----------------------------------------------------------------
st.subheader("📍 관측 지역 검색")

# 검색창과 프리셋 선택창을 나란히 배치
col1, col2 = st.columns([2, 1])
with col1:
    search_query = st.text_input("🔍 지명 또는 주소를 입력하세요 (예: 춘천시 소양강, 새만금, 지리산)", placeholder="엔터를 누르면 검색됩니다")
with col2:
    preset_choice = st.selectbox("📌 주요 관심 지역 빠르게 이동", ["직접 검색"] + list(preset_coords.keys()))

# 세션 상태 초기화
if 'lat' not in st.session_state:
    first_preset = list(preset_coords.keys())[0]
    st.session_state.lat = preset_coords[first_preset][0]
    st.session_state.lon = preset_coords[first_preset][1]
    st.session_state.region_name = first_preset

# 지역 변경 로직 처리
if search_query:
    results = geocode_place(search_query)
    if results:
        st.session_state.lat, st.session_state.lon, st.session_state.region_name = results[0]
        st.success(f"✅ '{st.session_state.region_name}'(으)로 좌표를 설정했습니다. 아래의 '위성 데이터 불러오기' 버튼을 눌러주세요.")
    else:
        st.warning("⚠️ 검색 결과가 없습니다. 다른 검색어나 조금 더 넓은 지명을 입력해보세요.")
elif preset_choice != "직접 검색":
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
    cloud_threshold = st.slider("☁️ 구름 허용률 (%)", 0, 100, 20, 5, help="수치를 높이면 흐린 날의 사진도 포함하여 더 많은 데이터를 가져옵니다.")
with col_d4:
    st.markdown("<br>", unsafe_allow_html=True) # 줄맞춤용 공백
    run_btn = st.button("🚀 위성 데이터 불러오기", use_container_width=True, type="primary")


# -----------------------------------------------------------------
# 5. 위성 분석 렌더링 영역 (지도 증발 방지 로직 적용)
# -----------------------------------------------------------------
# 버튼을 누르면 '실행됨' 상태를 기억시킴
if run_btn:
    st.session_state.run_triggered = True

# 버튼이 눌린 적이 있다면 계속 지도를 띄워둠
if st.session_state.get('run_triggered', False):
    st.session_state.current_mode = analysis_mode
    st.markdown("---")

    with st.spinner("🛰️ 우주에서 위성 이미지를 렌더링하고 있습니다. 잠시만 기다려주세요..."):
        region = ee.Geometry.Point([st.session_state.lon, st.session_state.lat]).buffer(3000)
        
        # GEE 데이터 호출
        gee_result = get_satellite_index_for_period(
            region, str(s_date), str(e_date), cloud_threshold, cfg['bands'], cfg['index_name']
        )
        calculated_index = gee_result[-1] if isinstance(gee_result, tuple) else gee_result
        
        count, stats = get_cached_stats(st.session_state.lat, st.session_state.lon, 3000, str(s_date), str(e_date), cloud_threshold, cfg['bands'], cfg['index_name'])
        
        ly_start = s_date.replace(year=s_date.year - 1)
        ly_end = e_date.replace(year=e_date.year - 1)
        last_count, last_stats = get_cached_stats(st.session_state.lat, st.session_state.lon, 3000, str(ly_start), str(ly_end), cloud_threshold, cfg['bands'], cfg['index_name'])

    if count == 0:
        st.warning("⚠️ 지정한 기간과 지역에 구름이 너무 많거나 유효한 위성 사진이 없습니다. 구름 허용률을 높이거나 기간을 넓혀보세요.")
    else:
        idx_name = cfg['index_name']
        avg_val = stats.get(idx_name, 0)
        last_avg = last_stats.get(idx_name, None) if last_count > 0 else None

        # [A] 지도 시각화
        st.subheader(f"🗺️ {st.session_state.region_name} 위성 지도 ({idx_name})")
        m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
        vis_params = {'min': cfg['min'], 'max': cfg['max'], 'palette': cfg['palette']}
        tile_url = get_ee_tile_url(calculated_index, vis_params)
        folium.TileLayer(
            tiles=tile_url,
            attr='Google Earth Engine',
            name=f'{idx_name} Index',
            overlay=True,
            control=True
        ).add_to(m)
        
        # [핵심] returned_objects=[] 를 추가하여 지도를 움직여도 새로고침되지 않게 막음
        st_folium(m, width="100%", height=500, returned_objects=[])

     # [B] 차트 및 데이터 인사이트 (3단 대시보드 레이아웃으로 고도화)
        st.markdown("---")
        st.subheader("📊 데이터 분석 결과")
        
        # 화면을 3분할하여 꽉 찬 느낌을 줍니다.
        col_m1, col_m2, col_m3 = st.columns([1, 1, 1])
        
        with col_m1:
            st.markdown("#### 🧭 현재 지수 상태")
            # 1. 텅 비어보이지 않도록 화려한 게이지(계기판) 차트 추가
            fig_gauge = go.Figure(go.Indicator(
                mode = "gauge+number",
                value = avg_val,
                title = {'text': f"{idx_name} 평균 지수", 'font': {'size': 14}},
                gauge = {
                    'axis': {'range': [cfg['min'], cfg['max']]},
                    'bar': {'color': "#2ecc71" if avg_val >= cfg['threshold'] else "#e74c3c"},
                    'threshold': {
                        'line': {'color': "red", 'width': 3},
                        'thickness': 0.75,
                        'value': cfg['threshold']
                    }
                }
            ))
            fig_gauge.update_layout(height=250, margin=dict(l=20, r=20, t=40, b=20))
            st.plotly_chart(fig_gauge, use_container_width=True)

        with col_m2:
            st.markdown("#### 📅 전년 대비 비교")
            # 2. 막대 그래프 디자인 개선
            fig_bar = go.Figure()
            fig_bar.add_trace(go.Bar(
                x=["전년 동기", "올해 실측"],
                y=[last_avg if last_avg is not None else 0, avg_val],
                marker_color=['#bdc3c7', '#3498db' if avg_val >= cfg['threshold'] else '#e74c3c'],
                text=[f"{last_avg:.4f}" if last_avg is not None else "데이터 없음", f"{avg_val:.4f}"],
                textposition='auto'
            ))
            fig_bar.update_layout(
                height=250, 
                margin=dict(l=20, r=20, t=40, b=20),
                yaxis=dict(range=[cfg['min'] - 0.1, cfg['max'] + 0.1])
            )
            st.plotly_chart(fig_bar, use_container_width=True)

        with col_m3:
            st.markdown("#### 💡 종합 진단 요약")
            st.markdown("<br>", unsafe_allow_html=True)
            
            # 3. 데이터가 없을 때도 UI가 깨지지 않도록 Metric 개선
            if last_avg is not None and last_avg != 0:
                change_rate = ((avg_val - last_avg) / abs(last_avg)) * 100
                st.metric(label="📈 전년 동기 대비 변화율", value=f"{avg_val:.4f}", delta=f"{change_rate:+.2f}%")
            else:
                change_rate = 0
                st.metric(label="📈 전년 동기 대비 변화율", value=f"{avg_val:.4f}", delta="비교 불가 (전년 데이터 없음)", delta_color="off")
                
            st.markdown("---")
            # 상태 경고창 디자인
            if avg_val >= cfg['threshold']:
                st.success(f"**🟢 상태 양호**\n\n{cfg['desc_good']}")
            else:
                st.error(f"**🔴 주의 요망**\n\n{cfg['desc_bad']}")

        # [C] 엑셀 보고서 다운로드
        st.markdown("<br>", unsafe_allow_html=True)
        with st.expander("🔽 상세 분석 보고서 다운로드 (Excel)"):
            st.markdown("현재 조회하신 데이터를 바탕으로 문서 첨부용 정식 보고서를 생성합니다.")
            reliability_score = "우수 (95%)" if cloud_threshold <= 25 else "보통 (80%)"
            
            df_report = pd.DataFrame({
                "관측 시점": ["전년 동기 평균 (대조군)" if last_avg is not None else "전년 동기 데이터 없음", f"올해 실측 평균 ({e_date.strftime('%m/%d')})"],
                f"원격 탐사 지수 ({idx_name})": [round(last_avg, 4) if last_avg is not None else None, round(avg_val, 4)],
                "행정 정보 및 안전 진단 통계": [
                    f"관제 지자체: {st.session_state.region_name} / 플랫폼 모드: {st.session_state.current_mode}", 
                    f"전년 동기 대비 변화율: {change_rate:+.2f}% / 위성 데이터 신뢰도: {reliability_score}"
                ]
            })
            st.dataframe(df_report, hide_index=True)
            
            excel_data = generate_excel_report(
                df_report, idx_name, st.session_state.region_name, st.session_state.current_mode, 
                change_rate, reliability_score, count
            )
            
            st.download_button(
                label="📥 엑셀 보고서 다운로드",
                data=excel_data,
                file_name=f"KSat_Report_{e_date.strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )