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
GEE_PROJECT_ID: str = "knut-startup-gee"

# ── 도메인 설정 ───────────────────────────────────────────────────────────────
# 사용자는 먼저 도메인(분석 목적)을 선택하고, 그 안에서 모드를 고른다.
# 새 도메인을 추가하려면 이 딕셔너리에 항목만 추가하면 된다 — app.py 수정 불필요.
#
# 각 도메인:
#   - modes: 이 도메인에서 보여줄 모드 키 목록 (mode_config의 키와 일치해야 함)
#   - tabs:  이 도메인에서 활성화할 분석 탭 목록
#   - description: 도메인 한 줄 설명
#
# 사용 가능한 탭 식별자: "기본 분석", "변화 탐지", "계절 트렌드", "핫스팟", "다중 지점 비교"
domain_config: dict[str, dict] = {
    "🌾 농업 모니터링": {
        "description": "작물 생육·병해충·논 수분 상태를 관측합니다.",
        "modes": [
            "🌾 농작물 생육 분석 (NDVI)",
            "🌿 엽록소 농도 및 병해충 조기탐지 (NDRE)",
            "🌊 저수지 및 홍수 모니터링 (NDWI)",
        ],
        "tabs": ["📊 기본 분석", "📅 계절 트렌드", "📍 다중 지점 비교"],
    },
    "🌊 재해·수자원": {
        "description": "침수·산불·토양수분 등 재해 상황을 탐지합니다.",
        "modes": [
            "🌊 저수지 및 홍수 모니터링 (NDWI)",
            "🌧️ 토양수분 및 침수 탐지 SAR (VV)",
            "🌲 산림·작물 구조 탐지 SAR (VH)",
            "🔥 산불 재해 및 산림 진단 (NBR)",
        ],
        "tabs": ["📊 기본 분석", "🔄 변화 탐지"],
    },
    "🏙️ 도시·환경": {
        "description": "도심 열섬·불투수면·대기질을 분석합니다.",
        "modes": [
            "♨️ 도심 폭염 및 열섬 현상 분석 (LST)",
            "🏗️ 도시 확장 및 불투수면 탐지 (NDBI)",
            "🏭 미세먼지 및 대기오염 지도 (NO2)",
        ],
        "tabs": ["📊 기본 분석", "🎯 핫스팟", "🔄 변화 탐지"],
    },
}

