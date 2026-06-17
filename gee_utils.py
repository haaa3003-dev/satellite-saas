# gee_utils.py
import streamlit as st
import ee

@st.cache_resource
def init_gee():
    """구글 어스 엔진 인증 및 초기화"""
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

def get_satellite_index_for_period(region, start_date, end_date, cloud_threshold, bands, index_name):
    """지정 기간/구역 내 Sentinel-2 위성 지수 및 통계량 산출"""
    collection = (
        ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
        .filterBounds(region)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_threshold))
    )
    count = collection.size().getInfo()
    if count == 0:
        return None, None, 0, None
        
    image = collection.median()
    calculated_index = image.normalizedDifference(bands).rename(index_name)
    
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
        
    return image, calculated_index, count, stats

def get_ee_tile_url(ee_image_object, vis_params):
    """GEE 지도 레이어 타일 URL 반환"""
    map_id_dict = ee.Image(ee_image_object).getMapId(vis_params)
    return map_id_dict['tile_fetcher'].url_format