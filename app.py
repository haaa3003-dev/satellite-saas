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

st.set_page_config(layout="wide")
st.title("🛰️ 지자체 종합 재난재해 및 자원관리 원격 관제 플랫폼")
st.caption("항공기계공학과 융합 프로젝트 - Sentinel-2 다중 시계열 및 위성 밴드 융합 AI 공간분석 SaaS")

gee_ready = init_gee()
if not gee_ready:
    st.error("🚨 GEE 인증에 실패했습니다. 환경 설정을 확인해주세요.")
    st.stop()

# -----------------------------------------------------------------
# 사이드바 컨트롤 시스템
# -----------------------------------------------------------------
st.sidebar.header("🛠️ 종합 관제 컨트롤 패널")
analysis_mode = st.sidebar.selectbox(
    "🔎 분석 모드 선택", 
    ["🌾 농작물 생육 분석 (NDVI)", "🌊 저수지 및 홍수 모니터링 (NDWI)", "🔥 산불 재해 및 산림 진단 (NBR)"]
)
cfg = mode_config[analysis_mode]

st.sidebar.markdown("---")
st.sidebar.subheader("🎯 관제 타겟 지역 설정")

region_preset = st.sidebar.selectbox(
    "협업 대상 지자체 및 관제 지역 선택",
    list(preset_coords.keys()) + ["🔍 지명/주소로 검색"]
)

# 디폴트 좌표 초기화
default_lat, default_lon = preset_coords["📍 직접 좌표 입력"]
if region_preset in preset_coords:
    default_lat, default_lon = preset_coords[region_preset]

# 🌟 개선포인트 1: 지명 검색 컴포넌트를 st.form으로 격리하여 타이핑 중 과호출 완전 해결
if region_preset == "🔍 지명/주소로 검색":
    with st.sidebar.form("geocode_form"):
        search_query = st.text_input("지명 또는 주소를 입력하세요", placeholder="예: 충주시 살미면")
        search_submitted = st.form_submit_button("🔍 검색")

    if search_submitted and search_query:
        st.session_state.geocode_results = geocode_place(search_query)

    if "geocode_results" in st.session_state and st.session_state.geocode_results:
        option_labels = [name for (_, _, name) in st.session_state.geocode_results]
        selected_label = st.sidebar.radio("검색된 위치 중 선택하세요", option_labels, index=0)
        selected = next(r for r in st.session_state.geocode_results if r[2] == selected_label)
        default_lat, default_lon = selected[0], selected[1]
        st.sidebar.success(f"📍 좌표 적용됨: ({default_lat:.4f}, {default_lon:.4f})")
    elif search_submitted:
        st.sidebar.warning("⚠️ 검색 결과가 없습니다.")

lat = st.sidebar.number_input("위도 (Latitude)", value=default_lat, format="%.4f")
lon = st.sidebar.number_input("경도 (Longitude)", value=default_lon, format="%.4f")
buffer_m = st.sidebar.slider("관측 관제 반경 (m)", 500, 5000, 1500, step=500)

st.sidebar.markdown("---")
st.sidebar.subheader("📅 원격 시계열 분석 기간")
start_date = st.sidebar.date_input("관측 시작일", value=date.today() - timedelta(days=45))
end_date = st.sidebar.date_input("관측 종료일", value=date.today() - timedelta(days=5))
cloud_threshold = st.sidebar.slider("최대 허용 구름 비율 (%)", 5, 50, 25)

if "analysis_done" not in st.session_state:
    st.session_state.analysis_done = False