# ── 모드 설정 딕셔너리 ────────────────────────────────────────────────────────
mode_config: dict[str, dict] = {
    "🌾 농작물 생육 분석 (NDVI)": {
        "collection": "COPERNICUS/S2_SR_HARMONIZED",
        "calc_type": "normalized_diff",
        "cloud_filter_prop": "CLOUDY_PIXEL_PERCENTAGE",
        "bands": ["B8", "B4"],
        "index_name": "NDVI",
        "label": "식생활성도",
        "native_resolution_m": 10,
        # NASA 표준 — 베이지(식생 없음) → 연초록 → 진초록(울창)
        "palette": ["#ffffcc", "#d9f0a3", "#addd8e", "#78c679", "#41ab5d", "#238443", "#005a32"],
        "landcover_mask": [30, 40, 10, 20],  # ESA WorldCover: 초지, 경작지, 수목, 관목
        "dw_mask": [4, 2, 1],                # Dynamic World: 농경지, 초지, 수목
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
        # Sentinel Hub 표준 — 베이지(건조) → 하늘 → 진파랑(수체)
        "palette": ["#ffffcc", "#c7e9b4", "#7fcdbb", "#41b6c4", "#1d91c0", "#225ea8", "#0c2c84"],
        "landcover_mask": [80, 40, 30],  # ESA WorldCover: 수체, 경작지, 초지
        "dw_mask": [0, 4, 2],            # Dynamic World: 수체, 농경지, 초지
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
        # 발산형 — 초록(정상 산림) → 노랑(경계) → 빨강(심각 피해)
        "palette": ["#1a9850", "#91cf60", "#d9ef8b", "#ffffbf", "#fee08b", "#fc8d59", "#d73027"],
        "landcover_mask": [10, 20],  # ESA WorldCover: 수목, 관목
        "dw_mask": [1, 2],           # Dynamic World: 수목, 초지(관목 포함)
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
        # 흰색(청정) → 연보라 → 진보라(오염 심각) · 대기질 국제 표준
        "palette": ["#f7f7f7", "#d9d9d9", "#bababa", "#c994c7", "#df65b0", "#980043", "#67001f"],
        "landcover_mask": None,  # NO2는 광역 대기 데이터, 마스킹 불필요
        "dw_mask": None,
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
        # 기상 국제 표준 — 파랑(저온·녹지) → 노랑 → 빨강(고온·도심)
        "palette": ["#4575b4", "#91bfdb", "#e0f3f8", "#ffffbf", "#fee090", "#fc8d59", "#d73027"],
        "landcover_mask": [50, 40, 30],  # ESA WorldCover: 도시, 경작지, 초지
        "dw_mask": [6, 4, 2],            # Dynamic World: 건물, 농경지, 초지
        "min": 20.0, "max": 45.0,
        "threshold": 35.0,
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
        "native_resolution_m": 20,
        # NASA 표준 확장 — 베이지(엽록소 없음) → 연초록 → 진초록(엽록소 풍부)
        "palette": ["#ffffcc", "#d9f0a3", "#addd8e", "#78c679", "#41ab5d", "#238443", "#005a32"],
        "landcover_mask": [30, 40],  # ESA WorldCover: 초지, 경작지
        "dw_mask": [4, 2],           # Dynamic World: 농경지, 초지
        "min": -0.1, "max": 0.7,
        "anomaly_min": -0.2, "anomaly_max": 0.2,
        "threshold": 0.35,
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
        "native_resolution_m": 20,
        # 노랑(녹지) → 주황 → 진빨강(불투수면 밀집) · 도시 확장 표준
        "palette": ["#ffffb2", "#fed976", "#feb24c", "#fd8d3c", "#fc4e2a", "#e31a1c", "#b10026"],
        "landcover_mask": [50],  # ESA WorldCover: 도시/건물
        "dw_mask": [6],          # Dynamic World: 건물
        "min": -0.5, "max": 0.5,
        "anomaly_min": -0.3, "anomaly_max": 0.3,
        "threshold": 0.0,
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

    # ── [신규] SAR VV — 토양수분 / 침수 탐지 ────────────────────────────────────
    # Sentinel-1 C-밴드 SAR(합성개구레이더) GRD 데이터.
    # 구름·야간 관계없이 촬영 가능 — 장마·태풍 시에도 데이터 수집된다.
    #
    # VV(수직-수직 편파):
    #   - 지표면 거칠기, 토양수분, 침수 여부에 민감
    #   - 논밭 침수, 홍수 범람 탐지에 주로 활용
    #   - 값이 낮을수록(dB 음수 큰값) 수면/침수 가능성 높음
    #
    # 단위: dB (데시벨). 일반 육지: -15 ~ -5 dB, 수면: -25 ~ -15 dB
    # 참고: GEE 카탈로그 COPERNICUS/S1_GRD
    # [주의] threshold=-15.0은 추정치. 지역·계절별 검증 필요.
    "🌧️ 토양수분 및 침수 탐지 SAR (VV)": {
        "collection": "COPERNICUS/S1_GRD",
        "calc_type": "sar_backscatter",
        "cloud_filter_prop": None,   # SAR은 구름 영향 없음
        "band": "VV",
        "orbit_pass": "DESCENDING",  # 한국 기준 야간 하강 궤도, 더 안정적
        "index_name": "SAR_VV",
        "label": "후방산란계수 VV",
        "native_resolution_m": 10,
        # 진청보라(수분 높음·침수) → 베이지 → 노랑(건조) · 레이더 수분 표준
        "palette": ["#0d0887", "#5302a3", "#8b0aa5", "#cb4679", "#f48849", "#fdc527", "#f0f921"],
        "landcover_mask": [40, 30, 80],  # ESA WorldCover: 경작지, 초지, 수체
        "dw_mask": [4, 2, 0],            # Dynamic World: 농경지, 초지, 수체
        "min": -25.0, "max": -5.0,
        "anomaly_min": -5.0, "anomaly_max": 5.0,
        "threshold": -15.0,
        "baseline": -20.0, "ceil": -8.0,
        "higher_is_worse": False,
        "desc_good": (
            "후방산란계수가 정상 범위입니다. "
            "지표면이 건조하거나 식생이 우세한 상태로 추정됩니다. "
            "구름·야간 관계없이 수집되는 레이더 데이터입니다. "
            "(해상도: 약 10m 단위 관측)"
        ),
        "desc_bad": (
            "후방산란계수가 낮게 관측됩니다. "
            "지표면 수분이 매우 높거나 침수 가능성이 있는 구역입니다. "
            "NDWI와 함께 교차 확인하면 침수 여부를 더 정밀하게 판단할 수 있습니다. "
            "장마·태풍 직후 침수 탐지에 특히 유효합니다. "
            "(해상도: 약 10m 단위 관측)"
        ),
    },

    # ── [신규] SAR VH — 산림/작물 구조 탐지 ─────────────────────────────────────
    # VH(수직-수평 편파):
    #   - 식생 내부 구조(잎·줄기 체적)에 민감
    #   - 벼 생육 단계, 산림 바이오매스 추정에 활용
    #   - VV 대비 식생 신호에 더 민감하게 반응
    #
    # [주의] threshold=-20.0은 추정치. 작물 종류별 검증 필요.
    "🌲 산림·작물 구조 탐지 SAR (VH)": {
        "collection": "COPERNICUS/S1_GRD",
        "calc_type": "sar_backscatter",
        "cloud_filter_prop": None,
        "band": "VH",
        "orbit_pass": "DESCENDING",
        "index_name": "SAR_VH",
        "label": "후방산란계수 VH",
        "native_resolution_m": 10,
        # 흰색(식생 없음) → 연초록 → 진초록(식생 풍부) · 식생 구조 표준
        "palette": ["#f7fcf5", "#e5f5e0", "#c7e9c0", "#a1d99b", "#74c476", "#31a354", "#006d2c"],
        "landcover_mask": [10, 20, 30, 40],  # ESA WorldCover: 수목, 관목, 초지, 경작지
        "dw_mask": [1, 2, 4],                # Dynamic World: 수목, 초지, 농경지
        "min": -30.0, "max": -5.0,
        "anomaly_min": -5.0, "anomaly_max": 5.0,
        "threshold": -20.0,
        "baseline": -25.0, "ceil": -8.0,
        "higher_is_worse": False,
        "desc_good": (
            "후방산란계수(VH)가 정상 범위입니다. "
            "식생 구조가 충분히 발달한 상태로 추정됩니다. "
            "벼 생육 중기~후기, 또는 산림 밀도가 높은 구역에서 높게 나타납니다. "
            "(해상도: 약 10m 단위 관측)"
        ),
        "desc_bad": (
            "후방산란계수(VH)가 낮게 관측됩니다. "
            "식생이 희박하거나 초기 생육 단계, 나대지·수면일 가능성이 있습니다. "
            "NDVI와 함께 비교하면 식생 상태를 광학·레이더 양쪽에서 교차 확인할 수 있습니다. "
            "(해상도: 약 10m 단위 관측)"
        ),
    },
}

# ── 도메인 설정 ───────────────────────────────────────────────────────────────
# 새 도메인 추가 시 이 딕셔너리에 항목 하나만 추가하면 된다.
# modes: mode_config의 키 목록
# tabs: 해당 도메인에서 보여줄 탭 이름 목록
# presets: 해당 도메인에 맞는 프리셋 키 목록 (preset_coords에서 필터링)
domain_config: dict[str, dict] = {
    "🌾 농업 모니터링": {
        "description": "작물 생육·병해충·수분 상태를 위성으로 모니터링합니다.",
        "modes": [
            "🌾 농작물 생육 분석 (NDVI)",
            "🌿 엽록소 농도 및 병해충 조기탐지 (NDRE)",
            "🌊 저수지 및 홍수 모니터링 (NDWI)",
        ],
        "tabs": ["📊 기본 분석", "📅 계절 트렌드", "📍 다중 지점 비교"],
        "preset_keys": [
            "🌾 전북 김제시 부량면 (벽골제 평야)",
            "🌾 충남 당진시 합덕읍 (당진평야)",
            "🌾 전남 해남군 황산면",
            "🌿 경기 이천시 부발읍 (이천쌀)",
            "🌿 전북 익산시 함열읍",
            "🌊 충북 충주시 충주호",
        ],
    },
    "🌊 재해·수자원": {
        "description": "침수·산불·토양수분을 레이더·광학 위성으로 탐지합니다.",
        "modes": [
            "🌊 저수지 및 홍수 모니터링 (NDWI)",
            "🌧️ 토양수분 및 침수 탐지 SAR (VV)",
            "🌲 산림·작물 구조 탐지 SAR (VH)",
            "🔥 산불 재해 및 산림 진단 (NBR)",
        ],
        "tabs": ["📊 기본 분석", "🔄 변화 탐지"],
        "preset_keys": [
            "🌊 충북 충주시 충주호",
            "🌊 강원 춘천시 소양강댐",
            "🔥 강원 고성군 토성면 (산불 취약)",
            "🔥 경북 울진군 (2022 대형산불)",
            "🌧️ 충남 논산시 강경읍 (금강 범람원)",
            "🌧️ 전남 나주시 다시면 (영산강)",
            "🌲 강원 홍천군 내면 (산림 밀집)",
        ],
    },
    "🏙️ 도시·환경": {
        "description": "열섬·불투수면·대기질로 도시 환경 변화를 분석합니다.",
        "modes": [
            "♨️ 도심 폭염 및 열섬 현상 분석 (LST)",
            "🏗️ 도시 확장 및 불투수면 탐지 (NDBI)",
            "🏭 미세먼지 및 대기오염 지도 (NO2)",
        ],
        "tabs": ["📊 기본 분석", "🎯 핫스팟", "🔄 변화 탐지"],
        "preset_keys": [
            "♨️ 서울 중구 (열섬 심각)",
            "♨️ 대구 중구 (전국 최고기온)",
            "🏙️ 서울 성동구 뚝섬",
            "🏗️ 경기 화성시 동탄2신도시",
            "🏗️ 인천 연수구 송도국제도시",
            "🏭 경북 포항시 남구 (산업단지)",
        ],
    },
}

# ── 프리셋 정의 ───────────────────────────────────────────────────────────────
# 기존 좌표(lat, lon) 방식 → 공공데이터 폴리곤 방식으로 전환
#
# 각 프리셋:
#   "type": "polygon"  → Vworld WFS 폴리곤 사용 (정밀 경계)
#   "type": "coord"    → 기존 좌표 방식 (Vworld API 키 없을 때 fallback)
#   "layer"            → Vworld WFS 레이어 이름
#   "filter"           → CQL 필터 (지역명·지목 등)
#   "coord"            → fallback 좌표 (west, south, east, north) bbox
#   "zoom"             → 지도 초기 줌 레벨

preset_config: dict[str, dict] = {

    # ── 농업 모니터링 ────────────────────────────────────────────────────────
    "🌾 전북 김제시 부량면 (벽골제 평야)": {
        "type": "polygon",
        "layer": "lt_c_adsigg_info",
        "filter": "sigg_nm='김제시'",
        "coord": (126.82, 35.74, 126.92, 35.80),
        "zoom": 12,
        "domain": "🌾 농업 모니터링",
        "desc": "전국 최대 평야 — 한국 쌀 주산지",
    },
    "🌾 충남 당진시 합덕읍 (당진평야)": {
        "type": "polygon",
        "layer": "lt_c_adsigg_info",
        "filter": "sigg_nm='당진시'",
        "coord": (126.72, 36.79, 126.82, 36.87),
        "zoom": 12,
        "domain": "🌾 농업 모니터링",
        "desc": "서해안 대규모 평야 농업지대",
    },
    "🌾 전남 해남군 황산면": {
        "type": "polygon",
        "layer": "lt_c_adsigg_info",
        "filter": "sigg_nm='해남군'",
        "coord": (126.38, 34.56, 126.52, 34.67),
        "zoom": 12,
        "domain": "🌾 농업 모니터링",
        "desc": "한반도 최남단 대규모 농경지",
    },
    "🌿 경기 이천시 부발읍 (이천쌀)": {
        "type": "polygon",
        "layer": "lt_c_adsigg_info",
        "filter": "sigg_nm='이천시'",
        "coord": (127.40, 37.26, 127.48, 37.32),
        "zoom": 13,
        "domain": "🌾 농업 모니터링",
        "desc": "이천쌀 주산지 — 병해충 조기탐지",
    },
    "🌿 전북 익산시 함열읍": {
        "type": "polygon",
        "layer": "lt_c_adsigg_info",
        "filter": "sigg_nm='익산시'",
        "coord": (126.93, 35.94, 127.01, 36.00),
        "zoom": 13,
        "domain": "🌾 농업 모니터링",
        "desc": "논농업 집중 — NDRE 검증 최적지",
    },

    # ── 재해·수자원 ──────────────────────────────────────────────────────────
    "🌊 충북 충주시 충주호": {
        "type": "polygon",
        "layer": "lt_c_waterarea",
        "filter": "river_nm LIKE '%충주%'",
        "coord": (127.85, 36.95, 128.02, 37.05),
        "zoom": 11,
        "domain": "🌊 재해·수자원",
        "desc": "충주댐 저수지 — 수위 변화 모니터링",
    },
    "🌊 강원 춘천시 소양강댐": {
        "type": "polygon",
        "layer": "lt_c_waterarea",
        "filter": "river_nm LIKE '%소양%'",
        "coord": (127.82, 37.88, 128.02, 38.05),
        "zoom": 11,
        "domain": "🌊 재해·수자원",
        "desc": "소양호 — 국내 최대 인공호수",
    },
    "🔥 강원 고성군 토성면 (산불 취약)": {
        "type": "polygon",
        "layer": "lt_c_forestmap",
        "filter": "sig_cd LIKE '42820%'",
        "coord": (128.46, 38.18, 128.56, 38.28),
        "zoom": 12,
        "domain": "🌊 재해·수자원",
        "desc": "동해안 산불 다발 — 산림청 고위험지역",
    },
    "🔥 경북 울진군 (2022 대형산불)": {
        "type": "polygon",
        "layer": "lt_c_forestmap",
        "filter": "sig_cd LIKE '47930%'",
        "coord": (129.28, 36.95, 129.48, 37.10),
        "zoom": 11,
        "domain": "🌊 재해·수자원",
        "desc": "2022년 역대 최대 산불 피해지역",
    },
    "🌧️ 충남 논산시 강경읍 (금강 범람원)": {
        "type": "polygon",
        "layer": "lt_c_waterarea",
        "filter": "river_nm LIKE '%금강%'",
        "coord": (126.73, 36.12, 126.83, 36.20),
        "zoom": 12,
        "domain": "🌊 재해·수자원",
        "desc": "금강 하류 침수 취약지 — SAR 검증",
    },
    "🌧️ 전남 나주시 다시면 (영산강)": {
        "type": "polygon",
        "layer": "lt_c_waterarea",
        "filter": "river_nm LIKE '%영산강%'",
        "coord": (126.68, 34.98, 126.78, 35.07),
        "zoom": 12,
        "domain": "🌊 재해·수자원",
        "desc": "영산강 유역 — 농경지 침수 탐지",
    },
    "🌲 강원 홍천군 내면 (산림 밀집)": {
        "type": "polygon",
        "layer": "lt_c_forestmap",
        "filter": "sig_cd LIKE '42720%'",
        "coord": (128.40, 37.62, 128.56, 37.74),
        "zoom": 12,
        "domain": "🌊 재해·수자원",
        "desc": "강원 내륙 산림 — SAR VH 검증",
    },

    # ── 도시·환경 ────────────────────────────────────────────────────────────
    "♨️ 서울 중구 (열섬 심각)": {
        "type": "polygon",
        "layer": "lt_c_adsigg_info",
        "filter": "full_nm:like:서울특별시 중구",
        "coord": (126.96, 37.55, 127.00, 37.57),
        "zoom": 14,
        "domain": "🏙️ 도시·환경",
        "desc": "서울 도심 — 전국 최고 열섬 강도",
    },
    "🏙️ 서울 성동구 뚝섬": {
        "type": "polygon",
        "layer": "lt_c_adsigg_info",
        "filter": "sigg_nm='성동구'",
        "coord": (127.04, 37.54, 127.08, 37.56),
        "zoom": 14,
        "domain": "🏙️ 도시·환경",
        "desc": "도심 녹지축 vs 열섬 비교",
    },
    "🏗️ 경기 화성시 동탄2신도시": {
        "type": "polygon",
        "layer": "lt_c_adsigg_info",
        "filter": "sigg_nm='화성시'",
        "coord": (127.06, 37.19, 127.12, 37.24),
        "zoom": 13,
        "domain": "🏙️ 도시·환경",
        "desc": "신도시 개발 — 불투수면 확장 관측",
    },
    "🏗️ 인천 연수구 송도국제도시": {
        "type": "polygon",
        "layer": "lt_c_adsigg_info",
        "filter": "sigg_nm='연수구'",
        "coord": (126.63, 37.37, 126.70, 37.42),
        "zoom": 13,
        "domain": "🏙️ 도시·환경",
        "desc": "매립지 개발 — NDBI 장기 모니터링",
    },
    "🏭 경북 포항시 남구 (산업단지)": {
        "type": "polygon",
        "layer": "lt_c_adsigg_info",
        "filter": "sigg_nm='포항시'",
        "coord": (129.32, 36.00, 129.42, 36.08),
        "zoom": 13,
        "domain": "🏙️ 도시·환경",
        "desc": "포스코 제철 — 대기질 NO2 모니터링",
    },
    "♨️ 대구 중구 (전국 최고기온)": {
        "type": "polygon",
        "layer": "lt_c_adsigg_info",
        "filter": "full_nm:like:대구광역시 중구",
        "coord": (128.58, 35.86, 128.62, 35.88),
        "zoom": 14,
        "domain": "🏙️ 도시·환경",
        "desc": "대구 분지 열섬 — 국내 최고 폭염 지역",
    },
}

# 하위 호환성 — 기존 preset_coords 방식 유지 (좌표 fallback용)
preset_coords: dict[str, tuple[float, float]] = {
    k: ((v["coord"][0] + v["coord"][2]) / 2,
        (v["coord"][1] + v["coord"][3]) / 2)
    for k, v in preset_config.items()
}
