# models.py
"""
K-Sat 도메인 모델 — 모든 데이터 구조는 여기서 정의한다.

기존 코드는 dict를 그대로 세션 상태에 저장했기 때문에
어떤 키가 있는지, 값이 None일 수 있는지를
코드를 전부 읽어야만 알 수 있었다.
dataclass로 교체하면 IDE 자동완성·타입 검사·None 방어가 한 번에 해결된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


# ─────────────────────────────────────────────
# 1. 요청 관련 모델
# ─────────────────────────────────────────────

@dataclass
class RegionInfo:
    """분석 대상 지역."""
    lat: float
    lon: float
    name: str
    buffer_m: int = 3000  # 반경(미터). 전체에서 단 한 곳에만 정의.

    def __post_init__(self) -> None:
        if not (-90 <= self.lat <= 90):
            raise ValueError(f"위도 범위 초과: {self.lat}")
        if not (-180 <= self.lon <= 180):
            raise ValueError(f"경도 범위 초과: {self.lon}")
        if self.buffer_m <= 0:
            raise ValueError(f"버퍼 반경은 양수여야 합니다: {self.buffer_m}")


@dataclass
class AnalysisRequest:
    """분석 실행에 필요한 모든 입력값."""
    region: RegionInfo
    mode_key: str
    start_date: date
    end_date: date
    cloud_threshold: int = 20

    def __post_init__(self) -> None:
        if self.start_date >= self.end_date:
            raise ValueError("시작일은 종료일보다 빨라야 합니다.")
        if not (0 <= self.cloud_threshold <= 100):
            raise ValueError(f"구름 허용률은 0~100 사이여야 합니다: {self.cloud_threshold}")


# ─────────────────────────────────────────────
# 2. GEE 응답 파싱 결과
# ─────────────────────────────────────────────

@dataclass
class SatelliteStatistics:
    """
    GEE reduceRegion 결과를 담는 구조체.

    기존: dict에서 'NDVI_mean', 'NDVI_min' 키를 직접 꺼냈음.
    개선: 파싱 책임을 extract_from_gee_dict()에 집중시켜
          호출부에서는 .mean, .min_val 등으로 접근한다.
    """
    mean: Optional[float] = None
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    std_dev: Optional[float] = None
    image_count: int = 0

    @classmethod
    def empty(cls) -> "SatelliteStatistics":
        """데이터 없음(count=0) 상황을 명시적으로 표현."""
        return cls(image_count=0)

    @classmethod
    def extract_from_gee_dict(
        cls,
        raw: Optional[dict],
        index_name: str,
        image_count: int,
    ) -> "SatelliteStatistics":
        """
        GEE reduceRegion 결과 dict에서 안전하게 값을 추출한다.

        GEE는 'NDVI_mean', 'NDVI_min', 'NDVI_max', 'NDVI_stdDev' 형태의
        키를 반환한다. 키가 없거나 값이 숫자가 아니면 None을 유지한다.

        [이전 버그] app.py 안에서 get_safe_value()가 if run_btn: 블록
        내부에 정의돼 있어 매 렌더링 사이클마다 재정의되고,
        로직이 분산돼 있었다. 여기서 한 번만 정의한다.
        """
        def _pick(suffix: str) -> Optional[float]:
            if not raw or not isinstance(raw, dict):
                return None
            val = raw.get(f"{index_name}_{suffix}")
            return val if isinstance(val, (int, float)) else None

        return cls(
            mean=_pick("mean"),
            min_val=_pick("min"),
            max_val=_pick("max"),
            std_dev=_pick("stdDev"),
            image_count=image_count,
        )

    @property
    def has_data(self) -> bool:
        return self.image_count > 0 and self.mean is not None

    @property
    def coefficient_of_variation(self) -> Optional[float]:
        """변동계수(CV) = 표준편차 / |평균|. 구역 내 균일성 지표."""
        if self.mean and abs(self.mean) > 1e-6 and self.std_dev is not None:
            return self.std_dev / abs(self.mean)
        return None


# ─────────────────────────────────────────────
# 3. 교차 진단 결과
# ─────────────────────────────────────────────

@dataclass
class CrossDiagnosisResult:
    """교차 진단 한 쌍의 결과."""
    label: str
    partner_mode_key: str
    available: bool
    title: str = ""
    description: str = ""
    partner_mean: Optional[float] = None


# ─────────────────────────────────────────────
# 4. 최종 분석 결과 (세션 상태에 저장되는 단위)
# ─────────────────────────────────────────────

@dataclass
class AnalysisResult:
    """
    run_analysis() 가 반환하고 세션 상태에 저장되는 최종 결과.

    기존 코드는 30개 이상의 키를 가진 dict를 세션에 저장했다.
    dataclass로 교체하면 필드 목록이 명확해지고,
    .change_rate 같은 파생 계산은 property로 캡슐화할 수 있다.
    """
    request: AnalysisRequest
    current: SatelliteStatistics
    last_year: SatelliteStatistics
    time_series: list[tuple[str, float]] = field(default_factory=list)
    cross_results: list[CrossDiagnosisResult] = field(default_factory=list)
    tile_url: Optional[str] = None

    # ── 파생 계산 ──────────────────────────────

    @property
    def change_rate(self) -> Optional[float]:
        """전년 동기 대비 변화율(%)."""
        if (self.last_year.has_data
                and self.last_year.mean
                and abs(self.last_year.mean) > 1e-6
                and self.current.mean is not None):
            return ((self.current.mean - self.last_year.mean)
                    / abs(self.last_year.mean)) * 100
        return None

    @property
    def reliability_label(self) -> str:
        """구름 허용률 기반 데이터 신뢰도 레이블."""
        return "우수 (95%)" if self.request.cloud_threshold <= 25 else "보통 (80%)"
