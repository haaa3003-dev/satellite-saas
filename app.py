# app.py
import streamlit as st
import ee
import folium
from streamlit_folium import st_folium
from datetime import date, timedelta
import pandas as pd
import plotly.graph_objects as go
import traceback  # 에러 트래킹용

# 핵심 커스텀 모듈 임포트
from mode_config import mode_config, preset_coords
from gee_utils import init_gee, get_satellite_index_for_period, get_cached_stats, get_time_series, get_ee_tile_url
from geocoding import geocode_place
from report_builder import generate_excel_report

# 1. 페이지 테마 설정 (오픈 데이터 포털 느낌)
st.set_page_config(page_title="K-Sat 오픈 탐색기", page_icon="🌍", layout="wide")

# 상단 헤더 영역
st.title("🌍 K-Sat 위성 데이터 오픈 탐색기")
st.caption("누구나 자유롭게 분석하고 활용하는 Sentinel-2 위성 기반 환경·재해 모니터링 플랫폼")

# 환영 메시지 및 사용 안내
st.info("💡 **[안내]** 본 플랫폼은 오픈 위성 데이터를 활용하여 누구나 무료로 특정 지역의 농업 생육, 수자원, 산림 상태를 관측할 수 있도록 지원합니다. 아래 검색창에 궁금한 지역을 입력해 보세요!")

st.markdown("---")

# GEE 인증
gee_ready = init_gee()
if not gee_ready:
    st.error("🚨 위성 데이터 서버(GEE) 인증에 실패했습니다. 환경 설정을 확인해주세요.")
    st.stop()

# -----------------------------------------------------------------
# 2. 사이드바 컨트롤 시스템
# -----------------------------------------------------------------
st.sidebar.header("🛠️ 탐색 설정")
# [수정] 옵션을 mode_config 키에서 자동으로 가져온다.
# 새 모드를 mode_config.py에 추가하기만 하면 여기는 다시 손댈 필요 없다.
analysis_mode = st.sidebar.selectbox(
    "🔎 관측 모드 선택",
    list(mode_config.keys())
)
cfg = mode_config[analysis_mode]

st.sidebar.markdown("---")
st.sidebar.caption("Powered by Google Earth Engine & K-Sat Team")

# -----------------------------------------------------------------
# 3. 메인 화면 - 중앙 검색창 및 지역 설정
# [채택: 제미나이의 콜백 기반 상호 배타 전환 방식]
# 검색창에 입력하면 프리셋이 자동으로 "직접 검색"으로 리셋되고,
# 프리셋을 선택하면 검색창이 자동으로 비워진다. 폼/제출버튼 없이도
# 두 입력이 서로를 가리지 않는다.
# -----------------------------------------------------------------
st.subheader("📍 관측 지역 검색")


def clear_preset():
    st.session_state.preset_select = "직접 검색"


def clear_search():
    st.session_state.search_input = ""


# 세션 상태 초기화 (검색 위젯용)
if 'search_input' not in st.session_state:
    st.session_state.search_input = ""
if 'preset_select' not in st.session_state:
    st.session_state.preset_select = "직접 검색"
if 'lat' not in st.session_state:
    first_preset = list(preset_coords.keys())[0] if preset_coords else "서울"
    st.session_state.lat = preset_coords.get(first_preset, (37.5665, 126.9780))[0]
    st.session_state.lon = preset_coords.get(first_preset, (37.5665, 126.9780))[1]
    st.session_state.region_name = first_preset

col1, col2 = st.columns([2, 1])
with col1:
    search_query = st.text_input(
        "🔍 지명 또는 주소를 입력하세요",
        placeholder="예: 춘천시 소양강, 새만금, 지리산 (입력 후 Enter)",
        key="search_input",
        on_change=clear_preset
    )
with col2:
    preset_choice = st.selectbox(
        "📌 주요 관심 지역 빠르게 이동",
        ["직접 검색"] + list(preset_coords.keys()),
        key="preset_select",
        on_change=clear_search
    )