# -----------------------------------------------------------------
# 글로벌 위성 분석 엔진 바인딩
# -----------------------------------------------------------------
if st.sidebar.button("🛰️ 글로벌 위성 분석 엔진 가동"):
    if start_date >= end_date:
        st.warning("⚠️ 관측 날짜 설정을 다시 확인해주세요.")
        st.stop()

    with st.spinner(f"구글 슈퍼컴퓨터 인프라가 {cfg['index_name']} 매핑 및 연산을 수행 중입니다... 🚀"):
        try:
            point = ee.Geometry.Point([lon, lat])
            region = point.buffer(buffer_m)
            
            this_count, this_stats = get_cached_stats(
                lat, lon, buffer_m, str(start_date), str(end_date), cloud_threshold, cfg['bands'], cfg['index_name']
            )

            if this_count == 0:
                st.session_state.analysis_done = False
                st.warning("⚠️ 선택 기간에 구름 피복률이 높아 위성 영상을 합성할 수 없습니다.")
                st.stop()

            this_image, this_idx = get_satellite_index_for_period(
                region, str(start_date), str(end_date), cloud_threshold, cfg['bands'], cfg['index_name']
            )

            ly_start = start_date.replace(year=start_date.year - 1)
            ly_end = end_date.replace(year=end_date.year - 1)
            last_count, last_stats = get_cached_stats(
                lat, lon, buffer_m, str(ly_start), str(ly_end), cloud_threshold, cfg['bands'], cfg['index_name']
            )
            if last_count > 0:
                _, last_idx = get_satellite_index_for_period(
                    region, str(ly_start), str(ly_end), cloud_threshold, cfg['bands'], cfg['index_name']
                )
            else:
                last_idx = None
            
            # 레이어 렌더링 세팅 정보
            vis_params_rgb = {'bands': ['B4', 'B3', 'B2'], 'min': 0, 'max': 2500}
            st.session_state.rgb_tile_url = get_ee_tile_url(this_image.clip(region), vis_params_rgb)
            st.session_state.idx_tile_url = get_ee_tile_url(this_idx.clip(region), {'min': cfg['min'], 'max': cfg['max'], 'palette': cfg['palette']})

            if this_idx and last_idx:
                anomaly_idx = this_idx.subtract(last_idx)
                st.session_state.anomaly_tile_url = get_ee_tile_url(anomaly_idx.clip(region), {'min': cfg['anomaly_min'], 'max': cfg['anomaly_max'], 'palette': ['red', 'white', 'green']})
            else:
                st.session_state.anomaly_tile_url = None

            st.session_state.analysis_done = True
            st.session_state.count = this_count
            st.session_state.current_mode = analysis_mode
            raw_name = region_preset.split("] ")[-1] if "] " in region_preset else region_preset
            st.session_state.region_name = raw_name.split(" (")[0]
            st.session_state.avg_val = (this_stats if this_stats else {}).get(f"{cfg['index_name']}_mean", 0) or 0
            st.session_state.max_val = (this_stats if this_stats else {}).get(f"{cfg['index_name']}_max", 0) or 0
            st.session_state.last_avg_val = (last_stats if last_stats else {}).get(f"{cfg['index_name']}_mean", 0) or 0 if last_stats else None
            st.session_state.map_lat, st.session_state.map_lon = lat, lon
            st.session_state.start_date, st.session_state.end_date, st.session_state.buffer_m = start_date, end_date, buffer_m

        except Exception as e:
            st.session_state.analysis_done = False
            st.error(f"🚨 연산 중 오류 발생: {e}")

