# cross_diagnosis.py
"""
서로 다른 두 지수를 같은 위치/기간에 대해 교차 진단하는 모듈.

각 진단 함수는 (값A, 모드설정A, 값B, 모드설정B)를 받아
(상태 제목, 설명 문구) 튜플을 반환한다.

[중요] 여기 들어가는 해석 문구는 캘리브레이션되지 않은 임계값 위에서
만들어진 "경향성 설명"이다. 단정적인 진단이 아니라
"~가능성이 있습니다" 식으로 표현해서 과신을 유도하지 않는다.

변경 사항:
- 타입 힌트 추가 (함수 시그니처, 반환 타입)
- CROSS_PAIRS를 TypeAlias로 명시
- find_cross_pairs_for_mode 반환 타입 명시
"""
from __future__ import annotations

from typing import Callable

# 해석 함수 타입 별칭: (값A, 설정A, 값B, 설정B) → (제목, 설명)
InterpretFn = Callable[[float, dict, float, dict], tuple[str, str]]

# 교차 쌍 타입: (모드키A, 모드키B, 라벨, 해석함수)
CrossPair = tuple[str, str, str, InterpretFn]


def _is_low(val: float, cfg: dict) -> bool:
    """모드의 higher_is_worse 방향성을 고려해 '나쁜 쪽'에 가까운지 판단."""
    if cfg.get("higher_is_worse", False):
        return val >= cfg["threshold"]   # 높을수록 나쁜 모드: threshold 이상이 '나쁜 쪽'
    return val < cfg["threshold"]        # 높을수록 좋은 모드: threshold 미만이 '나쁜 쪽'


def interpret_ndvi_ndwi(
    ndvi_val: float, ndvi_cfg: dict,
    ndwi_val: float, ndwi_cfg: dict,
) -> tuple[str, str]:
    ndvi_bad = _is_low(ndvi_val, ndvi_cfg)
    ndwi_bad = _is_low(ndwi_val, ndwi_cfg)

    if ndvi_bad and ndwi_bad:
        return (
            "🟤 가뭄 의심",
            "식생지수와 수분지수가 모두 낮게 관측됩니다. "
            "가뭄으로 인한 작물 스트레스 가능성이 있습니다.",
        )
    if ndvi_bad and not ndwi_bad:
        return (
            "🌊 침수 의심",
            "식생지수는 낮은데 수분지수는 정상 이상입니다. "
            "침수로 인한 작물 피해 가능성이 있습니다.",
        )
    if not ndvi_bad and ndwi_bad:
        return (
            "🌱 정상 생육 (건조 토양)",
            "식생 상태는 양호하나 토양 수분이 다소 낮게 관측됩니다. "
            "관수 상태를 함께 확인해보는 걸 권장합니다.",
        )
    return (
        "🟢 정상",
        "식생지수와 수분지수 모두 양호한 범위입니다.",
    )


def interpret_ndvi_nbr(
    ndvi_val: float, ndvi_cfg: dict,
    nbr_val: float, nbr_cfg: dict,
) -> tuple[str, str]:
    ndvi_bad = _is_low(ndvi_val, ndvi_cfg)
    nbr_bad = _is_low(nbr_val, nbr_cfg)

    if nbr_bad and ndvi_bad:
        return (
            "🔥 산불 피해 가능성 높음",
            "탄화흔적지수와 식생지수가 모두 낮게 관측됩니다. "
            "최근 산불로 인한 식생 소실 가능성이 있습니다.",
        )
    if nbr_bad and not ndvi_bad:
        return (
            "⚠️ 재확인 필요",
            "탄화흔적지수는 낮지만 식생지수는 양호합니다. "
            "그림자·수계 등 산불 외 다른 원인일 수 있어 영상으로 직접 확인을 권장합니다.",
        )
    if not nbr_bad and ndvi_bad:
        return (
            "🌾 비산불성 생육 저하",
            "탄화 흔적은 뚜렷하지 않으나 식생지수가 낮습니다. "
            "가뭄·병해충 등 산불 외 원인일 가능성이 있습니다.",
        )
    return (
        "🟢 정상 산림",
        "탄화 흔적 없이 식생지수도 양호한 범위입니다.",
    )


