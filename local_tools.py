"""Recoverable deletion and local document previews."""
import base64
from datetime import date, datetime
import io
import json
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

from flask import Blueprint, jsonify, render_template, request
from openpyxl import load_workbook

from models import ChartDataset, Document, InterviewRecording, ItemTag, Questionnaire, Tag, TrashEntry, get_db
from backup_store import create_backup, list_backups

local = Blueprint('local', __name__)
MODELS = {'documents': Document, 'interviews': InterviewRecording,
          'questionnaires': Questionnaire, 'datasets': ChartDataset}
TAG_TYPES = {'documents': 'document', 'interviews': 'interview', 'questionnaires': 'questionnaire'}


def move_to_trash(db, item, kind):
    fields = {}
    for column in item.__table__.columns:
        value = getattr(item, column.name)
        if isinstance(value, bytes):
            value = {'binary': base64.b64encode(value).decode('ascii')}
        elif isinstance(value, (date, datetime)):
            value = {'date': value.isoformat(), 'datetime': isinstance(value, datetime)}
        fields[column.name] = value
    tags = []
    if kind in TAG_TYPES:
        links = db.query(ItemTag).filter_by(item_id=item.id, item_type=TAG_TYPES[kind])
        tags = [link.tag_id for link in links]
        links.delete(synchronize_session=False)
    entry = TrashEntry(item_type=kind, original_id=item.id, title=getattr(item, 'title', None) or item.name,
                       payload=json.dumps({'fields': fields, 'tags': tags}, ensure_ascii=False))
    db.add(entry)
    db.delete(item)
    db.commit()
    return entry.id


@local.get('/local-data')
def local_data_page():
    return render_template('local_data.html')


@local.get('/api/local/trash')
def trash_list():
    entries = get_db().query(TrashEntry).order_by(TrashEntry.deleted_at.desc()).all()
    return jsonify(items=[dict(id=row.id, item_type=row.item_type, title=row.title,
                               original_id=row.original_id, deleted_at=row.deleted_at.isoformat()) for row in entries])


@local.post('/api/local/trash/<int:identifier>/restore')
def restore_item(identifier):
    db = get_db()
    entry = db.get(TrashEntry, identifier)
    if not entry:
        return jsonify(error='回收站记录不存在'), 404
    model = MODELS[entry.item_type]
    data = json.loads(entry.payload)
    fields = data['fields']
    for name, value in fields.items():
        if isinstance(value, dict) and 'binary' in value:
            fields[name] = base64.b64decode(value['binary'])
        elif isinstance(value, dict) and 'date' in value:
            fields[name] = datetime.fromisoformat(value['date']) if value['datetime'] else date.fromisoformat(value['date'])
    # A deleted ID may have been reused; never overwrite the newer record.
    if db.get(model, fields['id']):
        fields.pop('id')
    item = model(**fields)
    db.add(item)
    db.flush()
    if entry.item_type in TAG_TYPES:
        for tag in db.query(Tag).filter(Tag.id.in_(data['tags'])).all():
            db.add(ItemTag(item_id=item.id, item_type=TAG_TYPES[entry.item_type], tag_id=tag.id))
    restored_id = item.id
    db.delete(entry)
    db.commit()
    return jsonify(ok=True, restored_id=restored_id)


@local.delete('/api/local/trash/<int:identifier>')
def erase_item(identifier):
    db = get_db()
    entry = db.get(TrashEntry, identifier)
    if not entry:
        return jsonify(error='回收站记录不存在'), 404
    db.delete(entry)
    db.commit()
    return jsonify(ok=True)


@local.get('/api/local/backups')
def backup_list():
    return jsonify(items=list_backups())


@local.post('/api/local/backups')
def backup_now():
    try:
        path = create_backup('manual')
    except (OSError, RuntimeError) as error:
        return jsonify(error=f'备份未完成：{error}'), 500
    return jsonify(ok=True, name=path.name), 201


def office_archive(raw):
    archive = zipfile.ZipFile(io.BytesIO(raw))
    if sum(item.file_size for item in archive.infolist()) > 60 * 1024 * 1024:
        archive.close()
        raise ValueError('文档解压内容过大，请拆分后预览。')
    return archive


def document_content(doc, sheet_name=None, page=1):
    suffix = Path(doc.file_name or '').suffix.lower()
    raw = doc.file_data
    result = dict(kind='unsupported', sheets=[], rows=[], paragraphs=[], text='', page=page, more=False)
    if not raw:
        result['message'] = '这条记录没有附件。'
        return result
    if suffix == '.pdf':
        result['kind'] = 'pdf'
    elif suffix in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}:
        result['kind'] = 'image'
    elif suffix == '.xlsx':
        with office_archive(raw):
            pass
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        try:
            if sheet_name and sheet_name not in workbook.sheetnames:
                raise ValueError('工作表不存在。')
            sheet = workbook[sheet_name] if sheet_name else workbook.worksheets[0]
            result.update(kind='excel', sheets=workbook.sheetnames, sheet=sheet.title)
            iterator = sheet.iter_rows(min_row=(page - 1) * 50 + 1, max_row=page * 50 + 1,
                                       max_col=min(sheet.max_column or 1, 50), values_only=True)
            records = list(iterator)
            result['rows'] = [[str(value) if value is not None else '' for value in row] for row in records[:50]]
            result['more'] = len(records) > 50
            result['message'] = '每页显示 50 行，最多预览前 50 列；公式显示已保存的缓存值。'
        finally:
            workbook.close()
    elif suffix == '.docx':
        with office_archive(raw) as archive:
            xml = archive.read('word/document.xml')
        if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:
            raise ValueError('文档包含不支持的 XML 声明。')
        tree = ET.fromstring(xml)
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        paragraphs = []
        body = tree.find('w:body', ns)
        if body is None:
            raise ValueError('Word 文档没有正文。')
        for element in body:
            if element.tag == '{' + ns['w'] + '}p':
                paragraphs.append(''.join(node.text or '' for node in element.findall('.//w:t', ns)))
            elif element.tag == '{' + ns['w'] + '}tbl':
                for row in element.findall('w:tr', ns):
                    paragraphs.append(' | '.join(''.join(node.text or '' for node in cell.findall('.//w:t', ns))
                                                 for cell in row.findall('w:tc', ns)))
        result.update(kind='word', paragraphs=paragraphs[:2000],
                      message='正文与表格文字预览，不还原 Word 排版、图片和修订。' + ('仅展示前 2000 段。' if len(paragraphs) > 2000 else ''))
    elif suffix in {'.txt', '.csv', '.md', '.log'}:
        limited = raw[:1024 * 1024]
        try:
            text = limited.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                text = limited.decode('gb18030')
            except UnicodeDecodeError:
                raise ValueError('文本编码无法识别，请另存为 UTF-8 后上传。')
        result.update(kind='text', text=text, message='仅预览前 1 MB。' if len(raw) > len(limited) else '')
    else:
        result['message'] = '此格式暂不支持在线预览。旧版 .doc / .xls 请另存为 .docx / .xlsx，或下载查看。'
    return result


@local.get('/documents/<int:identifier>/preview')
def document_preview(identifier):
    doc = get_db().get(Document, identifier)
    if not doc:
        return '文档不存在或已移入回收站', 404
    try:
        content = document_content(doc, request.args.get('sheet'), max(1, min(2000, request.args.get('page', 1, type=int))))
    except Exception as error:
        content = dict(kind='unsupported', message=f'无法预览此文件：{error}', sheets=[])
    return render_template('document_preview.html', document=doc, preview=content)
