# report_builder.py
import io
import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import LineChart, Reference

def generate_excel_report(df, idx_name, region_name, current_mode, change_rate, reliability_score, count):
    """시계열 분석 데이터 및 openpyxl 자체 차트를 결합한 엑셀 바이너리 생성"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='종합관제보고서')
        
        workbook = writer.book
        worksheet = writer.sheets['종합관제보고서']
        
        header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        header_font = Font(name="맑은 고딕", size=11, bold=True, color="FFFFFF")
        data_font = Font(name="맑은 고딕", size=10)
        center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        left_align = Alignment(horizontal="left", vertical="center", wrap_text=True)
        thin_border = Border(
            left=Side(style='thin', color='D9D9D9'), right=Side(style='thin', color='D9D9D9'),
            top=Side(style='thin', color='D9D9D9'), bottom=Side(style='thin', color='D9D9D9')
        )
        
        for col_num in range(1, 4):
            cell = worksheet.cell(row=1, column=col_num)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_align
        
        for row in worksheet.iter_rows(min_row=2, max_row=len(df)+1, min_col=1, max_col=3):
            for cell in row:
                cell.font = data_font
                cell.border = thin_border
                if cell.column in [1, 2]:
                    cell.alignment = center_align
                    if cell.column == 2:
                        cell.number_format = '0.0000'
                else:
                    cell.alignment = left_align

        chart = LineChart()
        chart.title = f"📈 {region_name} 구역 [{idx_name}] 종합 관제 시계열 추이"
        chart.style = 13
        chart.y_axis.title = f"{idx_name} 지수"
        chart.x_axis.title = "관제 단계"
        chart.width = 17
        chart.height = 10
        
        data_ref = Reference(worksheet, min_col=2, min_row=1, max_row=len(df) + 1)
        cats_ref = Reference(worksheet, min_col=1, min_row=2, max_row=len(df) + 1)
        chart.add_data(data_ref, titles_from_data=True)
        chart.set_categories(cats_ref)
        chart.legend = None
        
        s1 = chart.series[0]
        s1.graphicalProperties.line.width = 30000
        s1.smooth = True
        worksheet.add_chart(chart, "E2")

        for col in worksheet.columns:
            max_len = 0
            col_letter = col[0].column_letter
            for cell in col:
                if cell.value:
                    val_str = str(cell.value)
                    cell_len = sum([2 if ord(char) > 128 else 1 for char in val_str])
                    if cell_len > max_len:
                        max_len = cell_len
            worksheet.column_dimensions[col_letter].width = max(max_len + 4, 16)
            
    return output.getvalue()