# gee_utils.py
import streamlit as st
import ee

@st.cache_resource
def init_gee():
    """구글 어스 엔진 인증 및 초기화"""
    try:
        has_cloud_secrets = "gee_credentials" in st.secrets
    except Exception:
        # secrets.toml 파일이 없는 로컬 환경 (Streamlit Cloud에만 등록된 경우 등)
        has_cloud_secrets = False

    if has_cloud_secrets:
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

def _build_index_image(region, start_date, end_date, cloud_threshold, bands, index_name):
    """공통 합성 이미지 및 지수 빌더 (ee 객체 반환, lazy 연산이라 가벼움)"""
    collection = (
        ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
        .filterBounds(region)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_threshold))
    )
    image = collection.median()
    calculated_index = image.normalizedDifference(bands).rename(index_name)
    return collection, image, calculated_index


@st.cache_data(ttl=3600, show_spinner=False)
def get_cached_stats(lat, lon, buffer_m, start_date, end_date, cloud_threshold, bands, index_name):
    """위경도/기간/구름기준이 동일하면 GEE 서버 재호출 없이 캐시된 통계 반환.
    실제 쿼터/네트워크 비용이 드는 .getInfo() 호출 부분만 캐싱 대상으로 분리했다.
    (ee.Image 자체는 직렬화가 안 돼서 캐싱 불가 — count/stats 같은 순수 값만 반환)
    """
    region = ee.Geometry.Point([lon, lat]).buffer(buffer_m)
    collection, _, calculated_index = _build_index_image(region, start_date, end_date, cloud_threshold, bands, index_name)

    count = collection.size().getInfo()
    if count == 0:
        return 0, None

    try:
        stats = calculated_index.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.max(), sharedInputs=True),
            geometry=region,
            scale=10
        ).getInfo()
        if stats is None:
            stats = {}
    except Exception:
        stats = {}

    return count, stats


def get_satellite_index_for_period(region, start_date, end_date, cloud_threshold, bands, index_name):
    """타일 렌더링용 ee 이미지/인덱스 객체 반환 (캐싱 불가, 매번 새로 빌드)"""
    _, image, calculated_index = _build_index_image(region, start_date, end_date, cloud_threshold, bands, index_name)
    return image, calculated_index

def get_ee_tile_url(ee_image_object, vis_params):
    """GEE 지도 레이어 타일 URL 반환"""
    map_id_dict = ee.Image(ee_image_object).getMapId(vis_params)
    return map_id_dict['tile_fetcher'].url_format