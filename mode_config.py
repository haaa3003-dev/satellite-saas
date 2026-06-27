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

    # ── [신규] NDRE ──────────────────────────────────────────────────────────────
    # Sentinel-2 Red Edge 밴드(B5)와 근적외선(B8A)을 이용한 엽록소 농도 지수.
    # NDVI보다 질소 결핍·병해충 스트레스에 더 민감하게 반응한다.
    # 공식: (B8A - B5) / (B8A + B5)
    # 참고: GEE 카탈로그 COPERNICUS/S2_SR_HARMONIZED
    #   B5 = Red Edge 1 (705nm, 20m), B8A = Red Edge 4 (865nm, 20m)
    # [주의] threshold=0.35는 추정치. 한국 농경지 실측 데이터로 검증 필요.
    "🌿 엽록소 농도 및 병해충 조기탐지 (NDRE)": {
        "collection": "COPERNICUS/S2_SR_HARMONIZED",
        "calc_type": "normalized_diff",
        "cloud_filter_prop": "CLOUDY_PIXEL_PERCENTAGE",
        "bands": ["B8A", "B5"],
        "index_name": "NDRE",
        "label": "엽록소 활성도",
        "native_resolution_m": 20,  # B5, B8A 모두 20m 밴드
        "palette": ["#ffffcc", "#78c679", "#238443", "#005a32"],
        "min": -0.1, "max": 0.7,
        "anomaly_min": -0.2, "anomaly_max": 0.2,
        "threshold": 0.35,   # [추정치] 검증 필요
        "baseline": 0.1, "ceil": 0.65,
        "higher_is_worse": False,
        "desc_good": (
            "엽록소 활성도가 양호한 범위입니다. "
            "작물의 질소 함유량이 충분하고 광합성이 활발히 이루어지고 있을 가능성이 높습니다. "
            "(NDVI보다 병해충·영양 결핍에 더 민감한 지표입니다)"
        ),
        "desc_bad": (
            "엽록소 활성도가 낮게 관측됩니다. "
            "질소 결핍, 병해충 피해, 또는 초기 생육 부진일 가능성이 있습니다. "
            "NDVI와 함께 교차 확인하면 원인 구분에 도움이 됩니다. "
            "(해상도: 약 20m 단위 관측)"
        ),
    },

    # ── [신규] NDBI ──────────────────────────────────────────────────────────────
    # 단파적외선(B11)과 근적외선(B8)을 이용한 건물/도로 등 불투수면 탐지 지수.
    # 공식: (B11 - B8) / (B11 + B8)
    # 값이 높을수록 건물·아스팔트 등 인공 구조물 밀도가 높음을 의미한다.
    # 도시 확장 모니터링, 녹지 잠식 탐지에 활용.
    # 참고: GEE 카탈로그 COPERNICUS/S2_SR_HARMONIZED
    #   B8 = NIR (842nm, 10m), B11 = SWIR 1 (1610nm, 20m)
    # [주의] GEE에서 B8(10m)과 B11(20m)을 normalizedDifference로 계산할 때
    #   GEE가 자동으로 낮은 해상도(20m) 기준으로 리샘플링하므로 scale=20 사용.
    # [주의] threshold=0.0은 추정치. 도심/농촌 혼재 지역에서 검증 필요.
    "🏗️ 도시 확장 및 불투수면 탐지 (NDBI)": {
        "collection": "COPERNICUS/S2_SR_HARMONIZED",
        "calc_type": "normalized_diff",
        "cloud_filter_prop": "CLOUDY_PIXEL_PERCENTAGE",
        "bands": ["B11", "B8"],
        "index_name": "NDBI",
        "label": "불투수면 밀도",
        "native_resolution_m": 20,  # B11이 20m 밴드이므로 20m 기준
        "palette": ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
        "min": -0.5, "max": 0.5,
        "anomaly_min": -0.3, "anomaly_max": 0.3,
        "threshold": 0.0,    # [추정치] 0 이상이면 불투수면 우세로 해석
        "baseline": -0.3, "ceil": 0.4,
        "higher_is_worse": True,  # 높을수록 불투수면(건물/도로) 밀도 높음 = 녹지 감소
        "desc_good": (
            "불투수면 비율이 낮아 녹지·농경지 비중이 우세한 구역입니다. "
            "도시 열섬 현상이 상대적으로 적을 가능성이 있습니다."
        ),
        "desc_bad": (
            "불투수면 비율이 높게 관측됩니다. "
            "건물·도로·아스팔트 등 인공 구조물이 밀집한 구역으로 추정됩니다. "
            "LST(지표면 온도)와 함께 보면 도심 열섬 강도를 더 정밀하게 판단할 수 있습니다. "
            "(해상도: 약 20m 단위 관측)"
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
    # [신규] NDRE 검증용 — 고품질 쌀 산지, 병해충 모니터링 수요 높은 지역
    "🌿 [NDRE] 전북 익산시 함열읍 (논농업 집중 지역)": (35.9680, 126.9740),
    "🌿 [NDRE] 경기 이천시 부발읍 (이천쌀 주산지)":   (37.2890, 127.4420),
    # [신규] NDBI 검증용 — 도시 확장/개발 압력이 뚜렷한 지역
    "🏗️ [NDBI] 경기 화성시 동탄2신도시 (신개발지)":  (37.2060, 127.0900),
    "🏗️ [NDBI] 인천 송도국제도시 (매립·개발 구역)":  (37.3830, 126.6560),
    "📍 직접 좌표 입력": (36.9910, 127.9259),
}
