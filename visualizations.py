"""Local, user-selected spreadsheet charts, independent of fixed survey analysis."""
import base64
import io
import json
import math
import textwrap
import threading
import zipfile
from datetime import date, datetime

import numpy as np
import pandas as pd
from flask import Blueprint, jsonify, render_template, request
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib import colormaps
from matplotlib import dates as mdates
from matplotlib.ticker import MaxNLocator
from openpyxl import load_workbook
from sqlalchemy import or_

from models import ChartDataset, Document, Questionnaire, get_db

charts = Blueprint('charts', __name__)
MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 10000
MAX_COLUMNS = 100
RENDER_LOCK = threading.Lock()
TYPES = {'bar', 'barh', 'pie', 'line', 'histogram', 'scatter'}


@charts.errorhandler(ValueError)
def invalid_input(error):
    return jsonify(error=str(error)), 400


@charts.before_request
def upload_limit():
    if request.content_length and request.content_length > MAX_BYTES + 65536:
        return jsonify(error='上传内容不能超过 10 MB。'), 413


def read_workbook(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('Excel 文件不能超过 10 MB。')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 50 * 1024 * 1024:
                raise ValueError('Excel 解压后内容过大，请拆分工作簿。')
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError('无法读取工作簿，请上传有效的 .xlsx 文件。') from error
    result = []
    total_rows = 0
    try:
        if len(workbook.worksheets) > 20:
            raise ValueError('工作表不能超过 20 个。')
        for sheet in workbook.worksheets:
            if (sheet.max_column or 0) > MAX_COLUMNS:
                raise ValueError('每张工作表最多支持 100 列。')
            rows = sheet.iter_rows(values_only=True)
            header = next(rows, ())
            if not header or all(value is None for value in header):
                continue
            columns = []
            for index, value in enumerate(header):
                label = str(value).strip() if value is not None else f'未命名列 {index + 1}'
                label = label[:200] or f'未命名列 {index + 1}'
                candidate = label
                suffix = 2
                while candidate in columns:
                    candidate = f'{label} ({suffix})'
                    suffix += 1
                columns.append(candidate)
            records = []
            for row in rows:
                if all(value is None for value in row):
                    continue
                total_rows += 1
                if total_rows > MAX_ROWS:
                    raise ValueError('每个工作簿最多支持 10000 条非空数据，请拆分后上传。')
                values = []
                for value in row[:len(columns)]:
                    if isinstance(value, (date, datetime)):
                        value = value.isoformat()
                    elif isinstance(value, float) and not math.isfinite(value):
                        value = None
                    elif isinstance(value, str):
                        if len(value) > 10000:
                            raise ValueError('单元格文本过长，请精简后上传。')
                        value = value.strip() or None
                    values.append(value)
                records.append(values)
            if records:
                result.append((sheet.title, pd.DataFrame(records, columns=columns)))
    except ValueError:
        raise
    except Exception as error:
        raise ValueError('工作表内容无法读取，请重新保存为 .xlsx 后重试。') from error
    finally:
        workbook.close()
    if not result:
        raise ValueError('没有可分析的数据；第一行应为表头，后续行应为数据。')
    return result


def questionnaire_frame(item):
    """Read the response table owned by a questionnaire, if one was imported."""
    try:
        content = json.loads(item.content or '{}')
        table = content.get('survey_table')
        if table is None:
            return None
        if not isinstance(table, dict) or not isinstance(table.get('columns'), list) or not isinstance(table.get('data'), list):
            raise ValueError('问卷数据格式无效。')
        columns, rows = table['columns'], table['data']
        if (not 1 <= len(columns) <= MAX_COLUMNS or not 1 <= len(rows) <= MAX_ROWS
                or any(not isinstance(name, str) or not name.strip() for name in columns)
                or len(set(columns)) != len(columns)
                or any(not isinstance(row, list) or len(row) != len(columns)
                       or any(value is not None and not isinstance(value, (str, int, float, bool)) for value in row)
                       for row in rows)):
            raise ValueError('问卷表头或答卷结构无效。')
        return pd.DataFrame(table['data'], columns=table['columns'])
    except (TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError('问卷数据格式无效。') from error


def frame_for(key):
    db = get_db()
    if key.startswith('saved-'):
        try:
            item = db.get(ChartDataset, int(key[6:]))
        except ValueError:
            item = None
        if item:
            data = json.loads(item.data_json)
            return pd.DataFrame(data['data'], columns=data['columns']), item.name
    elif key.startswith('questionnaire-'):
        try:
            item = db.get(Questionnaire, int(key[14:]))
        except ValueError:
            item = None
        if item is not None:
            frame = questionnaire_frame(item)
            if frame is not None:
                return frame, item.title
    elif key.startswith('document-'):
        try:
            _, identifier, sheet_index = key.split('-')
            item = db.get(Document, int(identifier))
            index = int(sheet_index)
        except (ValueError, TypeError):
            item, index = None, -1
        if item and (item.file_name or '').lower().endswith('.xlsx'):
            sheets = read_workbook(item.file_data)
            if 0 <= index < len(sheets):
                name, frame = sheets[index]
                return frame, f'{item.title} / {name}'
    raise ValueError('数据集不存在，请刷新数据列表。')


def numeric(series):
    return pd.to_numeric(series, errors='coerce').replace([np.inf, -np.inf], np.nan)


def field_metadata(frame):
    fields = []
    for name in frame.columns:
        series = frame[name].dropna()
        kind = 'category'
        if len(series) and numeric(series).notna().all():
            kind = 'number'
        elif len(series) and series.astype(str).str.match(r'^\d{4}-\d{2}-\d{2}').all():
            if pd.to_datetime(series, errors='coerce', format='mixed').notna().all():
                kind = 'date'
        fields.append(dict(name=name, type=kind, missing=int(frame[name].isna().sum()),
                           unique=int(series.nunique())))
    return fields


@charts.get('/visualizations')
def page():
    return render_template('visualizations.html')


@charts.get('/api/visualizations/datasets')
def datasets():
    db = get_db()
    items = [dict(id=f'saved-{row.id}', name=row.name, rows=row.row_count, removable=True)
             for row in db.query(ChartDataset).order_by(ChartDataset.id.desc())]
    warnings = []
    for questionnaire in db.query(Questionnaire).order_by(Questionnaire.id.desc()):
        try:
            frame = questionnaire_frame(questionnaire)
            if frame is not None:
                items.append(dict(id=f'questionnaire-{questionnaire.id}', name=f'问卷 / {questionnaire.title}',
                                  rows=len(frame), removable=False))
        except ValueError as error:
            warnings.append(f'{questionnaire.title}：{error}')
    documents = db.query(Document).filter(or_(Document.category == 'source',
                                              Document.file_name.ilike('%.xlsx'))).all()
    for doc in documents:
        if not (doc.file_name or '').lower().endswith('.xlsx'):
            continue
        try:
            for index, (name, frame) in enumerate(read_workbook(doc.file_data)):
                items.append(dict(id=f'document-{doc.id}-{index}', name=f'{doc.title} / {name}',
                                  rows=len(frame), removable=False))
        except ValueError as error:
            warnings.append(f'{doc.title}：{error}')
    return jsonify(datasets=items, warnings=warnings)


@charts.post('/api/visualizations/datasets')
def upload():
    file = request.files.get('file')
    if not file or not (file.filename or '').lower().endswith('.xlsx'):
        raise ValueError('请选择 .xlsx 格式的 Excel 文件。')
    sheets = read_workbook(file.stream.read(MAX_BYTES + 1))
    db = get_db()
    added = []
    try:
        for title, frame in sheets:
            name = f'{file.filename.replace(chr(92), "/").split("/")[-1]} / {title}'[:240]
            row = ChartDataset(name=name, row_count=len(frame),
                               data_json=frame.to_json(orient='split', force_ascii=False, index=False))
            db.add(row)
            db.flush()
            added.append(f'saved-{row.id}')
        db.commit()
    except Exception:
        db.rollback()
        raise
    return jsonify(ids=added), 201


@charts.delete('/api/visualizations/datasets/<int:identifier>')
def delete_dataset(identifier):
    db = get_db()
    row = db.get(ChartDataset, identifier)
    if not row:
        raise ValueError('数据集不存在。')
    from local_tools import move_to_trash
    trash_id = move_to_trash(db, row, 'datasets')
    return jsonify(ok=True, trash_id=trash_id)


@charts.get('/api/visualizations/datasets/<key>')
def dataset_detail(key):
    frame, name = frame_for(key)
    fields = field_metadata(frame)
    numeric_fields = [field for field in fields if field['type'] == 'number']
    categories = [field for field in fields if field['type'] == 'category' and 1 < field['unique'] <= 30]
    dates = [field for field in fields if field['type'] == 'date']
    if categories:
        suggestion = dict(type='barh', x=categories[0]['name'], y='', aggregation='count')
    elif dates and numeric_fields:
        suggestion = dict(type='line', x=dates[0]['name'], y=numeric_fields[0]['name'], aggregation='mean')
    elif numeric_fields:
        suggestion = dict(type='histogram', x=numeric_fields[0]['name'], y='', aggregation='count')
    else:
        suggestion = dict(type='barh', x=fields[0]['name'], y='', aggregation='count')
    preview = json.loads(frame.head(8).to_json(orient='split', index=False, force_ascii=False))
    return jsonify(name=name, rows=len(frame), fields=fields, preview=preview, suggestion=suggestion)


def prepare_chart(frame, options):
    kind = options.get('type', 'barh')
    x, y = options.get('x'), options.get('y')
    aggregation = options.get('aggregation', 'count')
    if not isinstance(kind, str) or not isinstance(aggregation, str) or kind not in TYPES or aggregation not in {'count', 'sum', 'mean'}:
        raise ValueError('图表类型或统计方式无效。')
    if not isinstance(x, str) or x not in frame.columns:
        raise ValueError('请选择有效的横轴 / 分类字段。')
    try:
        top = int(options.get('top', 12))
        bins = int(options.get('bins', 15))
    except (ValueError, TypeError) as error:
        raise ValueError('显示类别数与分箱数必须为整数。') from error
    if not 1 <= top <= 30 or not 3 <= bins <= 50:
        raise ValueError('类别数须为 1–30，分箱数须为 3–50。')
    valid = frame[x].notna()
    needs_y = kind == 'scatter' or (kind not in {'histogram'} and aggregation != 'count')
    if needs_y:
        if not isinstance(y, str) or y not in frame.columns:
            raise ValueError('请选择有效的数值字段。')
        values = numeric(frame[y])
        valid &= values.notna()
    if kind in {'histogram', 'scatter'}:
        xs = numeric(frame[x])
        valid &= xs.notna()
        xs = xs[valid].tolist()
        ys = values[valid].tolist() if kind == 'scatter' else []
        data = dict(x=xs, y=ys, omitted_categories=0)
    else:
        xs = frame.loc[valid, x].astype(str)
        if aggregation == 'count':
            grouped = xs.value_counts(sort=False).astype(float)
        else:
            grouped = pd.DataFrame({'key': xs, 'value': values[valid]}).groupby('key', sort=False)['value'].agg(aggregation)
        if kind == 'pie' and ((grouped < 0).any() or grouped.sum() <= 0):
            raise ValueError('饼图需要非负数值且合计大于零。')
        if kind == 'line':
            keys = pd.Series(grouped.index)
            numbers = numeric(keys)
            dates = pd.to_datetime(keys, errors='coerce', format='mixed') if keys.str.match(r'^\d{4}-\d{2}-\d{2}').all() else pd.Series(dtype='datetime64[ns]')
            if numbers.notna().all():
                order = np.argsort(numbers.to_numpy(), kind='stable')
            elif len(dates) and dates.notna().all():
                order = np.argsort(dates.to_numpy(), kind='stable')
            else:
                raise ValueError('折线图的横轴应为日期或数值字段，请改用柱状图展示分类。')
            grouped = grouped.iloc[order]
        else:
            grouped = grouped.sort_values(ascending=False, kind='stable')
        omitted = max(0, len(grouped) - top)
        if kind == 'pie' and omitted:
            raise ValueError('饼图类别超过显示上限，请增加类别数或改用条形图，避免比例失真。')
        grouped = grouped.iloc[:top]
        data = dict(x=grouped.index.tolist(), y=grouped.tolist(), omitted_categories=omitted)
    used = int(valid.sum())
    if not used:
        raise ValueError('所选字段没有有效数据，请检查空值或数值格式。')
    if any(not math.isfinite(float(v)) for v in data['y']):
        raise ValueError('统计结果超出数值范围。')
    return dict(type=kind, x_field=x, y_field=y if needs_y else '', aggregation=aggregation,
                bins=bins, total=len(frame), used=used, excluded=len(frame) - used, **data)


def render_chart(data, title):
    palette = ['#008b99', '#e47839', '#7864b5', '#39976b', '#d45378', '#437fbd']
    kind = data['type']
    labels = ['\n'.join(textwrap.wrap(str(value), 16)) for value in data['x']] if kind not in {'histogram', 'scatter'} else []
    with RENDER_LOCK:
        height = max(5, sum(max(1, label.count('\n') + 1) for label in labels) * .28 + 2) if kind == 'barh' else 6
        width = max(11, len(labels) * .7) if kind in {'bar', 'line'} else 11
        if kind == 'pie':
            height = max(6, sum(label.count('\n') + 1 for label in labels) * .22 + 1)
        figure = Figure(figsize=(width, height), layout='constrained', facecolor='#ffffff')
        axes = figure.subplots()
        if kind == 'bar':
            axes.bar(range(len(labels)), data['y'], color=palette[0])
            axes.set_xticks(range(len(labels)), labels, rotation=45, ha='right', fontsize=9)
        elif kind == 'barh':
            axes.barh(range(len(labels)), data['y'], color=palette[0])
            axes.set_yticks(range(len(labels)), labels, fontsize=9)
            axes.invert_yaxis()
        elif kind == 'pie':
            total = sum(data['y'])
            legend_labels = [f'{label}  ({value / total:.2%})' for label, value in zip(labels, data['y'])]
            colors = colormaps['turbo'](np.linspace(.08, .92, len(labels))) if len(labels) > 6 else palette
            # Small shares stay in the legend so adjacent sliver labels cannot overlap.
            wedges, _, _ = axes.pie(data['y'], autopct=lambda pct: f'{pct:.1f}%' if pct >= 5 else '', colors=colors, pctdistance=.8,
                                    textprops={'fontsize': 9})
            axes.legend(wedges, legend_labels, loc='center left', bbox_to_anchor=(1, .5), fontsize=9)
        elif kind == 'line':
            coordinates = numeric(pd.Series(data['x']))
            if coordinates.notna().all():
                axes.plot(coordinates.to_numpy(), data['y'], marker='o', color=palette[0])
                axes.xaxis.set_major_locator(MaxNLocator(nbins=8))
            else:
                coordinates = pd.to_datetime(pd.Series(data['x']), format='mixed')
                axes.plot(coordinates, data['y'], marker='o', color=palette[0])
                locator = mdates.AutoDateLocator(minticks=3, maxticks=8)
                axes.xaxis.set_major_locator(locator)
                axes.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
            axes.tick_params(axis='x', labelrotation=30, labelsize=9)
        elif kind == 'histogram':
            axes.hist(data['x'], bins=data['bins'], color=palette[0], edgecolor='white')
        else:
            axes.scatter(data['x'], data['y'], color=palette[0], alpha=.65, s=25)
        metric = {'count': '记录数', 'sum': '合计', 'mean': '平均值'}[data['aggregation']]
        if kind != 'pie':
            measure = f'{data["y_field"]} · {metric}' if data['y_field'] else metric
            x_label, y_label = data['x_field'], measure
            if kind == 'barh':
                x_label, y_label = measure, data['x_field']
            elif kind == 'scatter':
                y_label = data['y_field']
            elif kind == 'histogram':
                y_label = '记录数'
            axes.set_xlabel('\n'.join(textwrap.wrap(x_label, 55)))
            axes.set_ylabel('\n'.join(textwrap.wrap(y_label, 25)))
            axes.grid(axis='x' if kind == 'barh' else 'y', alpha=.18)
            axes.set_axisbelow(True)
        axes.set_title('\n'.join(textwrap.wrap(title, 45)), pad=16, fontsize=14)
        output = io.BytesIO()
        FigureCanvasAgg(figure).print_png(output)
        return base64.b64encode(output.getvalue()).decode('ascii')


@charts.post('/api/visualizations/chart')
def chart():
    options = request.get_json(silent=True)
    if not isinstance(options, dict):
        raise ValueError('图表配置必须为 JSON 对象。')
    key = options.get('dataset')
    if not isinstance(key, str):
        raise ValueError('请先选择数据集。')
    frame, name = frame_for(key)
    data = prepare_chart(frame, options)
    title = str(options.get('title') or f'{data["x_field"]}分析')[:120]
    return jsonify(image='data:image/png;base64,' + render_chart(data, title),
                   title=title, dataset=name, statistics=data)