# 지역 변경 로직 처리
if st.session_state.search_input:
    results = geocode_place(st.session_state.search_input)
    if results:
        st.session_state.lat, st.session_state.lon, st.session_state.region_name = results[0]
        st.success(f"✅ '{st.session_state.region_name}'(으)로 좌표를 설정했습니다. 아래 버튼을 눌러주세요.")
    else:
        st.warning("⚠️ 검색 결과가 없습니다. 다른 검색어나 조금 더 넓은 지명을 입력해보세요.")
elif st.session_state.preset_select != "직접 검색":
    preset = st.session_state.preset_select
    if preset in preset_coords:
        st.session_state.lat, st.session_state.lon = preset_coords[preset]
        st.session_state.region_name = preset

# -----------------------------------------------------------------
# 4. 상세 조건 설정 및 실행 버튼
# -----------------------------------------------------------------
st.markdown("---")
col_d1, col_d2, col_d3, col_d4 = st.columns(4)
with col_d1:
    e_date = st.date_input("📅 관측 종료일 (최근)", date.today())
with col_d2:
    s_date = st.date_input("📅 관측 시작일", e_date - timedelta(days=30))
with col_d3:
    cloud_threshold = st.slider("☁️ 구름 허용률 (%)", 0, 100, 20, 5, help="수치를 높이면 흐린 날의 사진도 포함하여 더 많은 데이터를 가져옵니다.")
with col_d4:
    st.markdown("<br>", unsafe_allow_html=True)
    run_btn = st.button("🚀 위성 데이터 불러오기", use_container_width=True, type="primary")

