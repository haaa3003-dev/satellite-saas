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

def _build_index_image(region, start_date, end_date, cloud_threshold, mode_cfg):
    """공통 합성 이미지 및 지수 빌더 (ee 객체 반환, lazy 연산이라 가벼움)

    mode_cfg에 담긴 calc_type에 따라 계산 방식을 분기한다.
    - normalized_diff : 두 밴드의 정규화 차이 (NDVI/NDWI/NBR 등, Sentinel-2)
    - single_band      : 단일 밴드 값을 그대로 사용 (예: 대기오염 농도, Sentinel-5P)
    - thermal_celsius  : Landsat Collection 2 열적외선 밴드를 켈빈→섭씨로 변환
                          (DN * 0.00341802 + 149.0 → 켈빈, 거기서 -273.15)
    """
    collection_id = mode_cfg['collection']
    cloud_filter_prop = mode_cfg.get('cloud_filter_prop')
    calc_type = mode_cfg['calc_type']
    index_name = mode_cfg['index_name']

    collection = (
        ee.ImageCollection(collection_id)
        .filterBounds(region)
        .filterDate(start_date, end_date)
    )
    # 컬렉션에 따라 구름 필터 속성명이 다르거나(CLOUDY_PIXEL_PERCENTAGE vs
    # CLOUD_COVER) 아예 없을 수 있어서(Sentinel-5P 대기 데이터 등) 선택적으로 적용
    if cloud_filter_prop:
        collection = collection.filter(ee.Filter.lt(cloud_filter_prop, cloud_threshold))

    image = collection.median()

    if calc_type == "normalized_diff":
        calculated_index = image.normalizedDifference(mode_cfg['bands']).rename(index_name)
    elif calc_type == "single_band":
        calculated_index = image.select(mode_cfg['band']).rename(index_name)
    elif calc_type == "thermal_celsius":
        calculated_index = (
            image.select(mode_cfg['band'])
            .multiply(0.00341802)
            .add(149.0)
            .subtract(273.15)
            .rename(index_name)
        )
    else:
        raise ValueError(f"알 수 없는 calc_type: {calc_type}")

    return collection, image, calculated_index


@st.cache_data(ttl=3600, show_spinner=False)
def get_cached_stats(lat, lon, buffer_m, start_date, end_date, cloud_threshold, mode_cfg):
    """위경도/기간/구름기준/모드가 동일하면 GEE 서버 재호출 없이 캐시된 통계 반환.
    실제 쿼터/네트워크 비용이 드는 .getInfo() 호출 부분만 캐싱 대상으로 분리했다.
    (ee.Image 자체는 직렬화가 안 돼서 캐싱 불가 — count/stats 같은 순수 값만 반환)
    """
    region = ee.Geometry.Point([lon, lat]).buffer(buffer_m)
    collection, _, calculated_index = _build_index_image(region, start_date, end_date, cloud_threshold, mode_cfg)

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


def get_satellite_index_for_period(region, start_date, end_date, cloud_threshold, mode_cfg):
    """타일 렌더링용 ee 이미지/인덱스 객체 반환 (캐싱 불가, 매번 새로 빌드)"""
    _, image, calculated_index = _build_index_image(region, start_date, end_date, cloud_threshold, mode_cfg)
    return image, calculated_index

def get_ee_tile_url(ee_image_object, vis_params):
    """GEE 지도 레이어 타일 URL 반환"""
    map_id_dict = ee.Image(ee_image_object).getMapId(vis_params)
    return map_id_dict['tile_fetcher'].url_format