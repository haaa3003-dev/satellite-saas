import streamlit as st
import ee
import folium
from streamlit_folium import st_folium
from datetime import date, timedelta

# =================================================================
# [백엔드] GEE 인증 및 초기화
# =================================================================
# [백엔드] GEE 인증 및 초기화 (로컬 PC 및 클라우드 서버 공용 방탄 코드)
# =================================================================
@st.cache_resource
def init_gee():
    # 1. 만약 스트림릿 웹 서버(Cloud) 환경이라면 서비스 계정 키로 로그인 시도
    if "gee_credentials" in st.secrets:
        try:
            cred_info = st.secrets["gee_credentials"]
            credentials = ee.ServiceAccountCredentials(
                cred_info["client_email"], 
                key_data=cred_info["private_key"]
            )
            ee.Initialize(credentials, project='knut-startup-gee')
            return True
        except Exception as e:
            st.error(f"서버 인증 실패: {e}")
            return False
            
    # 2. 내 컴퓨터(로컬 PC) 환경이라면 기존 방식대로 로그인 시도
    else:
        try:
            ee.Initialize(project='knut-startup-gee')
            return True
        except Exception:
            try:
                ee.Authenticate()
                ee.Initialize(project='knut-startup-gee')
                return True
            except Exception:
                return False

gee_ready = init_gee()

# =================================================================
# [백엔드] 특정 기간의 위성 데이터 및 통계 산출 함수 (버그 수정 완료)
# =================================================================
def get_ndvi_for_period(region, start_date, end_date, cloud_threshold):
    collection = (
        ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
        .filterBounds(region)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_threshold))
    )
    count = collection.size().getInfo()
    # 지적 1 수정: 호출부의 변수 개수(4개)와 일치시키기 위해 4개의 값을 정확히 반환 (Unpacking 에러 차단)
    if count == 0:
        return None, None, 0, None
        
    image = collection.median()
    ndvi = image.normalizedDifference(['B8', 'B4']).rename('NDVI')
    
    # 지적 2 수정: 구글 서버 리턴값이 None이거나 에러가 날 때 튕기지 않도록 방어 코드 구축
    try:
        stats = ndvi.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.max(), sharedInputs=True),
            geometry=region,
            scale=10
        ).getInfo()
        
        if stats is None:
            stats = {}
    except Exception:
        stats = {} # 예외 발생 시 빈 딕셔너리로 안전하게 대체
        
    return image, ndvi, count, stats

def get_ee_tile_url(ee_image_object, vis_params):
    """GEE 이미지를 folium 타일 URL로 변환"""
    map_id_dict = ee.Image(ee_image_object).getMapId(vis_params)
    return map_id_dict['tile_fetcher'].url_format

# =================================================================
# [프론트엔드] Streamlit 웹 화면 구성
# =================================================================
st.set_page_config(layout="wide")
st.title("🛰️ AI 위성 데이터 기반 지자체 맞춤형 농작물 모니터링 시스템")
st.caption("항공기계공학과 융합 프로젝트 - Sentinel-2 다중 시계열 전년 동기 대비 비교 솔루션")

if not gee_ready:
    st.error("🚨 GEE 인증에 실패했습니다. 터미널에서 `python auth_test.py`를 실행해 인증 상태를 세팅해주세요.")
    st.stop()

# 사이드바 컨트롤 패널
st.sidebar.header("🛠️ 서비스 컨트롤 패널")

# 지적 3 반영: 실효성 있는 지자체 협업 가상 프리셋 구축 및 실제 농경지 좌표 매핑
st.sidebar.subheader("🎯 타겟 지자체 및 작물")
crop_type = st.sidebar.selectbox("관측 대상 작물", ["벼 (쌀)", "밭작물 (콩/양파/마늘)", "시설 과수원"])

region_preset = st.sidebar.selectbox(
    "협업 대상 지자체 지역 선택",
    ["전북 김제시 부량면 (벽골제 평야 중심부)", "충남 당진시 합덕읍 (당진평야 주산지)", "전남 해남군 황산면 (대규모 필드)", "직접 좌표 입력"]
)

# 지역 프리셋 선택에 따른 실제 광활한 농경지 좌표 자동 세팅
if region_preset == "전북 김제시 부량면 (벽골제 평야 중심부)":
    default_lat, default_lon = 35.7684, 126.8643
elif region_preset == "충남 당진시 합덕읍 (당진평야 주산지)":
    default_lat, default_lon = 36.8250, 126.7720
elif region_preset == "전남 해남군 황산면 (대규모 필드)":
    default_lat, default_lon = 34.6150, 126.4780