# -----------------------------------------------------------------
# 5. 위성 데이터 처리 (버튼 클릭 시에만 GEE 호출 → 세션에 스냅샷 저장)
# 'analysis_res' 키의 존재 여부 자체가 "분석 완료" 플래그 역할을 한다.
# 날짜·구름허용률 등 다른 위젯을 조작해도 이 블록은 run_btn이 True일 때만
# 실행되므로 GEE가 불필요하게 재호출되지 않는다.
# -----------------------------------------------------------------
if run_btn:
    # [채택] 날짜 역전 체크 - 시작일이 종료일보다 늦으면 바로 막는다.
    if s_date >= e_date:
        st.warning("⚠️ 관측 시작일은 종료일보다 빨라야 합니다. 날짜를 다시 확인해주세요.")
    else:
        # 통신 전 이전 결과 초기화 (실패 시 stale한 이전 결과가 남지 않도록)
        if 'analysis_res' in st.session_state:
            del st.session_state['analysis_res']

        with st.spinner("🛰️ 위성 데이터를 렌더링하고 있습니다. 잠시만 기다려주세요..."):
            try:
                region = ee.Geometry.Point([st.session_state.lon, st.session_state.lat]).buffer(3000)

                gee_result = get_satellite_index_for_period(
                    region, str(s_date), str(e_date), cloud_threshold, cfg
                )
                calculated_index = gee_result[-1] if isinstance(gee_result, tuple) else gee_result

                count, stats = get_cached_stats(
                    st.session_state.lat, st.session_state.lon, 3000,
                    str(s_date), str(e_date), cloud_threshold, cfg
                )

                ly_start = s_date.replace(year=s_date.year - 1)
                ly_end = e_date.replace(year=e_date.year - 1)
                last_count, last_stats = get_cached_stats(
                    st.session_state.lat, st.session_state.lon, 3000,
                    str(ly_start), str(ly_end), cloud_threshold, cfg
                )

                # [채택: 내 버전] GEE reduceRegion 결과는 "NDVI_mean", "NDVI_max"처럼
                # 접미사가 붙은 키로 반환된다. 정확한 키만 신뢰하고, 못 찾으면 None을
                # 반환한다. (제미나이 버전의 "base_key로 시작하는 첫 값" fallback은
                # "NDVI_max"도 "NDVI"로 시작하기 때문에, _mean이 없는 비정상 상황에서
                # 최댓값을 평균인 것처럼 잘못 가져올 수 있는 위험이 남아있었음)
                # [확장] stat_suffix를 인자로 받아 mean/min/max/stdDev 모두 같은
                # 방식으로 정확한 키만 신뢰하고 가져올 수 있게 했다.
                def get_safe_value(stat_dict, base_key, stat_suffix="mean"):
                    if not stat_dict or not isinstance(stat_dict, dict):
                        return None
                    val = stat_dict.get(f"{base_key}_{stat_suffix}")
                    return val if isinstance(val, (int, float)) else None

                idx_key = cfg['index_name']
                avg_val = get_safe_value(stats, idx_key, "mean") or 0.0
                min_val = get_safe_value(stats, idx_key, "min")
                max_val = get_safe_value(stats, idx_key, "max")
                std_val = get_safe_value(stats, idx_key, "stdDev")
                last_avg = get_safe_value(last_stats, idx_key, "mean") if last_count > 0 else None

                # [확장 2] 기간 내 실측 시계열 — 100% 실측치, 예측 없음.
                # 개별 위성 촬영분마다의 (날짜, 평균값)을 가져온다.
                time_series = get_time_series(
                    st.session_state.lat, st.session_state.lon, 3000,
                    str(s_date), str(e_date), cloud_threshold, cfg
                )

                # 렌더링에 필요한 지도 URL 생성 (여기서 한 번만 호출)
                vis_params = {'min': cfg['min'], 'max': cfg['max'], 'palette': cfg['palette']}
                tile_url = get_ee_tile_url(calculated_index, vis_params) if count > 0 else None

                # 세션에 최종 결과 딕셔너리로 저장
                st.session_state.analysis_res = {
                    'count': count,
                    'avg_val': avg_val,
                    'min_val': min_val,
                    'max_val': max_val,
                    'std_val': std_val,
                    'last_avg': last_avg,
                    'time_series': time_series,
                    'tile_url': tile_url,
                    'idx_name': cfg['index_name'],
                    'region_name': st.session_state.region_name,
                    'lat': st.session_state.lat,
                    'lon': st.session_state.lon,
                    'mode': analysis_mode,
                    'cfg': cfg,
                    'e_date': e_date,
                    'reliability': "우수 (95%)" if cloud_threshold <= 25 else "보통 (80%)"
                }

            except Exception:
                # [채택: 제미나이] 사용자에게는 친절한 안내, 백그라운드에는 상세 로그
                print(f"GEE API Error: {traceback.format_exc()}")
                st.error("🚨 일시적으로 위성 데이터를 불러올 수 없거나 렌더링 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

# -----------------------------------------------------------------
# 6. 화면 출력부 (세션에 저장된 결과만 읽어서 출력)
# -----------------------------------------------------------------
if 'analysis_res' in st.session_state:
    res = st.session_state.analysis_res
    st.markdown("---")

    if res['count'] == 0:
        st.warning("⚠️ 지정한 기간과 지역에 구름이 너무 많거나 유효한 위성 사진이 없습니다. 구름 허용률을 높이거나 기간을 넓혀보세요.")
    else:
        # [A] 지도 시각화
        st.subheader(f"🗺️ {res['region_name']} 위성 지도 ({res['idx_name']})")
        m = folium.Map(location=[res['lat'], res['lon']], zoom_start=13)

        folium.TileLayer(
            tiles=res['tile_url'],
            attr='Google Earth Engine',
            name=f"{res['idx_name']} Index",
            overlay=True,
            control=True
        ).add_to(m)

        st_folium(m, width="100%", height=500, returned_objects=[])

        # [B] 차트 및 데이터 인사이트
        st.markdown("---")
        st.subheader("📊 데이터 분석 결과")

        col_m1, col_m2, col_m3 = st.columns([1, 1, 1])

        with col_m1:
            st.markdown("#### 🧭 현재 지수 상태")
            fig_gauge = go.Figure(go.Indicator(
                mode="gauge+number",
                value=round(res['avg_val'], 4),
                title={'text': f"올해 {res['idx_name']} 실측치", 'font': {'size': 14}},
                gauge={
                    'axis': {'range': [res['cfg']['min'], res['cfg']['max']]},
                    'bar': {'color': "#2ecc71" if res['avg_val'] >= res['cfg']['threshold'] else "#e74c3c"},
                    'threshold': {
                        'line': {'color': "red", 'width': 3},
                        'thickness': 0.75,
                        'value': res['cfg']['threshold']
                    }
                }
            ))
            fig_gauge.update_layout(height=250, margin=dict(l=20, r=20, t=40, b=20))
            st.plotly_chart(fig_gauge, use_container_width=True)

        with col_m2:
            st.markdown("#### 📅 전년 대비 비교")
            fig_bar = go.Figure()
            fig_bar.add_trace(go.Bar(
                x=["전년 동기", "올해 실측"],
                y=[res['last_avg'] if res['last_avg'] is not None else 0, res['avg_val']],
                marker_color=['#bdc3c7', '#3498db' if res['avg_val'] >= res['cfg']['threshold'] else '#e74c3c'],
                text=[f"{res['last_avg']:.4f}" if res['last_avg'] is not None else "데이터 없음", f"{res['avg_val']:.4f}"],
                textposition='auto'
            ))
            fig_bar.update_layout(
                height=250,
                margin=dict(l=20, r=20, t=40, b=20),
                yaxis=dict(range=[res['cfg']['min'] - 0.1, res['cfg']['max'] + 0.1])
            )
            st.plotly_chart(fig_bar, use_container_width=True)

        with col_m3:
            st.markdown("#### 💡 종합 진단 요약")
            st.markdown("<br>", unsafe_allow_html=True)

            if res['last_avg'] is not None and res['last_avg'] != 0:
                change_rate = ((res['avg_val'] - res['last_avg']) / abs(res['last_avg'])) * 100
                st.metric(label=f"🎯 올해 평균 {res['idx_name']}", value=f"{res['avg_val']:.4f}", delta=f"{change_rate:+.2f}% (전년 동기 대비)")
            else:
                change_rate = None
                st.metric(label=f"🎯 올해 평균 {res['idx_name']}", value=f"{res['avg_val']:.4f}", delta="비교 불가 (전년 데이터 없음)", delta_color="off")

            st.markdown("---")
            if res['avg_val'] >= res['cfg']['threshold']:
                st.success(f"**🟢 상태 양호**\n\n{res['cfg']['desc_good']}")
            else:
                st.error(f"**🔴 주의 요망**\n\n{res['cfg']['desc_bad']}")

        # [확장 1] 평균값 하나로는 안 보이던 "구역 내 균일성"을 보여주는
        # 최소/최대/표준편차 패널. 같은 평균이라도 표준편차가 크면
        # 구역 내 일부만 국지적으로 문제가 있을 가능성을 시사한다.
        st.markdown("---")
        st.subheader("📐 구역 내 분포 통계")
        std_val = res.get('std_val')
        min_val = res.get('min_val')
        max_val = res.get('max_val')

        col_s1, col_s2, col_s3, col_s4 = st.columns(4)
        col_s1.metric("평균", f"{res['avg_val']:.4f}")
        col_s2.metric("최솟값", f"{min_val:.4f}" if min_val is not None else "—")
        col_s3.metric("최댓값", f"{max_val:.4f}" if max_val is not None else "—")
        col_s4.metric("표준편차", f"{std_val:.4f}" if std_val is not None else "—")

        # 표준편차 기반 균일성 해석 (실측값만으로 계산되는 규칙 기반 문구,
        # 추정 임계값이 아니라 "평균 대비 표준편차 비율"이라는 상대적 지표라
        # 모드별 절대 임계값보다 신뢰도가 높음)
        if std_val is not None and abs(res['avg_val']) > 1e-6:
            uniformity_ratio = std_val / abs(res['avg_val'])
            if uniformity_ratio < 0.15:
                st.caption(f"📊 구역 내 수치가 비교적 균일합니다 (변동계수 {uniformity_ratio:.2f}). 전체적으로 고른 상태로 추정됩니다.")
            elif uniformity_ratio < 0.4:
                st.caption(f"📊 구역 내 수치 편차가 다소 있습니다 (변동계수 {uniformity_ratio:.2f}). 일부 구간에서 평균과 다른 상태가 섞여 있을 수 있습니다.")
            else:
                st.caption(f"📊 구역 내 수치 편차가 큽니다 (변동계수 {uniformity_ratio:.2f}). 평균만으로는 안 보이는 국지적 이상이 있을 가능성이 있어, 지도에서 구체적 위치를 같이 확인하는 걸 권장합니다.")

        # [확장 2] 기간 내 실측 시계열 그래프.
        # 선택한 기간을 median()으로 뭉갠 점 하나가 아니라, 그 기간 안에
        # 실제로 촬영된 위성 이미지마다의 평균값을 점으로 찍는다.
        # [중요] 추세선 연장이나 미래 추정은 절대 하지 않는다 — 전부 과거
        # 실측치다. (예전에 뺐던 "14일 예측" 기능과는 본질적으로 다름)
        st.markdown("---")
        st.subheader("📈 기간 내 실측 추이")
        time_series = res.get('time_series') or []

        if len(time_series) < 2:
            st.caption("ℹ️ 선택한 기간 내 유효한 위성 촬영분이 2건 미만이라 추이 그래프를 표시할 수 없습니다. 기간을 넓혀보세요.")
        else:
            ts_dates = [d for d, v in time_series]
            ts_values = [v for d, v in time_series]

            fig_ts = go.Figure()
            fig_ts.add_trace(go.Scatter(
                x=ts_dates, y=ts_values,
                mode='lines+markers',
                name=f"{res['idx_name']} 실측치",
                line=dict(color="#3498db", width=2),
                marker=dict(size=8)
            ))
            fig_ts.add_hline(
                y=res['cfg']['threshold'], line_dash="dash", line_color="red",
                annotation_text="기준선", annotation_position="top right"
            )
            fig_ts.update_layout(
                height=300,
                margin=dict(l=20, r=20, t=30, b=20),
                yaxis_title=f"{res['idx_name']} 지수",
                xaxis_title="촬영일",
                yaxis=dict(range=[res['cfg']['min'] - 0.1, res['cfg']['max'] + 0.1])
            )
            st.plotly_chart(fig_ts, use_container_width=True)
            st.caption(f"ℹ️ 선택 기간 내 유효 촬영분 {len(time_series)}건의 실측 평균값입니다. (예측·추정 없음, 100% 실측 데이터)")

        # [C] 엑셀 보고서 다운로드
        st.markdown("<br>", unsafe_allow_html=True)
        with st.expander("🔽 상세 분석 보고서 다운로드 (Excel)"):
            st.markdown("현재 조회하신 데이터를 바탕으로 문서 첨부용 정식 보고서를 생성합니다.")

            change_rate_display = f"{change_rate:+.2f}%" if change_rate is not None else "비교 불가 (전년 데이터 없음)"

            df_report = pd.DataFrame({
                "관측 시점": [
                    "전년 동기 평균 (대조군)" if res['last_avg'] is not None else "전년 동기 데이터 없음",
                    f"올해 실측 평균 ({res['e_date'].strftime('%m/%d')})",
                    "구역 내 분포 통계 (표준편차)"
                ],
                f"원격 탐사 지수 ({res['idx_name']})": [
                    round(res['last_avg'], 4) if res['last_avg'] is not None else None,
                    round(res['avg_val'], 4),
                    round(res['std_val'], 4) if res.get('std_val') is not None else None
                ],
                "행정 정보 및 안전 진단 통계": [
                    f"관제 지자체: {res['region_name']} / 플랫폼 모드: {res['mode']}",
                    f"전년 동기 대비 변화율: {change_rate_display} / 위성 데이터 신뢰도: {res['reliability']}",
                    f"구역 내 최솟값: {res['min_val']:.4f} / 최댓값: {res['max_val']:.4f}" if res.get('min_val') is not None and res.get('max_val') is not None else "구역 내 최소/최댓값 데이터 없음"
                ]
            })
            st.dataframe(df_report, hide_index=True)

            excel_data = generate_excel_report(
                df_report, res['idx_name'], res['region_name'], res['mode'],
                change_rate if change_rate is not None else 0, res['reliability'], res['count']
            )

            st.download_button(
                label="📥 엑셀 보고서 다운로드",
                data=excel_data,
                file_name=f"KSat_Report_{res['e_date'].strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
