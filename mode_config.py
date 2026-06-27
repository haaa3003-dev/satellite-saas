# mode_config.py
"""
위성 모드 설정.

변경 사항:
- native_resolution_m 필드 추가 (기존에는 scale=10이 gee_utils에 하드코딩돼 있어
  S5P/Landsat 모드에서 잘못된 해상도로 reduceRegion이 실행됐다).
- 각 모드의 해상도를 GEE 공식 카탈로그 기준으로 명시.
- BUFFER_M, GEE_PROJECT_ID 같은 전역 상수도 여기서 한 곳에 관리한다.
"""

# ── 전역 상수 ─────────────────────────────────────────────────────────────────
# 기존: 3000이 app.py 안에 리터럴로 4번 반복됐다.
ANALYSIS_BUFFER_M: int = 3000
GEE_PROJECT_ID: str = "knut-startup-gee"

# ── 모드 설정 딕셔너리 ────────────────────────────────────────────────────────
mode_config: dict[str, dict] = {
    "🌾 농작물 생육 분석 (NDVI)": {
        "collection": "COPERNICUS/S2_SR_HARMONIZED",
        "calc_type": "normalized_diff",
        "cloud_filter_prop": "CLOUDY_PIXEL_PERCENTAGE",
        "bands": ["B8", "B4"],
        "index_name": "NDVI",
        "label": "식생활성도",
        # [수정] Sentinel-2 네이티브 해상도 10m. 기존 scale=10 하드코딩을 대체.
        "native_resolution_m": 10,
        "palette": ["red", "yellow", "green"],
        "min": 0.0, "max": 1.0,
        "anomaly_min": -0.3, "anomaly_max": 0.3,
        "threshold": 0.4, "baseline": 0.15, "ceil": 0.85,
        "higher_is_worse": False,
        "desc_good": (
            "지표면의 식생 활성도가 기준치(0.4)를 상회합니다. "
            "작물이 정상적인 성장 주기에 안착하여 활발히 생육 중임이 증명되었습니다."
        ),
        "desc_bad": (
            "식생지수가 다소 낮게 모니터링됩니다. "
            "초기 파종/모내기로 인한 수면 노출이거나 가뭄, "
            "병해충으로 인한 생육 지연일 가능성이 있습니다."
        ),
    },
    "🌊 저수지 및 홍수 모니터링 (NDWI)": {
        "collection": "COPERNICUS/S2_SR_HARMONIZED",
        "calc_type": "normalized_diff",
        "cloud_filter_prop": "CLOUDY_PIXEL_PERCENTAGE",
        "bands": ["B3", "B8"],
        "index_name": "NDWI",
        "label": "수분포화도",
        "native_resolution_m": 10,
        "palette": ["white", "#99ccff", "blue"],
        "min": -0.5, "max": 0.8,
        "anomaly_min": -0.4, "anomaly_max": 0.4,
        "threshold": 0.1, "baseline": -0.40, "ceil": 0.75,
        "higher_is_worse": False,
        "desc_good": (
            "해당 구역의 수분포화도가 높습니다. "
            "대형 저수지의 저수율이 풍부하거나 호우로 인한 "
            "지표면 침수 및 하천 범람 구역일 수 있습니다."
        ),
        "desc_bad": (
            "수분포화도가 마이너스권을 기록합니다. "
            "수자원이 고갈되어 가뭄 징후가 보이거나 "
            "건조한 나대지 상태를 나타냅니다."
        ),
    },
    "🔥 산불 재해 및 산림 진단 (NBR)": {
        "collection": "COPERNICUS/S2_SR_HARMONIZED",
        "calc_type": "normalized_diff",
        "cloud_filter_prop": "CLOUDY_PIXEL_PERCENTAGE",
        "bands": ["B8", "B12"],
        "index_name": "NBR",
        "label": "탄화흔적도",
        "native_resolution_m": 10,
        "palette": ["#331a00", "yellow", "darkgreen"],
        "min": -0.4, "max": 0.8,
        "anomaly_min": -0.5, "anomaly_max": 0.5,
        "threshold": 0.15, "baseline": -0.30, "ceil": 0.80,
        "higher_is_worse": False,
        "desc_good": (
            "탄화흔적이 없는 깨끗하고 푸른 산림 상태를 나타냅니다. "
            "산림 자원의 건강성이 아주 우수하게 유지되고 있습니다."
        ),
        "desc_bad": (
            "지수가 급격한 마이너스로 추락했습니다. "
            "최근 산불 재해로 인해 지표면이 까맣게 타버린 '탄화 흔적지'이거나 "
            "급격한 산림 훼손 구역입니다."
        ),
    },
    "🏭 미세먼지 및 대기오염 지도 (NO2)": {
        "collection": "COPERNICUS/S5P/OFFL/L3_NO2",
        "calc_type": "single_band",
        "cloud_filter_prop": None,  # S5P L3은 씬 단위 구름 필터 없음
        "band": "tropospheric_NO2_column_number_density",
        "index_name": "NO2",
        "label": "이산화질소 밀도",
        # [수정] S5P 네이티브 해상도 5500m. 기존 scale=10은 잘못된 값이었다.
        "native_resolution_m": 5500,
        "palette": ["black", "blue", "purple", "cyan", "green", "yellow", "red"],
        "min": 0.0, "max": 0.0002,  # mol/m², GEE 공식 시각화 범위
        "threshold": 0.00007,       # [추정치] 공식 규제 기준 아님, 검증 필요
        "higher_is_worse": True,
        "desc_good": (
            "대기 오염 물질 농도가 낮아 광역적인 공기질 트렌드가 양호한 상태입니다. "
            "(해상도: 약 3.5~7km 단위 관측이라 필지 단위가 아닌 광역 트렌드용입니다)"
        ),
        "desc_bad": (
            "이산화질소 등 오염 물질 농도가 높게 관측됩니다. "
            "특정 필지가 아닌 해당 지역 전체의 광역 대기질 악화를 의미합니다. "
            "(해상도: 약 3.5~7km 단위 관측)"
        ),
    },
    "♨️ 도심 폭염 및 열섬 현상 분석 (LST)": {
        "collection": "LANDSAT/LC08/C02/T1_L2",
        "calc_type": "thermal_celsius",
        "cloud_filter_prop": "CLOUD_COVER",
        "band": "ST_B10",
        "index_name": "LST",
        "label": "지표면 온도",
        # [수정] Landsat 열적외선 밴드 네이티브 해상도 30m.
        "native_resolution_m": 30,
        "palette": [
            "#042333", "#2c3359", "#4d3d75", "#76448a", "#a4468f",
            "#cf4c7e", "#eb6361", "#f78b40", "#f4b925", "#e9f00a",
        ],
        "min": 20.0, "max": 45.0,   # 섭씨, thermal_celsius 변환 후 기준
        "threshold": 35.0,           # [추정치] 공식 폭염 기준 아님, 검증 필요
        "higher_is_worse": True,
        "desc_good": (
            "지표면 온도가 정상 범위 내에 있어 열섬 현상이 덜합니다. "
            "(해상도: 약 30m 단위 관측)"
        ),
        "desc_bad": (
            "국지적인 지표면 온도가 매우 높게 관측됩니다. "
            "빌딩 밀집 구역이나 아스팔트로 인한 도심 열섬 현상에 주의가 필요합니다. "
            "(해상도: 약 30m 단위 관측)"
        ),
    },
}

