"""Generate explicitly fictional spreadsheet data without reading any files."""
import argparse
from datetime import date, timedelta
from pathlib import Path
import random

from openpyxl import Workbook


def generate(destination):
    rng = random.Random(20261116)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '模拟问卷'
    sheet.append(['样本编号', '群体', '年级', '专业类别', '城市认同得分', '就业意愿得分', '填写日期'])
    for index in range(1, 121):
        group = rng.choice(['学生', '学生', '学生', '教师'])
        identity = rng.randint(1, 5)
        employment = max(1, min(5, identity + rng.choice([-1, 0, 0, 1])))
        sheet.append([f'DEMO-{index:03d}', group, rng.choice(['一年级', '二年级', '三年级', '四年级']) if group == '学生' else '不适用',
                      rng.choice(['人文社科', '理工', '经管', '艺术']), identity, employment,
                      date(2026, 1, 1) + timedelta(days=(index - 1) // 4)])
    for row in sheet.iter_rows(min_row=2, min_col=7):
        row[0].number_format = 'yyyy-mm-dd'
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = 22
    monthly = workbook.create_sheet('模拟月度汇总')
    monthly.append(['月份', '参与人数', '平均认同得分'])
    for month in range(1, 7):
        monthly.append([date(2026, month, 1), rng.randint(15, 40), round(rng.uniform(2.5, 4.5), 2)])
        monthly.cell(month + 1, 1).number_format = 'yyyy-mm-dd'
    for column in monthly.columns:
        monthly.column_dimensions[column[0].column_letter].width = 22
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination)
    workbook.close()
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('sample-survey.xlsx'))
    args = parser.parse_args()
    print('Fictional example created:', generate(args.output))