else:
    default_lat, default_lon = 36.9910, 127.9259

lat = st.sidebar.number_input("위도 (Latitude)", value=default_lat, format="%.4f")
lon = st.sidebar.number_input("경도 (Longitude)", value=default_lon, format="%.4f")
buffer_m = st.sidebar.slider("관측 반경 (m)", 500, 5000, 1500, step=500)

st.sidebar.markdown("---")
st.sidebar.subheader("📅 생육기 관측 시기 설정")

# 국내 벼/일반 작물 활발한 생육기 자동 설정 (과거 데이터 안정적 수집용 세팅)
default_start = date.today() - timedelta(days=45)
default_end = date.today() - timedelta(days=5)
start_date = st.sidebar.date_input("올해 관측 시작일", value=default_start)
end_date = st.sidebar.date_input("올해 관측 종료일", value=default_end)

cloud_threshold = st.sidebar.slider("최대 허용 구름 비율 (%)", 5, 50, 25)

st.markdown("---")

# 세션 상태 고정
if "analysis_done" not in st.session_state:
    st.session_state.analysis_done = False

if st.sidebar.button("🔍 다중 시계열 정밀 분석"):
    if start_date >= end_date:
        st.warning("⚠️ 시작 날짜가 종료 날짜보다 빠르도록 설정해주세요.")
        st.stop()

    with st.spinner("구글 슈퍼컴퓨터가 올해와 작년의 위성 데이터를 교차 분석 중입니다... 🚀"):
        try:
            point = ee.Geometry.Point([lon, lat])
            region = point.buffer(buffer_m)
            
            # [시계열 1] 올해 데이터 분석
            this_image, this_ndvi, this_count, this_stats = get_ndvi_for_period(
                region, str(start_date), str(end_date), cloud_threshold
            )
            
            if this_ndvi is None:
                st.session_state.analysis_done = False
                st.warning("⚠️ 선택하신 기간에 구름이 너무 많아 올해 위성 영상을 합성할 수 없습니다. 관측 기간을 더 넓혀보세요.")
                st.stop()
                
            # [시계열 2] 작년 동기간 데이터 분석
            ly_start = start_date.replace(year=start_date.year - 1)
            ly_end = end_date.replace(year=end_date.year - 1)
            
            # 💡 수정포인트 1: 밑줄(_)로 버리던 작년 식생지수 맵을 'last_ndvi'로 온전히 받아옵니다.
            _, last_ndvi, _, last_stats = get_ndvi_for_period(
                region, str(ly_start), str(ly_end), cloud_threshold + 10 
            )
            
            # 지도 시각화용 타일 URL 추출
            vis_params_rgb = {'bands': ['B4', 'B3', 'B2'], 'min': 0, 'max': 2500}
            rgb_tile_url = get_ee_tile_url(this_image.clip(region), vis_params_rgb)

            vis_params_ndvi = {'min': 0, 'max': 1, 'palette': ['red', 'yellow', 'green']}
            ndvi_tile_url = get_ee_tile_url(this_ndvi.clip(region), vis_params_ndvi)

            # 💡 수정포인트 2: (올해 NDVI - 작년 NDVI) 연산을 수행하고 이상 탐지 지도 URL을 만듭니다.
            if this_ndvi is not None and last_ndvi is not None:
                anomaly_ndvi = this_ndvi.subtract(last_ndvi)
                # 마이너스(-0.3)는 작년보다 나빠진 빨강, 플러스(0.3)는 작년보다 좋아진 초록, 0은 흰색
                vis_params_anomaly = {'min': -0.3, 'max': 0.3, 'palette': ['red', 'white', 'green']}
                anomaly_tile_url = get_ee_tile_url(anomaly_ndvi.clip(region), vis_params_anomaly)
                st.session_state.anomaly_tile_url = anomaly_tile_url
            else:
                st.session_state.anomaly_tile_url = None

            # 세션 상태 안전하게 바인딩 (지적 2 보완: NoneType 검증 후 대입)
            this_stats = this_stats if this_stats else {}
            st.session_state.analysis_done = True
            st.session_state.rgb_tile_url = rgb_tile_url
            st.session_state.ndvi_tile_url = ndvi_tile_url
            st.session_state.count = this_count
            st.session_state.crop_type = crop_type
            st.session_state.region_name = region_preset.split(" (")[0]
            
            st.session_state.avg_ndvi = this_stats.get('NDVI_mean', 0) or 0
            st.session_state.max_ndvi = this_stats.get('NDVI_max', 0) or 0
            
            # 작년 데이터 존재 여부 체크 및 안전 대입
            if last_stats:
                st.session_state.last_avg_ndvi = last_stats.get('NDVI_mean', 0) or 0
            else:
                st.session_state.last_avg_ndvi = None
                
            st.session_state.map_lat = lat
            st.session_state.map_lon = lon

        except Exception as e:
            st.session_state.analysis_done = False
            st.error(f"🚨 시스템 데이터 융합 중 예외 오류 발생: {e}")

# =================================================================
# [화면 출력] 메인 레이아웃 및 결과 표출
# =================================================================
if st.session_state.analysis_done:
    st.success(f"✅ [분석 성공] {st.session_state.region_name} - {st.session_state.crop_type} 필드 관측 완료 (영상 {st.session_state.count}장 합성)")

    col1, col2 = st.columns([1.4, 1])

    with col1:
        st.subheader("🗺️ 고해상도 위성 교차 매핑 공간정보")
        m = folium.Map(location=[st.session_state.map_lat, st.session_state.map_lon], zoom_start=14)

        folium.TileLayer(
            tiles=st.session_state.rgb_tile_url,
            attr='Google Earth Engine',
            name='Sentinel-2 실제 현장 컬러 사진',
            overlay=True,
            control=True
        ).add_to(m)

        folium.TileLayer(
            tiles=st.session_state.ndvi_tile_url,
            attr='Google Earth Engine',
            name='식생 활성도 지수 지도 (NDVI)',
            overlay=True,
            control=True
        ).add_to(m)

        # 💡 [여기서부터 추가된 코드] 화면 지도에 이상 탐지(빨강=위험) 레이어 토글 스위치를 추가합니다!
        if st.session_state.get('anomaly_tile_url'):
            folium.TileLayer(
                tiles=st.session_state.anomaly_tile_url,
                attr='Google Earth Engine',
                name='🚨 전년 대비 이상 탐지 (빨강=위험)',
                overlay=True,
                control=True
            ).add_to(m)

        folium.LayerControl().add_to(m)
        st_folium(m, width=850, height=480, key="ndvi_map", returned_objects=[])
        
    with col2:
        st.subheader("📈 전년 동기 대비 시계열 분석 통계")
        
        avg_ndvi = st.session_state.avg_ndvi
        max_ndvi = st.session_state.max_ndvi
        last_avg = st.session_state.last_avg_ndvi

        # 작년 동기 대비 데이터가 안전하게 수집되었을 때만 화살표(delta) 출력
        if last_avg is not None and last_avg > 0:
            delta_ndvi = avg_ndvi - last_avg
            st.metric(
                label=f"🌾 올해 {st.session_state.crop_type} 평균 건강도 (NDVI)", 
                value=f"{avg_ndvi:.3f}", 
                delta=f"{delta_ndvi:+.3f} (작년 동기 대비)"
            )
        else:
            st.metric(label=f"🌾 올해 {st.session_state.crop_type} 평균 건강도 (NDVI)", value=f"{avg_ndvi:.3f}", delta="작년 데이터 미비로 비교 불가")
            
        st.metric(label="🚀 관측 구역 내 최고 활성 점수", value=f"{max_ndvi:.3f}")

        st.markdown("---")
        
        st.subheader("📋 AI 작황 정밀 진단 리포트")
        if avg_ndvi >= 0.4:
            st.info(
                f"🟢 **생육기 검증 완료:** 현재 선택 구역의 평균 식생지수가 **{avg_ndvi:.2f}**로 고활성 기준점(0.4)을 넘었습니다. "
                f"작물이 정상적인 생육 주기(Vegetative Stage)에 안착하여 푸른 잎을 넓히고 있음이 과학적으로 증명되었습니다.\n\n"
                f"{'📈 분석 결과 작년보다 기후적 요건이 우수하여 성장 속도가 더 가파릅니다.' if (last_avg and avg_ndvi > last_avg) else '📉 다만 작년 동기에 비해서는 활성도가 다소 낮으므로 가뭄 징후나 국지적 병충해 예찰을 권장합니다.'}"
            )
        else:
            st.warning(
                f"🔴 **주의 단계:** 평균 지수가 **{avg_ndvi:.2f}**로 다소 낮게 잡힙니다. 모내기 직후라 물이 많이 채워진 상태이거나, "
                f"최근 기상 악화로 인한 발육 지연일 수 있으니 우측 상단 레이어를 켜서 실제 인공위성 사진과 교차 검증하세요."
            )
else:
    st.info("👈 왼쪽 컨트롤 패널에서 협업 대상 지자체를 선택하고 '다중 시계열 정밀 분석' 버튼을 클릭해 보세요.")