def interpret_no2_lst(
    no2_val: float, no2_cfg: dict,
    lst_val: float, lst_cfg: dict,
) -> tuple[str, str]:
    no2_bad = _is_low(no2_val, no2_cfg)
    lst_bad = _is_low(lst_val, lst_cfg)

    if no2_bad and lst_bad:
        return (
            "🔴 복합 환경 스트레스",
            "대기오염 농도와 지표온도가 모두 높게 관측됩니다. "
            "열섬현상이 대기 정체를 유발해 오염물질이 잘 흩어지지 못하는 "
            "복합 악순환 가능성이 있습니다.",
        )
    if no2_bad and not lst_bad:
        return (
            "🏭 국지적 대기오염",
            "지표온도는 정상 범위이나 대기오염 농도가 높게 관측됩니다. "
            "산업시설·교통량 등 직접적인 오염원의 영향일 가능성이 있습니다.",
        )
    if not no2_bad and lst_bad:
        return (
            "♨️ 단순 열섬 현상",
            "대기질은 양호하나 지표온도가 높게 관측됩니다. "
            "녹지 부족, 아스팔트·콘크리트 비중 등 "
            "도시구조에 의한 열섬현상으로 추정됩니다.",
        )
    return (
        "🟢 양호",
        "대기질과 지표온도 모두 양호한 범위입니다.",
    )


def interpret_ndre_ndvi(
    ndre_val: float, ndre_cfg: dict,
    ndvi_val: float, ndvi_cfg: dict,
) -> tuple[str, str]:
    """
    NDRE(엽록소)와 NDVI(식생 활성도) 교차 진단.

    NDVI는 전체 식생량을 보고, NDRE는 엽록소·질소 함유량에 더 민감하다.
    둘의 조합으로 "자라고는 있는데 속이 안 좋은" 상태를 포착할 수 있다.
    """
    ndre_bad = _is_low(ndre_val, ndre_cfg)
    ndvi_bad = _is_low(ndvi_val, ndvi_cfg)

    if ndre_bad and ndvi_bad:
        return (
            "🟡 복합 생육 부진",
            "엽록소 활성도와 식생지수가 모두 낮게 관측됩니다. "
            "질소 결핍, 병해충 피해, 또는 가뭄이 복합적으로 작용하고 있을 가능성이 있습니다. "
            "정밀 예찰 및 토양 분석을 권장합니다.",
        )
    if ndre_bad and not ndvi_bad:
        return (
            "⚠️ 잠재적 병해충 또는 영양 결핍 의심",
            "식생량(NDVI)은 정상이나 엽록소 활성도(NDRE)가 낮습니다. "
            "외관상 자라고 있지만 내부적으로 질소 결핍이나 초기 병해충 스트레스가 "
            "진행 중일 가능성이 있습니다. NDVI보다 NDRE가 먼저 반응하는 경우입니다.",
        )
    if not ndre_bad and ndvi_bad:
        return (
            "🌱 초기 생육 단계 추정",
            "엽록소 활성도는 양호하나 전체 식생량이 아직 낮습니다. "
            "파종 직후 또는 이앙 초기 단계로 잎 면적이 충분히 확보되지 않은 "
            "정상적인 생육 초기 상태일 가능성이 있습니다.",
        )
    return (
        "🟢 생육 상태 양호",
        "엽록소 활성도와 식생지수 모두 양호한 범위입니다. "
        "작물이 충분한 질소를 흡수하며 건강하게 생육 중인 것으로 추정됩니다.",
    )


def interpret_ndbi_lst(
    ndbi_val: float, ndbi_cfg: dict,
    lst_val: float, lst_cfg: dict,
) -> tuple[str, str]:
    """
    NDBI(불투수면)와 LST(지표면 온도) 교차 진단.

    불투수면이 높고 지표온도도 높으면 열섬 원인이 도시 구조에 있음을 시사한다.
    지자체 도시계획 담당자에게 유용한 조합.
    """
    ndbi_bad = _is_low(ndbi_val, ndbi_cfg)  # higher_is_worse=True이므로 높으면 bad
    lst_bad = _is_low(lst_val, lst_cfg)

    if ndbi_bad and lst_bad:
        return (
            "🔴 도시 열섬 고위험 구역",
            "불투수면 비율과 지표면 온도가 모두 높게 관측됩니다. "
            "건물·도로 등 인공 구조물이 밀집해 열을 흡수·방출하는 "
            "전형적인 도심 열섬 구조로 추정됩니다. "
            "녹지 확충 또는 반사율 높은 포장재 도입 등을 검토할 수 있습니다.",
        )
    if ndbi_bad and not lst_bad:
        return (
            "🏗️ 개발 구역 (온도 영향 제한적)",
            "불투수면 비율은 높으나 지표온도는 아직 정상 범위입니다. "
            "신규 개발 중이거나 녹지·수계가 인근에 있어 온도 상승을 일부 완화하고 있을 "
            "가능성이 있습니다. 장기적인 모니터링이 필요합니다.",
        )
    if not ndbi_bad and lst_bad:
        return (
            "☀️ 비도시 고온 구역",
            "불투수면 비율은 낮으나 지표온도가 높게 관측됩니다. "
            "논밭·나대지 등 토양 노출 구역에서의 복사열이거나 "
            "계절적 고온 현상일 가능성이 있습니다.",
        )
    return (
        "🟢 양호 (녹지 우세·온도 정상)",
        "불투수면 비율이 낮고 지표온도도 정상 범위입니다. "
        "녹지·농경지 비중이 높아 열섬 위험이 낮은 구역으로 추정됩니다.",
    )


def interpret_ndbi_ndwi(
    ndbi_val: float, ndbi_cfg: dict,
    ndwi_val: float, ndwi_cfg: dict,
) -> tuple[str, str]:
    """
    NDBI(불투수면)와 NDWI(수분) 교차 진단.

    불투수면이 높고 수분이 낮으면 도시 침수 취약성·건조 위험이 동시에 높아진다.
    도시 방재·수자원 담당 부서에 유용한 조합.
    """
    ndbi_bad = _is_low(ndbi_val, ndbi_cfg)
    ndwi_bad = _is_low(ndwi_val, ndwi_cfg)

    if ndbi_bad and ndwi_bad:
        return (
            "⚠️ 불투수면 집중 + 수분 부족",
            "불투수면 비율이 높고 지표 수분도 낮게 관측됩니다. "
            "빗물이 토양에 흡수되지 못하고 표면 유출로 이어져 "
            "집중호우 시 침수 위험이 높아질 수 있습니다. "
            "동시에 녹지·토양의 건조도 진행 중일 가능성이 있습니다.",
        )
    if ndbi_bad and not ndwi_bad:
        return (
            "🌊 불투수면 집중 + 수분 충분",
            "불투수면 비율은 높으나 지표 수분이 충분히 관측됩니다. "
            "강우 직후이거나 하천·저수지 인근 도심 구역일 가능성이 있습니다. "
            "배수 시설 상태를 함께 확인하는 것을 권장합니다.",
        )
    if not ndbi_bad and ndwi_bad:
        return (
            "🌾 녹지 우세 + 건조 토양",
            "불투수면 비율이 낮고 녹지·농경지가 우세하나 수분이 다소 부족합니다. "
            "가뭄 또는 관개 부족 상태일 가능성이 있습니다.",
        )
    return (
        "🟢 양호 (녹지 우세·수분 정상)",
        "불투수면 비율이 낮고 지표 수분도 정상 범위입니다. "
        "녹지·농경지 비중이 높아 수분 순환이 원활한 구역으로 추정됩니다.",
    )


# ── 교차 쌍 정의 ───────────────────────────────────────────────────────────────
# 한 모드가 여러 쌍에 속할 수 있다 (NDVI는 NDWI/NBR/NDRE 세 쌍에 속함).
# 각 항목: (모드키A, 모드키B, 쌍 라벨, 해석함수)
CROSS_PAIRS: list[CrossPair] = [
    (
        "🌾 농작물 생육 분석 (NDVI)",
        "🌊 저수지 및 홍수 모니터링 (NDWI)",
        "가뭄·침수 교차 진단",
        interpret_ndvi_ndwi,
    ),
    (
        "🌾 농작물 생육 분석 (NDVI)",
        "🔥 산불 재해 및 산림 진단 (NBR)",
        "산불 피해 교차 검증",
        interpret_ndvi_nbr,
    ),
    (
        "🏭 미세먼지 및 대기오염 지도 (NO2)",
        "♨️ 도심 폭염 및 열섬 현상 분석 (LST)",
        "도심 환경 복합 진단",
        interpret_no2_lst,
    ),
    # [신규] NDRE ↔ NDVI: 잠재적 병해충·영양 결핍 조기 포착
    (
        "🌿 엽록소 농도 및 병해충 조기탐지 (NDRE)",
        "🌾 농작물 생육 분석 (NDVI)",
        "병해충·영양 결핍 교차 진단",
        interpret_ndre_ndvi,
    ),
    # [신규] NDBI ↔ LST: 도시 열섬 원인 분석
    (
        "🏗️ 도시 확장 및 불투수면 탐지 (NDBI)",
        "♨️ 도심 폭염 및 열섬 현상 분석 (LST)",
        "도시 열섬 원인 교차 분석",
        interpret_ndbi_lst,
    ),
    # [신규] NDBI ↔ NDWI: 도시 침수 취약성 평가
    (
        "🏗️ 도시 확장 및 불투수면 탐지 (NDBI)",
        "🌊 저수지 및 홍수 모니터링 (NDWI)",
        "도시 침수 취약성 교차 평가",
        interpret_ndbi_ndwi,
    ),
]


def find_cross_pairs_for_mode(mode_key: str) -> list[CrossPair]:
    """
    선택된 모드가 속한 모든 교차 진단 쌍을 반환한다.
    (현재 모드, 짝 모드, 라벨, 해석함수) 형태로, 현재 모드 기준으로 정렬.
    """
    results: list[CrossPair] = []
    for a, b, label, fn in CROSS_PAIRS:
        if mode_key == a:
            results.append((a, b, label, fn))
        elif mode_key == b:
            results.append((b, a, label, fn))
    return results
