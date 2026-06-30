"""
vworld.py — 공간정보 오픈플랫폼(Vworld) WFS API 클라이언트

공공데이터 폴리곤 기반 경계 표시를 위한 핵심 모듈.
ESA WorldCover / Dynamic World 격자 마스킹보다 정밀한 실제 경계 기반 분석 제공.

기능:
  1. bbox 내 필지 경계 GeoJSON 가져오기 (마스킹용)
  2. 지명/작물 검색 → 공공데이터 폴리곤 반환 (검색용)
  3. 행정구역 경계 가져오기 (도시 도메인용)

지원 레이어:
  lt_c_landinfobasemap    연속지적도 (모든 필지)
  lt_c_forestmap          임야도 (산림)
  lt_c_ulandbasemap       도시계획도 (도시)
  lt_c_waterarea          수계도 (하천·저수지·호소)
  lt_c_adsigg_info        시군구 행정구역 경계
  lt_c_adsido_info        시도 행정구역 경계

API 키 발급: https://www.vworld.kr/dev/v4dv_apitile_s001.do
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

VWORLD_WFS_URL = "https://api.vworld.kr/req/wfs"
VWORLD_DATA_URL = "https://api.vworld.kr/req/data"  # 행정구역·수계도·임야도용

# ── 레이어별 API 엔드포인트 구분 ─────────────────────────────────────────────
# WFS API: 연속지적도 등 필지 기반
# DATA API: 행정구역·수계도·임야도 등 공간 객체 기반
DATA_API_LAYERS = {
    "lt_c_adsigg_info",   # 시군구 행정구역
    "lt_c_adsido_info",   # 시도 행정구역
    "lt_c_waterarea",     # 수계도
    "lt_c_forestmap",     # 임야도
    "lt_c_ademd_info",    # 읍면동
}

# ── 도메인별 레이어 매핑 ──────────────────────────────────────────────────────
# index_name → (Vworld 레이어, CQL 필터 or None)
VWORLD_LAYER_MAP: dict[str, tuple[str, str | None] | None] = {
    "NDVI":   ("lt_c_landinfobasemap", "jimok_cd IN ('전','답','과수원','목장용지')"),
    "NDRE":   ("lt_c_landinfobasemap", "jimok_cd IN ('전','답','과수원','목장용지')"),
    "NDWI":   ("lt_c_waterarea",       None),
    "NBR":    ("lt_c_forestmap",       None),
    "LST":    ("lt_c_adsigg_info",     None),   # 시군구 행정구역
    "NDBI":   ("lt_c_adsigg_info",     None),   # 시군구 행정구역
    "NO2":    None,                              # 광역 대기 — 마스킹 불필요
    "SAR_VV": ("lt_c_waterarea",       None),
    "SAR_VH": ("lt_c_forestmap",       None),
}

# ── 작물 키워드 → 지목 코드 매핑 ─────────────────────────────────────────────
CROP_KEYWORD_MAP: dict[str, str] = {
    "사과": "과수원", "배": "과수원", "포도": "과수원",
    "감귤": "과수원", "복숭아": "과수원", "체리": "과수원",
    "과수": "과수원", "과일": "과수원",
    "벼": "답", "쌀": "답", "논": "답",
    "밭": "전", "채소": "전", "고추": "전", "마늘": "전",
    "양파": "전", "배추": "전", "감자": "전", "콩": "전",
    "목장": "목장용지", "초지": "목장용지",
}

# ── 지형 키워드 → 레이어 매핑 ────────────────────────────────────────────────
TERRAIN_KEYWORD_MAP: dict[str, str] = {
    "저수지": "lt_c_waterarea",
    "댐":     "lt_c_waterarea",
    "호수":   "lt_c_waterarea",
    "하천":   "lt_c_waterarea",
    "호":     "lt_c_waterarea",   # 충주호, 소양호 등
    "산림":   "lt_c_forestmap",
    "임야":   "lt_c_forestmap",
    "숲":     "lt_c_forestmap",
    "산":     "lt_c_forestmap",
    "강":     "lt_c_waterarea",
}

# 도시 분석 키워드 → 행정구역 레이어
URBAN_KEYWORD_MAP: dict[str, str] = {
    "열섬": "lt_c_adsigg_info",
    "대기오염": "lt_c_adsigg_info",
    "미세먼지": "lt_c_adsigg_info",
    "폭염": "lt_c_adsigg_info",
    "불투수면": "lt_c_adsigg_info",
    "도시": "lt_c_adsigg_info",
}


# ─────────────────────────────────────────────────────────────────────────────
# 핵심 WFS 요청 함수
# ─────────────────────────────────────────────────────────────────────────────

def _wfs_request(
    api_key: str,
    layer: str,
    bbox: tuple[float, float, float, float] | None = None,
    cql_filter: str | None = None,
    attr_filter: str | None = None,
    max_features: int = 500,
    timeout: int = 10,
) -> dict[str, Any] | None:
    """
    Vworld API 공통 요청 함수.
    레이어에 따라 DATA API와 WFS API를 자동 분기.
    """
    try:
        if layer in DATA_API_LAYERS:
            return _data_api_request(api_key, layer, bbox, attr_filter, max_features, timeout)
        else:
            return _wfs_api_request(api_key, layer, bbox, cql_filter, max_features, timeout)
    except Exception as e:
        logger.warning("Vworld 요청 실패 | layer=%s error=%s", layer, e)
        return None


def _data_api_request(
    api_key: str,
    layer: str,
    bbox: tuple[float, float, float, float] | None = None,
    attr_filter: str | None = None,
    max_features: int = 500,
    timeout: int = 10,
) -> dict[str, Any] | None:
    """Vworld 2D 데이터 API — 행정구역·수계도·임야도."""
    params: dict[str, str] = {
        "key":      api_key,
        "service":  "data",
        "request":  "GetFeature",
        "data":     layer.upper(),
        "format":   "json",
        "size":     str(min(max_features, 1000)),
        "page":     "1",
        "geometry": "true",
        "crs":      "EPSG:4326",
        "domain":   "http://localhost:8501",
    }

    if bbox:
        west, south, east, north = bbox
        params["bbox"] = f"{west},{south},{east},{north}"  # EPSG:4326 제거

    if attr_filter:
        params["attrFilter"] = attr_filter

    try:
        t0 = time.time()
        resp = requests.get(VWORLD_DATA_URL, params=params, timeout=timeout)
        elapsed = time.time() - t0
        resp.raise_for_status()
        data = resp.json()

        status = data.get("response", {}).get("status", "")
        if status != "OK":
            logger.warning("Vworld DATA API 오류 | layer=%s status=%s", layer, status)
            return None

        features = (
            data.get("response", {})
                .get("result", {})
                .get("featureCollection", {})
                .get("features", [])
        )
        feat_count = len(features)
        logger.info("Vworld DATA API | layer=%s features=%d elapsed=%.2fs", layer, feat_count, elapsed)

        if feat_count == 0:
            return None

        return {"type": "FeatureCollection", "features": features}

    except Exception as e:
        logger.warning("Vworld DATA API 실패 | layer=%s error=%s", layer, e)
        return None


def _wfs_api_request(
    api_key: str,
    layer: str,
    bbox: tuple[float, float, float, float] | None = None,
    cql_filter: str | None = None,
    max_features: int = 500,
    timeout: int = 10,
) -> dict[str, Any] | None:
    """Vworld WFS API — 연속지적도 등 필지 기반."""
    params: dict[str, str] = {
        "key":         api_key,
        "service":     "WFS",
        "version":     "2.0.0",
        "request":     "GetFeature",
        "typeName":    layer,
        "srsName":     "EPSG:4326",
        "maxFeatures": str(max_features),
        "output":      "application/json",
    }

    if bbox:
        west, south, east, north = bbox
        params["bbox"] = f"{west},{south},{east},{north},EPSG:4326"

    if cql_filter:
        params["CQL_FILTER"] = cql_filter

    try:
        t0 = time.time()
        resp = requests.get(VWORLD_WFS_URL, params=params, timeout=timeout)
        elapsed = time.time() - t0
        resp.raise_for_status()
        geojson = resp.json()
        feat_count = len(geojson.get("features", []))
        logger.info("Vworld WFS API | layer=%s features=%d elapsed=%.2fs", layer, feat_count, elapsed)
        return geojson if feat_count > 0 else None
    except Exception as e:
        logger.warning("Vworld WFS API 실패 | layer=%s error=%s", layer, e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 1. bbox 기반 마스킹 폴리곤
# ─────────────────────────────────────────────────────────────────────────────

def get_vworld_boundary(
    bbox: tuple[float, float, float, float],
    index_name: str,
    api_key: str,
    max_features: int = 500,
) -> dict[str, Any] | None:
    """bbox 내 공공데이터 폴리곤 반환 (GEE 마스킹용)."""
    if not api_key:
        return None

    layer_cfg = VWORLD_LAYER_MAP.get(index_name)
    if layer_cfg is None:
        return None

    layer, cql_filter = layer_cfg

    # DATA API 레이어는 attrFilter 방식, WFS는 CQL_FILTER 방식
    if layer in DATA_API_LAYERS:
        # CQL_FILTER를 attrFilter 형식으로 변환
        # "sigg_nm='중구'" → "sigg_nm:=:중구"
        attr_filter = _cql_to_attr_filter(cql_filter) if cql_filter else None
        return _wfs_request(api_key, layer, bbox=bbox, attr_filter=attr_filter,
                            max_features=max_features)
    else:
        return _wfs_request(api_key, layer, bbox=bbox, cql_filter=cql_filter,
                            max_features=max_features)


def _cql_to_attr_filter(cql: str) -> str | None:
    """
    CQL 필터를 Vworld DATA API attrFilter 형식으로 변환.
    "sigg_nm='중구'" → "sigg_nm:=:중구"
    "river_nm LIKE '%소양%'" → "river_nm:like:소양"
    "full_nm:like:서울특별시 중구" → 그대로 반환 (이미 attrFilter 형식)
    """
    import re
    if not cql:
        return None

    # 이미 attrFilter 형식인 경우 그대로 반환
    if ':like:' in cql or ':=:' in cql:
        return cql

    # = 연산자
    m = re.match(r"(\w+)\s*=\s*'([^']+)'", cql)
    if m:
        return f"{m.group(1)}:=:{m.group(2)}"

    # LIKE 연산자
    m = re.match(r"(\w+)\s+LIKE\s+'%([^%]+)%'", cql, re.IGNORECASE)
    if m:
        return f"{m.group(1)}:like:{m.group(2)}"

    return None


def get_vworld_mask(
    bbox: tuple[float, float, float, float],
    index_name: str,
    api_key: str,
):
    """
    Vworld 폴리곤 → GEE 마스크 이미지 변환.
    없으면 None 반환 → ESA+DW 격자 마스킹으로 fallback.
    """
    geojson = get_vworld_boundary(bbox, index_name, api_key)
    if geojson is None:
        return None
    try:
        import ee  # noqa: PLC0415
        fc = ee.FeatureCollection(geojson)
        return ee.Image.constant(1).clip(fc).unmask(0)
    except Exception as e:
        logger.warning("GEE 마스크 변환 실패 | error=%s", e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 2. 지명 + 작물/용도 검색 → 폴리곤 반환
# ─────────────────────────────────────────────────────────────────────────────

def parse_search_query(query: str) -> dict[str, str | None]:
    """
    자연어 검색어를 파싱해서 지역명, 레이어, 필터를 추출.

    예시:
      "충주 사과밭"   → {region: "충주", layer: "lt_c_landinfobasemap", filter: "jimok_cd='과수원'"}
      "소양강댐"      → {region: "소양강댐", layer: "lt_c_waterarea", filter: None}
      "서울 중구"     → {region: "중구", layer: "lt_c_adsigg_info", filter: "sigg_nm='중구'"}
    """
    result: dict[str, str | None] = {
        "region": None,
        "layer": None,
        "cql_filter": None,
        "jimok": None,
    }

    query = query.strip()

    # 지형 키워드 매칭 — 단어 끝 매칭으로 오탐 방지
    # "영산강"의 "산"이 forestmap으로 잡히는 문제 방지
    for keyword, layer in TERRAIN_KEYWORD_MAP.items():
        if keyword in query:
            # "산"이 다른 글자 뒤에 붙은 경우 제외 (예: 영산강의 "산")
            if keyword == "산" and query.index(keyword) > 0:
                prev_char = query[query.index(keyword) - 1]
                if prev_char not in (' ', '\t'):
                    continue
            result["layer"] = layer
            query = query.replace(keyword, "").strip()
            break

    # 작물 키워드 매칭 (지형 키워드가 없을 때)
    if result["layer"] is None:
        for keyword, jimok in CROP_KEYWORD_MAP.items():
            if keyword in query:
                result["jimok"] = jimok
                result["layer"] = "lt_c_landinfobasemap"
                result["cql_filter"] = f"jimok_cd='{jimok}'"
                query = query.replace(keyword, "").replace("밭", "").replace("농장", "").strip()
                break

    # 도시 분석 키워드 매칭
    if result["layer"] is None:
        for keyword, layer in URBAN_KEYWORD_MAP.items():
            if keyword in query:
                result["layer"] = layer
                query = query.replace(keyword, "").strip()
                break

    # 행정구역 패턴 매칭 (시·군·구·읍·면·동)
    import re
    admin_match = re.search(r'(\S+[시군구읍면동])', query)
    if admin_match:
        admin_name = admin_match.group(1)
        result["region"] = admin_name

        # 레이어가 아직 없으면 행정구역 레이어 사용
        if result["layer"] is None:
            if admin_name.endswith(('시', '군')):
                result["layer"] = "lt_c_adsigg_info"
                result["cql_filter"] = f"sigg_nm='{admin_name}'"
            elif admin_name.endswith('구'):
                result["layer"] = "lt_c_adsigg_info"
                result["cql_filter"] = f"sigg_nm='{admin_name}'"
        else:
            # 작물 레이어에 지역 필터 추가
            sigungu = admin_name
            existing = result["cql_filter"] or ""
            result["cql_filter"] = f"sigg_nm='{sigungu}' AND {existing}" if existing else f"sigg_nm='{sigungu}'"
    else:
        # 행정구역 패턴 없음 → 나머지 텍스트를 지역명으로
        remaining = query.strip()
        if remaining:
            result["region"] = remaining

    return result


def search_polygon(
    query: str,
    api_key: str,
    bbox: tuple[float, float, float, float] | None = None,
    max_features: int = 200,
) -> dict[str, Any] | None:
    """
    자연어 검색어로 공공데이터 폴리곤 검색.

    Parameters
    ----------
    query       : "충주 사과밭", "소양강댐", "서울 중구" 등
    api_key     : Vworld API 키
    bbox        : 검색 범위 제한 (없으면 전국 검색)
    max_features: 최대 반환 필지 수

    Returns
    -------
    GeoJSON FeatureCollection 또는 None
    """
    if not api_key:
        logger.debug("Vworld API 키 없음")
        return None

    parsed = parse_search_query(query)
    layer = parsed.get("layer")
    cql_filter = parsed.get("cql_filter")

    if not layer:
        layer = "lt_c_landinfobasemap"

    logger.info("Vworld 검색 | query=%s layer=%s filter=%s", query, layer, cql_filter)

    if layer in DATA_API_LAYERS:
        attr_filter = _cql_to_attr_filter(cql_filter) if cql_filter else None
        return _wfs_request(api_key, layer, bbox=bbox, attr_filter=attr_filter,
                            max_features=max_features)
    else:
        return _wfs_request(api_key, layer, bbox=bbox, cql_filter=cql_filter,
                            max_features=max_features)


# ─────────────────────────────────────────────────────────────────────────────
# 3. 행정구역 경계 전용 함수
# ─────────────────────────────────────────────────────────────────────────────

def get_admin_boundary(
    sigungu_name: str,
    api_key: str,
) -> dict[str, Any] | None:
    """
    시군구 이름으로 행정구역 경계 폴리곤 반환.

    예: "중구", "강남구", "수원시", "제주시"
    """
    if not api_key:
        return None

    cql_filter = f"sigg_nm='{sigungu_name}'"
    return _wfs_request(
        api_key,
        "lt_c_adsigg_info",
        cql_filter=cql_filter,
        max_features=10,
    )


def get_water_boundary(
    water_name: str,
    api_key: str,
    bbox: tuple[float, float, float, float] | None = None,
) -> dict[str, Any] | None:
    """
    수체 이름으로 저수지·강·호수 경계 폴리곤 반환.

    예: "소양강", "충주호", "안동댐"
    """
    if not api_key:
        return None

    cql_filter = f"river_nm LIKE '%{water_name}%'"
    return _wfs_request(
        api_key,
        "lt_c_waterarea",
        bbox=bbox,
        cql_filter=cql_filter,
        max_features=50,
    )


def get_forest_boundary(
    bbox: tuple[float, float, float, float],
    api_key: str,
) -> dict[str, Any] | None:
    """bbox 내 임야도 경계 반환."""
    if not api_key:
        return None
    return _wfs_request(api_key, "lt_c_forestmap", bbox=bbox, max_features=300)


# ─────────────────────────────────────────────────────────────────────────────
# 4. GeoJSON → Folium 오버레이 (지도 경계선 표시용)
# ─────────────────────────────────────────────────────────────────────────────

def geojson_to_folium_layer(
    geojson: dict[str, Any],
    layer_name: str = "필지 경계",
    color: str = "#2c7fb8",
    fill_opacity: float = 0.0,
    weight: float = 1.5,
):
    """
    GeoJSON → Folium GeoJson 레이어 반환.
    fill_opacity=0 으로 경계선만 표시 (위성 이미지 위에 얹음).
    """
    import folium  # noqa: PLC0415
    return folium.GeoJson(
        geojson,
        name=layer_name,
        style_function=lambda _: {
            "color": color,
            "weight": weight,
            "fillOpacity": fill_opacity,
            "fillColor": color,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=list(geojson["features"][0]["properties"].keys())[:3]
            if geojson.get("features") else [],
            aliases=["속성"] * 3,
            sticky=False,
        ) if geojson.get("features") else None,
    )
