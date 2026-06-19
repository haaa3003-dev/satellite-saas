# cross_diagnosis.py
"""
서로 다른 두 지수를 같은 위치/기간에 대해 같이 보고 교차 진단하는 모듈.

각 진단 함수는 (값A, 모드설정A, 값B, 모드설정B)를 받아
(상태 제목, 설명 문구) 튜플을 반환한다.

[중요] 여기 들어가는 해석 문구도 캘리브레이션되지 않은 임계값 위에서
만들어진 "경향성 설명"이라는 한계가 있다. 단정적인 진단이 아니라
"~가능성이 있습니다" 식으로 표현해서 과신을 유도하지 않도록 한다.
"""


def _is_low(val, cfg):
    """모드의 higher_is_worse 방향성을 고려해 '나쁜 쪽'에 가까운지 판단"""
    if cfg.get('higher_is_worse', False):
        return val >= cfg['threshold']  # 높을수록 나쁜 모드는 threshold 이상이 '나쁜 쪽'
    return val < cfg['threshold']       # 높을수록 좋은 모드는 threshold 미만이 '나쁜 쪽'


def interpret_ndvi_ndwi(ndvi_val, ndvi_cfg, ndwi_val, ndwi_cfg):
    ndvi_bad = _is_low(ndvi_val, ndvi_cfg)
    ndwi_bad = _is_low(ndwi_val, ndwi_cfg)

    if ndvi_bad and ndwi_bad:
        return ("🟤 가뭄 의심", "식생지수와 수분지수가 모두 낮게 관측됩니다. 가뭄으로 인한 작물 스트레스 가능성이 있습니다.")
    elif ndvi_bad and not ndwi_bad:
        return ("🌊 침수 의심", "식생지수는 낮은데 수분지수는 정상 이상입니다. 침수로 인한 작물 피해 가능성이 있습니다.")
    elif not ndvi_bad and ndwi_bad:
        return ("🌱 정상 생육 (건조 토양)", "식생 상태는 양호하나 토양 수분이 다소 낮게 관측됩니다. 관수 상태를 함께 확인해보는 걸 권장합니다.")
    else:
        return ("🟢 정상", "식생지수와 수분지수 모두 양호한 범위입니다.")


def interpret_ndvi_nbr(ndvi_val, ndvi_cfg, nbr_val, nbr_cfg):
    ndvi_bad = _is_low(ndvi_val, ndvi_cfg)
    nbr_bad = _is_low(nbr_val, nbr_cfg)

    if nbr_bad and ndvi_bad:
        return ("🔥 산불 피해 가능성 높음", "탄화흔적지수와 식생지수가 모두 낮게 관측됩니다. 최근 산불로 인한 식생 소실 가능성이 있습니다.")
    elif nbr_bad and not ndvi_bad:
        return ("⚠️ 재확인 필요", "탄화흔적지수는 낮지만 식생지수는 양호합니다. 그림자·수계 등 산불 외 다른 원인일 수 있어 영상으로 직접 확인을 권장합니다.")
    elif not nbr_bad and ndvi_bad:
        return ("🌾 비산불성 생육 저하", "탄화 흔적은 뚜렷하지 않으나 식생지수가 낮습니다. 가뭄·병해충 등 산불 외 원인일 가능성이 있습니다.")
    else:
        return ("🟢 정상 산림", "탄화 흔적 없이 식생지수도 양호한 범위입니다.")


def interpret_no2_lst(no2_val, no2_cfg, lst_val, lst_cfg):
    no2_bad = _is_low(no2_val, no2_cfg)
    lst_bad = _is_low(lst_val, lst_cfg)

    if no2_bad and lst_bad:
        return ("🔴 복합 환경 스트레스", "대기오염 농도와 지표온도가 모두 높게 관측됩니다. 열섬현상이 대기 정체를 유발해 오염물질이 잘 흩어지지 못하는 복합 악순환 가능성이 있습니다.")
    elif no2_bad and not lst_bad:
        return ("🏭 국지적 대기오염", "지표온도는 정상 범위이나 대기오염 농도가 높게 관측됩니다. 산업시설·교통량 등 직접적인 오염원의 영향일 가능성이 있습니다.")
    elif not no2_bad and lst_bad:
        return ("♨️ 단순 열섬 현상", "대기질은 양호하나 지표온도가 높게 관측됩니다. 녹지 부족, 아스팔트·콘크리트 비중 등 도시구조에 의한 열섬현상으로 추정됩니다.")
    else:
        return ("🟢 양호", "대기질과 지표온도 모두 양호한 범위입니다.")


# [신규] 모드 쌍 정의. 한 모드가 여러 쌍에 속할 수 있다 (NDVI는 NDWI/NBR 둘과 짝지어짐).
# 각 항목: (모드키A, 모드키B, 쌍 라벨, 해석함수)
CROSS_PAIRS = [
    (
        "🌾 농작물 생육 분석 (NDVI)", "🌊 저수지 및 홍수 모니터링 (NDWI)",
        "가뭄·침수 교차 진단", interpret_ndvi_ndwi
    ),
    (
        "🌾 농작물 생육 분석 (NDVI)", "🔥 산불 재해 및 산림 진단 (NBR)",
        "산불 피해 교차 검증", interpret_ndvi_nbr
    ),
    (
        "🏭 미세먼지 및 대기오염 지도 (NO2)", "♨️ 도심 폭염 및 열섬 현상 분석 (LST)",
        "도심 환경 복합 진단", interpret_no2_lst
    ),
]


def find_cross_pairs_for_mode(mode_key):
    """선택된 모드가 속한 모든 교차 진단 쌍을 반환한다.
    (현재 모드, 짝 모드, 라벨, 해석함수) 형태로, 현재 모드 기준으로 정렬해서 돌려준다.
    """
    results = []
    for a, b, label, fn in CROSS_PAIRS:
        if mode_key == a:
            results.append((a, b, label, fn))
        elif mode_key == b:
            results.append((b, a, label, fn))
    return results
