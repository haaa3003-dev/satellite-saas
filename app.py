import streamlit as st
import ee
import pandas as pd
import io
import requests

# ==========================================
# 1. 설정 및 도구 함수 (에러 방지용)
# ==========================================
def init_gee():
    """구글 어스 엔진 초기화 (에러 방지용 임시 성공 처리)"""
    try:
        # 실제 인증(Credentials) 전까지는 화면을 띄우기 위해 무조건 True 반환
        return True
    except Exception as e:
        st.error(f"GEE 에러: {e}")
        return False

@st.cache_data(show_spinner=False)
def geocode_place(query: str):
    """지명 검색 (OpenStreetMap API)"""
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": 5, "countrycodes": "kr", "accept-language": "ko"}
    try:
        res = requests.get(url, params=params, headers={"User-Agent": "satellite-saas-project/1.0"})
        return [(float(i["lat"]), float(i["lon"]), i["display_name"]) for i in res.json()]
    except:
        return []

def generate_excel_report(data_list):
    """엑셀 다운로드 파일 생성"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df = pd.DataFrame(data_list)
        df.to_excel(writer, index=False, sheet_name='관제결과')
    return output.getvalue()

# ==========================================
# 2. 메인 화면 UI 및 실행 로직
# ==========================================
st.set_page_config(page_title="지자체 원격 관제 플랫폼", page_icon="🛸")
st.title("🛸 지자체 원격 관제 플랫폼")

# GEE 상태 체크
gee_ready = init_gee()
if not gee_ready:
    st.warning("Google Earth Engine 초기화에 실패했습니다.")

# 사이드바: 지역 검색 폼
with st.sidebar.form("search_form"):
    search_query = st.text_input("지명 검색 (예: 서울특별시)")
    submitted = st.form_submit_button("검색")
    
    if submitted and search_query:
        st.session_state.results = geocode_place(search_query)

# 검색 결과 출력
if 'results' in st.session_state and st.session_state.results:
    st.sidebar.success(f"검색 결과: {len(st.session_state.results)}건 발견")
    for res in st.session_state.results:
        st.sidebar.write(f"- {res[2]} (위도:{res[0]:.4f}, 경도:{res[1]:.4f})")

# 분석 시작 버튼 및 결과 출력
if st.button("분석 시작"):
    st.info("위성 데이터 분석을 시작합니다... (테스트 모드)")
    
    # 임시 결과 데이터
    sample_data = [
        {"지역": "테스트 구역 A", "위성 지수": 0.85, "상태": "정상"},
        {"지역": "테스트 구역 B", "위성 지수": 0.42, "상태": "주의(점검요망)"}
    ]
    
    st.table(sample_data)
    
    # 엑셀 다운로드 버튼
    excel_data = generate_excel_report(sample_data)
    st.download_button(
        label="📊 분석 결과 엑셀 다운로드",
        data=excel_data,
        file_name="satellite_report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )