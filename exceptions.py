# exceptions.py
"""
K-Sat 커스텀 예외 계층.

기존: 모든 오류를 except Exception 하나로 묶어 print()만 했다.
      → 운영 환경에서 GEE 서버 다운인지, 인증 만료인지, 데이터 없음인지
        구분이 불가능했다.

개선: 오류 유형별 클래스를 정의하고, 호출부에서 유형에 맞게 처리한다.
"""


class KSatBaseError(Exception):
    """K-Sat 전용 예외의 공통 부모."""


# ── GEE 관련 ──────────────────────────────────

class GEEError(KSatBaseError):
    """Google Earth Engine 관련 오류의 부모."""


class GEEAuthenticationError(GEEError):
    """
    GEE 인증 실패 또는 프로젝트 접근 권한 없음.
    → 사용자에게: "서버 인증을 확인해주세요."
    """


class GEEQuotaError(GEEError):
    """
    GEE API 호출 쿼터 초과 또는 rate limit.
    → 사용자에게: "잠시 후 다시 시도해주세요."
    """


class GEETimeoutError(GEEError):
    """
    GEE 서버 응답 지연.
    → 사용자에게: "서버가 응답하지 않습니다. 잠시 후 다시 시도해주세요."
    """


class GEENoDataError(GEEError):
    """
    지정 기간/지역에 유효한 위성 영상이 없음.
    오류가 아니라 정상적인 '데이터 없음' 상태이지만,
    예외로 표현해서 호출부에서 명확히 처리하게 한다.
    """


# ── 네트워크 관련 ─────────────────────────────

class NetworkError(KSatBaseError):
    """외부 네트워크 호출(Nominatim 등) 실패."""


# ── 입력 검증 관련 ────────────────────────────

class ValidationError(KSatBaseError):
    """사용자 입력값 검증 실패."""
