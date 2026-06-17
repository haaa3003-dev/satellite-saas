import pandas as pd
import io

def generate_excel_report(data_list):
    """분석 데이터를 엑셀(.xlsx) 파일로 변환하여 반환하는 함수"""
    # 메모리 상에 엑셀 파일을 생성 (디스크 용량 절약)
    output = io.BytesIO()
    
    # pandas를 이용해 데이터를 엑셀 형태로 작성
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df = pd.DataFrame(data_list)
        df.to_excel(writer, index=False, sheet_name='관제결과')
        
    return output.getvalue()