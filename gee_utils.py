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

def _compute_index_from_image(image, mode_cfg):
    """단일 ee.Image 한 장에서 지수를 계산하는 공통 로직.

    median 합성 이미지든, 시계열의 개별 이미지 한 장이든 항상 이 함수를
    거치게 해서 "합성 결과"와 "시계열 점 하나하나"의 계산식이 어긋나지
    않도록 보장한다 (같은 곳에서 같은 공식을 쓰는 게 원칙).
    """
    calc_type = mode_cfg['calc_type']
    index_name = mode_cfg['index_name']

    if calc_type == "normalized_diff":
        return image.normalizedDifference(mode_cfg['bands']).rename(index_name)
    elif calc_type == "single_band":
        return image.select(mode_cfg['band']).rename(index_name)
    elif calc_type == "thermal_celsius":
        return (
            image.select(mode_cfg['band'])
            .multiply(0.00341802)
            .add(149.0)
            .subtract(273.15)
            .rename(index_name)
        )
    else:
        raise ValueError(f"알 수 없는 calc_type: {calc_type}")


def _filtered_collection(region, start_date, end_date, cloud_threshold, mode_cfg):
    """공통 컬렉션 필터링 (위치/기간/구름기준)"""
    collection_id = mode_cfg['collection']
    cloud_filter_prop = mode_cfg.get('cloud_filter_prop')

    collection = (
        ee.ImageCollection(collection_id)
        .filterBounds(region)
        .filterDate(start_date, end_date)
    )
    # 컬렉션에 따라 구름 필터 속성명이 다르거나(CLOUDY_PIXEL_PERCENTAGE vs
    # CLOUD_COVER) 아예 없을 수 있어서(Sentinel-5P 대기 데이터 등) 선택적으로 적용
    if cloud_filter_prop:
        collection = collection.filter(ee.Filter.lt(cloud_filter_prop, cloud_threshold))
    return collection


def _build_index_image(region, start_date, end_date, cloud_threshold, mode_cfg):
    """공통 합성 이미지 및 지수 빌더 (ee 객체 반환, lazy 연산이라 가벼움)

    mode_cfg에 담긴 calc_type에 따라 계산 방식을 분기한다.
    - normalized_diff : 두 밴드의 정규화 차이 (NDVI/NDWI/NBR 등, Sentinel-2)
    - single_band      : 단일 밴드 값을 그대로 사용 (예: 대기오염 농도, Sentinel-5P)
    - thermal_celsius  : Landsat Collection 2 열적외선 밴드를 켈빈→섭씨로 변환
                          (DN * 0.00341802 + 149.0 → 켈빈, 거기서 -273.15)
    """
    collection = _filtered_collection(region, start_date, end_date, cloud_threshold, mode_cfg)
    image = collection.median()
    calculated_index = _compute_index_from_image(image, mode_cfg)
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
        # [확장] mean만이 아니라 min/max/stdDev까지 한 번의 reduceRegion 호출로 가져온다.
        # 같은 평균이라도 표준편차가 크면 "필지 일부에 국지적 문제 발생 가능성"처럼
        # 더 구체적인 해석이 가능해진다. (호출 횟수는 그대로 1번 — 비용 증가 없음)
        combined_reducer = (
            ee.Reducer.mean()
            .combine(ee.Reducer.minMax(), sharedInputs=True)
            .combine(ee.Reducer.stdDev(), sharedInputs=True)
        )
        stats = calculated_index.reduceRegion(
            reducer=combined_reducer,
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


@st.cache_data(ttl=3600, show_spinner=False)
def get_time_series(lat, lon, buffer_m, start_date, end_date, cloud_threshold, mode_cfg):
    """선택 기간 내 개별 위성 촬영분마다 (날짜, 평균값)을 계산해 반환한다.

    [중요] 이건 예측이 아니라 100% 실측 시계열이다. median()으로 기간을
    하나로 뭉개는 대신, 컬렉션 안의 이미지 한 장 한 장에 대해
    날짜와 평균값을 서버에서 미리 계산(.map())해서, getInfo() 호출을
    딱 한 번만 날린다 (이미지 개수만큼 왕복하지 않음 — 비용 절약).
    """
    region = ee.Geometry.Point([lon, lat]).buffer(buffer_m)
    collection = _filtered_collection(region, start_date, end_date, cloud_threshold, mode_cfg)
    index_name = mode_cfg['index_name']

    def _reduce_single(image):
        idx_img = _compute_index_from_image(image, mode_cfg)
        mean_val = idx_img.reduceRegion(
            reducer=ee.Reducer.mean(), geometry=region, scale=10
        ).get(index_name)
        return ee.Feature(None, {
            'date': image.date().format('YYYY-MM-dd'),
            'value': mean_val
        })

    try:
        features = collection.map(_reduce_single).getInfo().get('features', [])
    except Exception:
        return []

    series = []
    for f in features:
        props = f.get('properties', {})
        val = props.get('value')
        date_str = props.get('date')
        if val is not None and date_str is not None and isinstance(val, (int, float)):
            series.append((date_str, val))

    # [개선] 같은 날짜에 인접 궤도 중복 촬영이 잡히는 경우가 있어
    # (예: Sentinel-2가 같은 날 다른 궤도에서 두 번 지나가는 경우),
    # 날짜별로 평균을 내서 날짜당 점 하나씩만 남긴다.
    by_date = {}
    for date_str, val in series:
        by_date.setdefault(date_str, []).append(val)

    merged = [
        (date_str, sum(vals) / len(vals))
        for date_str, vals in by_date.items()
    ]
    merged.sort(key=lambda x: x[0])
    return merged


def get_ee_tile_url(ee_image_object, vis_params):
    """GEE 지도 레이어 타일 URL 반환"""
    map_id_dict = ee.Image(ee_image_object).getMapId(vis_params)
    return map_id_dict['tile_fetcher'].url_format