# ── 프리셋 좌표 ────────────────────────────────────────────────────────────────
preset_coords: dict[str, tuple[float, float]] = {
    "🌾 [농업] 전북 김제시 부량면 (벽골제 평야)": (35.7684, 126.8643),
    "🌾 [농업] 충남 당진시 합덕읍 (당진평야)":   (36.8250, 126.7720),
    "🌾 [농업] 전남 해남군 황산면 (대규모 필드)": (34.6150, 126.4780),
    "🌊 [수자원] 충북 충주시 종민동 (충주호 저수지)": (36.9910, 127.9259),
    "🌊 [수자원] 강원 춘천시 신북읍 (소양강댐 부근)": (37.9425, 127.8140),
    "🔥 [재해] 강원 고성군 토성면 (산불 취약 산림지)": (38.2250, 128.5110),
    "🔥 [재해] 경북 안동시 풍천면 (산림 보존 구역)":   (36.5750, 128.5210),
    "🏙️ [도시] 서울 뚝섬 한강공원 (도심 녹지축)":     (37.5285, 127.0675),
    "🏭 [대기질] 경북 포항시 남구 (산업단지 인근)":    (36.0190, 129.3435),
    "♨️ [열섬] 서울 중구 (도심 빌딩 밀집 지역)":      (37.5665, 126.9780),
    "📍 직접 좌표 입력": (36.9910, 127.9259),
}
