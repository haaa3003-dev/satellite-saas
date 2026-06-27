# geocoding.py
"""
지명/주소 → 위경도 변환 모듈 (OpenStreetMap Nominatim).

변경 사항:
- 타입 힌트 추가
- except Exception 제거 → requests 예외 유형별 분기
- logging 추가
- NetworkError 커스텀 예외 전파 (호출부에서 처리)
"""
from __future__ import annotations

import logging

import requests
import streamlit as st

from exceptions import NetworkError

logger = logging.getLogger(__name__)

# Nominatim 이용 약관: User-Agent는 프로젝트를 식별할 수 있는 값이어야 한다.
_USER_AGENT = "ksat-open-explorer/1.0 (academic project)"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_TIMEOUT_SECONDS = 5


@st.cache_data(show_spinner=False)
def geocode_place(query: str) -> list[tuple[float, float, str]]:
    """
    지명/주소 키워드로 좌표 목록을 반환한다.

    반환: [(위도, 경도, 표시명), ...], 최대 5개.
    결과 없음: 빈 리스트.
    네트워크 오류: NetworkError 발생 (호출부에서 사용자 안내).

    [기존] except Exception: return []
    → 네트워크 오류와 "결과 없음"이 구분 불가능했다.
    [개선] requests 예외를 NetworkError로 변환해 호출부에서 처리하게 한다.
    """
    query = query.strip()
    if len(query) < 2:
        return []

    params = {
        "q": query,
        "format": "json",
        "limit": 5,
        "countrycodes": "kr",
        "accept-language": "ko",
    }
    headers = {"User-Agent": _USER_AGENT}

    try:
        response = requests.get(
            _NOMINATIM_URL,
            params=params,
            headers=headers,
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        items: list[dict] = response.json()

        return [
            (float(item["lat"]), float(item["lon"]), item["display_name"])
            for item in items
        ]

    except requests.exceptions.Timeout as exc:
        logger.warning("Nominatim timeout | query=%s", query)
        raise NetworkError("지명 검색 서버가 응답하지 않습니다. 잠시 후 다시 시도해주세요.") from exc

    except requests.exceptions.ConnectionError as exc:
        logger.warning("Nominatim connection error | query=%s", query)
        raise NetworkError("네트워크 연결을 확인해주세요.") from exc

    except requests.exceptions.HTTPError as exc:
        logger.warning("Nominatim HTTP error %s | query=%s", exc.response.status_code, query)
        raise NetworkError(f"지명 검색 서버 오류 (HTTP {exc.response.status_code})") from exc

    except (KeyError, ValueError) as exc:
        logger.exception("Nominatim response parse error | query=%s", query)
        raise NetworkError("지명 검색 응답을 처리할 수 없습니다.") from exc