# -----------------------------------------------------------------
# 대시보드 뷰 렌더링
# -----------------------------------------------------------------
if st.session_state.analysis_done:
    curr_cfg = mode_config.get(st.session_state.current_mode, cfg)
    idx_name = curr_cfg['index_name']
    st.success(f"✅ [관제 가동] {st.session_state.region_name} 구역 - {st.session_state.current_mode} 탐지 완료")

    col1, col2 = st.columns([1.4, 1])
    with col1:
        st.subheader("🗺️ 지자체 공간정보 디지털 트윈 위성 맵")
        m = folium.Map(location=[st.session_state.map_lat, st.session_state.map_lon], zoom_start=14)
        folium.TileLayer(tiles=st.session_state.rgb_tile_url, attr='GEE', name='Sentinel-2 컬러 사진', overlay=True).add_to(m)
        folium.TileLayer(tiles=st.session_state.idx_tile_url, attr='GEE', name=f"{curr_cfg['label']} 맵 ({idx_name})", overlay=True).add_to(m)
        if st.session_state.get('anomaly_tile_url'):
            folium.TileLayer(tiles=st.session_state.anomaly_tile_url, attr='GEE', name='🚨 전년 동기 대비 이상 징후 레이어', overlay=True).add_to(m)
        
        folium.Circle(location=[st.session_state.map_lat, st.session_state.map_lon], radius=st.session_state.buffer_m, stroke=False, fill=True, fill_color='#1F4E78', fill_opacity=0.15).add_to(m)
        folium.LayerControl().add_to(m)
        st_folium(m, width=850, height=480, key="platform_map", returned_objects=[])

    with col2:
        st.subheader("📊 전년 대조 시계열 정량 지표")
        avg_val, max_val, last_avg = st.session_state.avg_val, st.session_state.max_val, st.session_state.last_avg_val
        if last_avg is not None and abs(last_avg) > 0:
            st.metric(label=f"📈 관측 구역 현재 평균 {curr_cfg['label']} 수치", value=f"{avg_val:.4f}", delta=f"{avg_val - last_avg:+.4f} (전년 동기 대조)")
        else:
            st.metric(label=f"📈 관측 구역 현재 평균 {curr_cfg['label']} 수치", value=f"{avg_val:.4f}", delta="전년 데이터 유실")
        st.metric(label="🚀 구역 내 최고 피크 수치", value=f"{max_val:.4f}")

        st.markdown("---")
        st.subheader("📋 공공 결재용 정밀 원격 진단서")
        if avg_val >= curr_cfg['threshold']:
            st.info(f"🟢 **정상 관제 단계:** {curr_cfg['desc_good']}\n\n" + (f"📊 전년 대비 환경 지표가 발달 중입니다." if (last_avg and avg_val > last_avg) else "⚠️ 전년 대비 소폭 지표 감소."))
        else:
            st.warning(f"🔴 **주의/위험 예찰 단계:** {curr_cfg['desc_bad']}")

        # 전년 동기 대비 실측 비교 그래프 (예측 없음, 실측값만)
        st.markdown("---")
        st.subheader(f"📈 전년 동기 대비 {idx_name} 실측 비교")
        s_date, e_date = st.session_state.start_date, st.session_state.end_date

        categories, values, colors = [], [], []
        if last_avg is not None:
            categories.append("전년 동기 평균")
            values.append(last_avg)
            colors.append("lightgray")
        categories.append(f"올해 실측 평균 ({e_date.strftime('%m/%d')})")
        values.append(avg_val)
        colors.append("#1F4E78")

        fig = go.Figure()
        fig.add_trace(go.Bar(x=categories, y=values, marker_color=colors, text=[f"{v:.4f}" for v in values], textposition="outside"))
        fig.update_layout(plot_bgcolor='rgba(240, 240, 240, 0.5)', yaxis_title=f"{idx_name} 지수", yaxis=dict(range=[curr_cfg['min'] - 0.1, curr_cfg['max'] + 0.1]), showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("ℹ️ 위 수치는 위성 영상으로 실측된 평균값만을 표시합니다. (예측값 아님)")

        # Excel 데이터 가공 및 파일 래핑
        st.markdown("---")
        st.subheader("📥 지자체 결재 및 첨부용 정식 보고서 (Excel)")
        change_rate = ((avg_val - last_avg) / abs(last_avg) * 100) if last_avg and abs(last_avg) > 0 else 0
        reliability_score = "우수 (95%)" if cloud_threshold <= 25 else "보통 (80%)"

        df_report = pd.DataFrame({
            "관측 시점": ["전년 동기 평균 (대조군)" if last_avg is not None else "전년 동기 데이터 없음", f"올해 실측 평균 ({e_date.strftime('%m/%d')})"],
            f"원격 탐사 지수 ({idx_name})": [round(last_avg, 4) if last_avg is not None else None, round(avg_val, 4)],
            "행정 정보 및 안전 진단 통계": [f"관제 지자체: {st.session_state.region_name} / 플랫폼 모드: {st.session_state.current_mode}", f"전년 동기 대비 변화율: {change_rate:+.2f}% / 위성 데이터 신뢰도: {reliability_score} ({st.session_state.count}장 합성)"]
        })
        
        excel_data = generate_excel_report(df_report, idx_name, st.session_state.region_name, st.session_state.current_mode, change_rate, reliability_score, st.session_state.count)
        st.download_button(label=f"📥 지자체 결재용 [{idx_name}] 관제 레포트 다운로드 (.xlsx)", data=excel_data, file_name=f"[{st.session_state.region_name}]_{idx_name}_종합관제보고서.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
else:
    st.info("👈 왼쪽 컨트롤 패널에서 분석 모드와 확장된 지자체 타겟 지역을 선택하고 엔진 가동 버튼을 클릭해 보세요.")