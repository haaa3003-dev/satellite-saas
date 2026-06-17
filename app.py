import streamlit as st
# 모듈에서 필요한 함수들 불러오기
from services.gee_service import init_gee, get_satellite_index_for_period, get_ee_tile_url
from services.geocode_service import geocode_place
from utils.report_builder import generate_excel_report
from mode_config import mode_config, preset_coords

# 1. 초기화 및 UI 구성
gee_ready = init_gee()
st.title("🛸 지자체 원격 관제 플랫폼")

if not gee_ready:
    st.warning("Google Earth Engine이 아직 인증되지 않았거나 초기화에 실패했습니다.")

# 2. 사이드바 Form 적용 (API 과부하 방지)
with st.sidebar.form("search_form"):
    search_query = st.text_input("지명 검색 (예: 서울특별시)")
    submitted = st.form_submit_button("검색")
    
    if submitted:
        st.session_state.results = geocode_place(search_query)

# 검색 결과가 있으면 출력
if 'results' in st.session_state and st.session_state.results:
    st.sidebar.success(f"검색 결과: {len(st.session_state.results)}건 발견")
    st.sidebar.write(st.session_state.results)

# 3. 로직 실행
if st.button("분석 시작"):
    st.info("위성 데이터 분석을 시작합니다...")
    # 여기에 GEE 호출 및 시각화 로직이 들어갑니다.
    
    # 엑셀 다운로드 테스트용 (임시 데이터)
    sample_data = [{"지역": "테스트", "지수": 0.85}]
    excel_data = generate_excel_report(sample_data)
    
    st.download_button(
        label="📊 엑셀 리포트 다운로드",
        data=excel_data,
        file_name="종합관제보고서.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )