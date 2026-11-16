import os
import io
import json
import mimetypes
import base64
import hashlib
from datetime import datetime, date
from flask import Flask, render_template, request, jsonify, send_file, redirect, Response, session, url_for
from flask_cors import CORS
from werkzeug.utils import secure_filename
from sqlalchemy import func, text, or_

from models import init_app, get_db, Questionnaire, InterviewRecording, Document, Tag, ItemTag, User, db_session
import html as h

app = Flask(__name__)
from config import session_secret
app.secret_key = session_secret()
init_app()

from visualizations import charts, questionnaire_frame, read_workbook, MAX_BYTES
app.register_blueprint(charts)
from local_tools import local, move_to_trash
app.register_blueprint(local)

MAX_FILE_SIZE = 50 * 1024 * 1024


def attachment_name(filename):
    return (filename or '').replace('\\', '/').rsplit('/', 1)[-1] or 'upload.bin'


@app.teardown_appcontext
def close_database_session(error=None):
    db_session.remove()


@app.route('/api/documents/<int:identifier>', methods=['GET'])
@app.route('/api/interviews/<int:identifier>', methods=['GET'])
def api_attachment_item(identifier):
    model = Document if request.path.startswith('/api/documents/') else InterviewRecording
    item = get_db().get(model, identifier)
    if not item:
        return jsonify(error='记录不存在'), 404
    return jsonify(item=item.to_dict())


def _questionnaire_frame(questionnaire, nrows=None):
    frame = questionnaire_frame(questionnaire)
    if frame is not None:
        return frame.head(nrows) if nrows is not None else frame
    return _load_survey_frame(nrows=nrows)


def _load_survey_frame(nrows=None):
    """Return the survey spreadsheet as a DataFrame.

    Prefers the copy stored in the database so a deployment does not depend on
    the original .xlsx file sitting next to the source code.
    """
    import pandas as pd
    db = get_db()
    doc = db.query(Document).filter(Document.category == "source").first()
    if doc and doc.file_data:
        return pd.read_excel(io.BytesIO(doc.file_data), nrows=nrows)
    return None



@app.route("/documents")
def documents_page():
    db = get_db()
    category = request.args.get("category", "")
    q = db.query(Document).filter(Document.category != "source")
    if category in ("student", "teacher"):
        q = q.filter(Document.category == category)
    docs = q.order_by(Document.created_at.desc()).all()
    doc_list = []
    for d in docs:
        item = d.to_dict()
        item["has_thumb"] = d.thumbnail is not None
        if d.thumbnail:
            item["thumbnail_b64"] = base64.b64encode(d.thumbnail).decode("utf-8")
        doc_list.append(item)
    return render_template("documents.html", tags=[t.to_dict() for t in db.query(Tag).all()], documents=doc_list)

@app.route("/api/documents")
def api_documents():
    db = get_db()
    page = max(1, request.args.get("page",1,type=int)); per_page = max(1, min(100, request.args.get("per_page",20,type=int)))
    tag_id = request.args.get("tag_id",None,type=int); doc_type = request.args.get("type",""); search = request.args.get("search","").strip()
    # the survey spreadsheet lives under category 'source'; it is browsed from the questionnaire page
    query = db.query(Document).filter(Document.category != "source")
    if tag_id:
        ids = db.query(ItemTag.item_id).filter(ItemTag.item_type=="document",ItemTag.tag_id==tag_id)
        query = query.filter(Document.id.in_(ids))
    if doc_type == "image": query = query.filter(Document.file_type.like("image/%"))
    elif doc_type == "document": query = query.filter(~Document.file_type.like("image/%"))
    category = request.args.get("category", "")
    if category in ("student", "teacher"):
        query = query.filter(Document.category == category)
    if search: query = query.filter(Document.title.ilike(f"%{search}%"))
    total = query.count()
    items = query.order_by(Document.created_at.desc()).offset((page-1)*per_page).limit(per_page).all()
    return jsonify({"items":[d.to_dict(include_file=True) for d in items],"total":total,"page":page,"per_page":per_page,"pages":(total+per_page-1)//per_page})

@app.route("/api/documents",methods=["POST"])
def api_create_document():
    title = request.form.get("title","").strip()
    if not title: return jsonify({"error":"标题不能为空"}),400
    category = request.form.get("category") or "student"
    if category not in ("student", "teacher"): category = "student"
    db = get_db(); doc = Document(title=title,description=request.form.get("description",""),category=category)
    file = request.files.get("file")
    if file and file.filename:
        file_data = file.read()
        if len(file_data) > MAX_FILE_SIZE: return jsonify({"error":"文件过大"}),400
        doc.file_data = file_data; doc.file_name = attachment_name(file.filename)
        doc.file_type = file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
        doc.file_size = len(file_data)
        if doc.file_type and doc.file_type.startswith("image/"):
            try:
                from PIL import Image as PILImage
                img = PILImage.open(io.BytesIO(file_data))
                doc.width, doc.height = img.size
                img.thumbnail((300,300),PILImage.LANCZOS)
                buf = io.BytesIO(); img.save(buf,format="WEBP",quality=80)
                doc.thumbnail = buf.getvalue()
            except: pass
    db.add(doc); db.flush()
    for tid in request.form.getlist("tag_ids[]"): db.add(ItemTag(item_id=doc.id,item_type="document",tag_id=int(tid)))
    db.commit()
    return jsonify({"item":doc.to_dict(include_file=True)}),201

@app.route("/api/documents/<int:did>/download")
def api_download_document(did):
    db = get_db(); doc = db.query(Document).get(did)
    if not doc or not doc.file_data: return jsonify({"error":"文件未找到"}),404
    return send_file(io.BytesIO(doc.file_data),mimetype=doc.file_type or "application/octet-stream",as_attachment=True,download_name=doc.file_name)

@app.route("/api/documents/<int:did>/view")
def api_view_document(did):
    db = get_db(); doc = db.query(Document).get(did)
    if not doc or not doc.file_data: return jsonify({"error":"文件未找到"}),404
    return Response(doc.file_data,mimetype=doc.file_type or "application/octet-stream")

@app.route("/api/documents/<int:did>",methods=["PUT"])
def api_update_document(did):
    db = get_db(); doc = db.query(Document).get(did)
    if not doc: return jsonify({"error":"未找到"}),404
    data = request.get_json()
    if data.get("title"): doc.title = data["title"]
    if "description" in data: doc.description = data.get("description","")
    if data.get("category") in ("student", "teacher"): doc.category = data["category"]
    if "tag_ids" in data:
        db.query(ItemTag).filter(ItemTag.item_id==did,ItemTag.item_type=="document").delete()
        for tid in data["tag_ids"]: db.add(ItemTag(item_id=did,item_type="document",tag_id=tid))
    db.commit()
    return jsonify({"item":doc.to_dict(include_file=True)})

@app.route("/api/documents/<int:did>",methods=["DELETE"])
def api_delete_document(did):
    db = get_db(); doc = db.query(Document).get(did)
    if not doc: return jsonify({"error":"未找到"}),404
    trash_id = move_to_trash(db, doc, 'documents')
    return jsonify(ok=True, trash_id=trash_id)

@app.route("/interviews")
def interviews_page():
    db = get_db()
    category = request.args.get("category", "")
    q = db.query(InterviewRecording)
    if category in ("student", "teacher"):
        q = q.filter(InterviewRecording.category == category)
    items = q.order_by(InterviewRecording.created_at.desc()).all()
    return render_template("interviews.html", tags=[t.to_dict() for t in db.query(Tag).all()], interviews=[i.to_dict() for i in items])

@app.route("/api/interviews")
def api_interviews():
    db = get_db()
    page = max(1, request.args.get("page",1,type=int)); per_page = max(1, min(100, request.args.get("per_page",20,type=int)))
    tag_id = request.args.get("tag_id",None,type=int); search = request.args.get("search","").strip()
    query = db.query(InterviewRecording)
    if tag_id:
        ids = db.query(ItemTag.item_id).filter(ItemTag.item_type=="interview",ItemTag.tag_id==tag_id)
        query = query.filter(InterviewRecording.id.in_(ids))
    category = request.args.get("category", "")
    if category in ("student", "teacher"):
        query = query.filter(InterviewRecording.category == category)
    if search:
        query = query.filter(InterviewRecording.title.ilike(f"%{search}%"))
    total = query.count()
    items = query.order_by(InterviewRecording.created_at.desc()).offset((page-1)*per_page).limit(per_page).all()
    return jsonify({"items":[i.to_dict() for i in items],"total":total,"page":page,"per_page":per_page,"pages":(total+per_page-1)//per_page})

@app.route("/api/interviews/<int:iid>/download")
def api_download_interview(iid):
    db = get_db(); i = db.query(InterviewRecording).get(iid)
    if not i or not i.file_data: return jsonify({"error":"文件未找到"}),404
    return send_file(io.BytesIO(i.file_data),mimetype=i.file_type or "application/octet-stream",as_attachment=True,download_name=i.file_name)

@app.route("/api/interviews/<int:iid>/view")
def api_view_interview(iid):
    db = get_db(); i = db.query(InterviewRecording).get(iid)
    if not i or not i.file_data: return jsonify({"error":"文件未找到"}),404
    return Response(i.file_data,mimetype=i.file_type or "application/octet-stream")

@app.route("/tags")
def tags_page():
    return redirect("/analysis")

@app.route("/api/tags")
def api_tags():
    db = get_db()
    result = []
    for t in db.query(Tag).order_by(Tag.name).all():
        d = t.to_dict()
        d["item_count"] = db.query(ItemTag).filter(ItemTag.tag_id==t.id).count()
        result.append(d)
    return jsonify({"items":result})

@app.route("/api/tags",methods=["POST"])
def api_create_tag():
    data = request.get_json()
    if not data or not data.get("name","").strip(): return jsonify({"error":"标签名不能为空"}),400
    db = get_db(); name = data["name"].strip()
    if db.query(Tag).filter(Tag.name==name).first(): return jsonify({"error":"标签已存在"}),409
    tag = Tag(name=name,color=data.get("color","#6366f1"))
    db.add(tag); db.commit()
    return jsonify({"item":tag.to_dict()}),201

@app.route("/search")
def search_page():
    return render_template("search.html")

@app.route("/api/search")
def api_search():
    q = request.args.get("q","").strip()
    if not q: return jsonify({"items":[]})
    db = get_db(); results = []; seen = set()
    config = [
        (Questionnaire,"questionnaires_fts","问卷","📋","questionnaires"),
        (InterviewRecording,"interviews_fts","访谈记录","🎬","interviews"),
        (Document,"documents_fts","文档","📄","documents"),
    ]
    for model_cls, fts_table, type_name, icon, url_part in config:
        try:
            rows = db.execute(text(f"SELECT rowid, rank FROM \"{fts_table}\" WHERE \"{fts_table}\" MATCH :q ORDER BY rank LIMIT 20"),{"q":q}).fetchall()
            for row in rows:
                if (url_part, row[0]) not in seen:
                    seen.add((url_part, row[0]))
                    item = db.query(model_cls).get(row[0])
                    if item:
                        d = item.to_dict()
                        d["_type"] = type_name; d["_type_icon"] = icon; d["_url"] = f"/{url_part}?highlight={item.id}"
                        results.append(d)
        except: pass
    if q:
        like_q = "%" + q + "%"
        for model_cls, col_name, type_name, icon, url_part in config:
            try:
                fields = [getattr(model_cls, name) for name in ('title', 'description', 'content', 'notes', 'transcript', 'interviewee', 'interviewer') if hasattr(model_cls, name)]
                for item in db.query(model_cls).filter(or_(*(col.ilike(like_q) for col in fields))).limit(20).all():
                    if (url_part, item.id) not in seen:
                        seen.add((url_part, item.id))
                        d = item.to_dict()
                        d["_type"] = type_name; d["_type_icon"] = icon; d["_url"] = f"/{url_part}?highlight={item.id}"
                        results.append(d)
            except: pass
    return jsonify({"items":results,"query":q})

@app.route("/api/detail/<item_type>/<int:item_id>")
def api_detail_html(item_type, item_id):
    db = get_db()
    if item_type == "questionnaires":
        q = db.query(Questionnaire).get(item_id)
        if not q: return "\u672a\u627e\u5230"
        tgs = "".join([f"<span class=\"tag-badge\" style=\"background:{t.color}22;color:{t.color};border:1px solid {t.color}44\">{h.escape(t.name)}</span>" for t in q.tag_items])
        parts = []
        parts.append("<div class=\"detail-section\"><div class=\"detail-label\">\u6807\u9898</div><div class=\"detail-value\">" + h.escape(q.title) + "</div></div>")
        parts.append("<div class=\"detail-section\"><div class=\"detail-label\">\u63cf\u8ff0</div><div class=\"detail-value\">" + (h.escape(q.description) or "\u65e0") + "</div></div>")
        parts.append("<div class=\"detail-section\"><div class=\"detail-label\">\u56de\u7b54\u6570</div><div class=\"detail-value\">" + str(q.respondent_count) + "</div></div>")
        parts.append("<div class=\"detail-section\"><div class=\"detail-label\">\u6807\u7b7e</div><div class=\"detail-value\">" + (tgs or "\u65e0\u6807\u7b7e") + "</div></div>")
        parts.append("<div class=\"detail-section\"><div class=\"detail-label\">\u521b\u5efa\u65f6\u95f4</div><div class=\"detail-value\">" + (q.created_at.strftime("%Y-%m-%d %H:%M") if q.created_at else "-") + "</div></div>")
        own_frame = questionnaire_frame(q)
        src_doc = db.query(Document).filter(Document.category == 'source').first() if own_frame is None else None
        btn = '<button class="btn btn-sm btn-outline" onclick="loadQuestionnairePart(' + str(item_id) + ',\'columns\')">\u95ee\u5377\u9898\u76ee</button> '
        btn += '<button class="btn btn-sm btn-outline" onclick="loadQuestionnairePart(' + str(item_id) + ',\'preview\')">\u6570\u636e\u9884\u89c8</button> '
        if own_frame is not None:
            btn += f'<a class="btn btn-sm btn-outline" href="/api/questionnaires/{item_id}/download">下载数据表</a> '
            btn += '<a class="btn btn-sm btn-outline" href="/visualizations">生成图表</a>'
        elif src_doc:
            btn += '<a class="btn btn-sm btn-outline" href="/api/documents/' + str(src_doc.id) + '/download">\u4e0b\u8f7d\u539f\u59cb\u6570\u636e</a>'
        parts.append('<div class="detail-section"><div class="detail-label">\u95ee\u5377\u6570\u636e</div><div class="detail-value">' + btn + '</div></div>')
        return "\n".join(parts)
    elif item_type == "documents":
        d = db.query(Document).get(item_id)
        if not d: return "not found"
        return (f'<a class="btn btn-primary" href="/documents/{item_id}/preview" target="_blank" rel="noopener">在线预览</a>'
                f'<iframe title="文档预览" src="/documents/{item_id}/preview" style="width:100%;height:65vh;border:0"></iframe>')
    elif item_type == "interviews":
        i = db.query(InterviewRecording).get(item_id)
        if not i: return "not found"
        parts = []
        parts.append("<div><b>标题：</b> " + h.escape(str(i.title)) + "</div>")
        parts.append("<div><b>受访者：</b> " + h.escape(str(i.interviewee or "")) + "</div>")
        parts.append('<div style="white-space:pre-wrap">' + h.escape(i.transcript or i.notes or '') + '</div>')
        if i.file_name:
            parts.append("<div><a href='/api/interviews/" + str(item_id) + "/download'>download</a></div>")
            if i.file_type and i.file_type.startswith("video/"):
                parts.append("<video controls src='/api/interviews/" + str(item_id) + "/view' style='max-width:100%;max-height:400px'></video>")
            elif i.file_type and i.file_type.startswith('audio/'):
                parts.append(f'<audio controls src="/api/interviews/{item_id}/view"></audio>')
        return "<br>".join(parts)
    return "invalid type"
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import glob as _glob


def _setup_cjk_font():
    """Register whatever CJK font this machine has so chart labels render.

    Windows ships SimHei / YaHei, Linux usually needs fonts-noto-cjk or
    wqy-zenhei, macOS has PingFang. Any of them will do.
    """
    candidates = [
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    candidates += _glob.glob("/usr/share/fonts/**/*CJK*.tt*", recursive=True)
    candidates += _glob.glob("/usr/share/fonts/**/*wqy*.tt*", recursive=True)

    for path in candidates:
        if os.path.exists(path):
            try:
                font_manager.fontManager.addfont(path)
            except Exception:
                pass

    family = ["SimHei", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC",
              "Source Han Sans SC", "WenQuanYi Zen Hei", "PingFang SC", "Heiti SC"]
    plt.rcParams["font.sans-serif"] = family + list(plt.rcParams.get("font.sans-serif", []))
    plt.rcParams["axes.unicode_minus"] = False


_setup_cjk_font()

@app.route("/api/analysis/chart/<chart_name>")
def api_analysis_chart(chart_name):
    df = _load_survey_frame()
    if df is None:
        return "", 404
    fig, ax = plt.subplots(figsize=(10,5.5))
    fig.patch.set_facecolor("#f8fafc"); ax.set_facecolor("#f8fafc")
    colors = ["#6366f1","#10b981","#f59e0b","#ec4899","#8b5cf6","#06b6d4","#f97316","#14b8a6"]

    if chart_name == "school":
        col = "1.\u4f60\u6240\u5728\u7684\u5b66\u6821\u662f\uff1a"
        counts = df[col].value_counts()
        bars = ax.barh(list(counts.index), list(counts.values), color=colors[:len(counts)])
        for bar, v in zip(bars, counts.values): ax.text(bar.get_width()+2, bar.get_y()+bar.get_height()/2, str(v), ha="left", va="center")
        ax.set_xlabel("\u4eba\u6570"); ax.set_title("\u5404\u6821\u53c2\u4e0e\u4eba\u6570\u5206\u5e03"); ax.invert_yaxis()
    elif chart_name == "gender":
        col = "2.\u4f60\u7684\u6027\u522b:"
        counts = df[col].value_counts()
        bars = ax.bar(list(counts.index), list(counts.values), color=colors[:len(counts)], width=0.5)
        for bar, v in zip(bars, counts.values): ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+2, str(v)+"\u4eba", ha="center", fontsize=11)
        ax.set_ylim(0, max(counts.values)*1.1); ax.set_ylabel("\u4eba\u6570"); ax.set_title("\u6027\u522b\u5206\u5e03")
    elif chart_name == "stay":
        col = "29.\u6bd5\u4e1a\u540e\u4f60\u662f\u5426\u613f\u610f\u7559\u5728\u9752\u5c9b\u53d1\u5c55?"
        counts = df[col].value_counts()
        order = ["\u975e\u5e38\u613f\u610f","\u6bd4\u8f83\u613f\u610f","\u4e0d\u786e\u5b9a","\u4e0d\u592a\u613f\u610f","\u975e\u5e38\u4e0d\u613f\u610f"]
        ordered = {k:counts.get(k,0) for k in order if k in counts.index}
        ax.pie(list(ordered.values()), labels=list(ordered.keys()), autopct="%1.1f%%", colors=colors[:len(ordered)], startangle=90)
        ax.set_title("\u7559\u9752\u610f\u613f\u5206\u5e03")
    elif chart_name == "hometown":
        col = "7.\u4f60\u7684\u751f\u6e90\u5730:"
        counts = df[col].value_counts()
        ax.pie(list(counts.values), labels=list(counts.index), autopct="%1.1f%%", colors=colors[:len(counts)], startangle=90)
        ax.set_title("\u751f\u6e90\u5730\u5206\u5e03")
    elif chart_name == "job_path":
        col = "37.\u4f60\u9996\u9009\u7684\u5c31\u4e1a\u8def\u5f84\u662f?"
        counts = df[col].value_counts()
        ax.pie(list(counts.values), labels=list(counts.index), autopct="%1.1f%%", colors=colors[:len(counts)], startangle=90)
        ax.set_title("\u9996\u9009\u5c31\u4e1a\u8def\u5f84")

    elif chart_name == "belonging":
        col = "28.\u4f60\u5bf9\u9752\u5c9b\u7684\u5f52\u5c5e\u611f\u5982\u4f55?"
        counts = df[col].value_counts()
        order = ["5(\u6781\u5f3a\u5f52\u5c5e\u611f)","4","3","2","1(\u6beb\u65e0\u5f52\u5c5e\u611f)"]
        ordered = {k:counts.get(k,0) for k in order if k in counts.index}
        bars = ax.barh(list(ordered.keys()), list(ordered.values()), color=colors[:len(ordered)])
        for bar, v in zip(bars, ordered.values()): ax.text(bar.get_width()+2, bar.get_y()+bar.get_height()/2, str(v), ha="left", va="center")
        ax.set_xlabel("\u4eba\u6570"); ax.set_title("\u5bf9\u9752\u5c9b\u5f52\u5c5e\u611f\u5206\u5e03"); ax.invert_yaxis()

    elif chart_name == "job_location":
        col = "36.\u4f60\u9996\u9009\u7684\u672a\u6765\u5c31\u4e1a\u5730\u70b9\u662f?"
        counts = df[col].value_counts()
        ax.pie(list(counts.values), labels=list(counts.index), autopct="%1.1f%%", colors=colors[:len(counts)], startangle=90)
        ax.set_title("\u9996\u9009\u5c31\u4e1a\u5730\u70b9")

    elif chart_name == "major":
        col = "6.\u4f60\u7684\u4e13\u4e1a\u7c7b\u522b\uff1a"
        vals = df[col].apply(lambda x: "\u5176\u4ed6" if str(x).startswith("\u5176\u4ed6") else str(x))
        counts = vals.value_counts()
        ax.pie(list(counts.values), labels=list(counts.index), autopct="%1.1f%%", colors=colors[:len(counts)], startangle=90)
        ax.set_title("\u4e13\u4e1a\u7c7b\u522b\u5206\u5e03")

    elif chart_name == "intern":
        col = "31.\u4f60\u662f\u5426\u6709\u5728\u9752\u5c9b\u4f01\u4e1a\u5b9e\u4e60\u6216\u5b9e\u8df5\u7684\u7ecf\u5386?"
        counts = df[col].value_counts()
        ax.pie(list(counts.values), labels=list(counts.index), autopct="%1.1f%%", colors=colors[:len(counts)], startangle=90)
        ax.set_title("\u5b9e\u4e60\u7ecf\u5386\u5206\u5e03")

    elif chart_name == "contact":
        col = "30.\u4f60\u4e0e\u9752\u5c9b\u672c\u5730\u5c45\u6c11\uff08\u975e\u5b66\u751f\uff09\u7684\u4ea4\u5f80\u9891\u7387\u662f?"
        counts = df[col].value_counts()
        order = ["\u9891\u7e41\u4ea4\u5f80","\u7ecf\u5e38\u4ea4\u5f80","\u5076\u5c14\u4ea4\u5f80","\u4ece\u4e0d\u4ea4\u5f80"]
        ordered = {k:counts.get(k,0) for k in order if k in counts.index}
        bars = ax.barh(list(ordered.keys()), list(ordered.values()), color=colors[:len(ordered)])
        for bar, v in zip(bars, ordered.values()): ax.text(bar.get_width()+2, bar.get_y()+bar.get_height()/2, str(v), ha="left", va="center")
        ax.set_xlabel("\u4eba\u6570"); ax.set_title("\u4e0e\u672c\u5730\u5c45\u6c11\u4ea4\u5f80\u9891\u7387"); ax.invert_yaxis()

    elif chart_name == "policy":
        col = "33.\u4f60\u5bf9\u9752\u5c9b\u4e3a\u9ad8\u6821\u6bd5\u4e1a\u751f\u63d0\u4f9b\u7684\u4eba\u624d\u653f\u7b56\uff08\u5982\u4f4f\u623f\u8865\u8d34\u3001\u521b\u4e1a\u652f\u6301\u7b49\uff09\u4e86\u89e3\u591a\u5c11?"
        counts = df[col].value_counts()
        ax.pie(list(counts.values), labels=list(counts.index), autopct="%1.1f%%", colors=colors[:len(counts)], startangle=90)
        ax.set_title("\u4eba\u624d\u653f\u7b56\u4e86\u89e3\u7a0b\u5ea6")

    elif chart_name == "cross_stay_school":
        school_col = "1.\u4f60\u6240\u5728\u7684\u5b66\u6821\u662f\uff1a"
        stay_col = "29.\u6bd5\u4e1a\u540e\u4f60\u662f\u5426\u613f\u610f\u7559\u5728\u9752\u5c9b\u53d1\u5c55?"
        cross = df.groupby([school_col, stay_col]).size().unstack(fill_value=0)
        cross_pct = cross.div(cross.sum(axis=1), axis=0)*100
        order = ["\u975e\u5e38\u613f\u610f","\u6bd4\u8f83\u613f\u610f","\u4e0d\u786e\u5b9a","\u4e0d\u592a\u613f\u610f","\u975e\u5e38\u4e0d\u613f\u610f"]
        avail = [c for c in order if c in cross_pct.columns]
        cross_pct[avail].plot(kind="barh", stacked=True, ax=ax, color=colors[:len(avail)])
        ax.set_xlabel("\u6bd4\u4f8b(%)"); ax.set_title("\u5404\u6821\u5b66\u751f\u7559\u9752\u610f\u613f\u5bf9\u6bd4"); ax.legend(loc="lower right", fontsize=8)


    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    buf.seek(0); plt.close(fig)
    return Response(buf.getvalue(), mimetype="image/png")

@app.route("/api/stats")
def api_stats():
    db = get_db()
    months = [f"{m:02d}" for m in range(1,13)]
    q_data = []; i_data = []; d_data = []
    for m in months:
        q_data.append(db.query(Questionnaire).filter(func.strftime("%m",Questionnaire.created_at)==m).count())
        i_data.append(db.query(InterviewRecording).filter(func.strftime("%m",InterviewRecording.created_at)==m).count())
        d_data.append(db.query(Document).filter(func.strftime("%m",Document.created_at)==m).count())
    tags_usage = db.query(Tag.name, func.count(ItemTag.id).label("cnt")).join(ItemTag,Tag.id==ItemTag.tag_id).group_by(Tag.id).order_by(func.count(ItemTag.id).desc()).limit(10).all()
    return jsonify({"months":[f"2026-{m}" for m in months],"questionnaires":q_data,"interviews":i_data,"documents":d_data,"tag_usage":[{"name":t.name,"count":t.cnt} for t in tags_usage]})

@app.route("/")
def index():
    return redirect("/analysis")

@app.route("/questionnaires")
def questionnaires_page():
    db = get_db()
    qs = db.query(Questionnaire).order_by(Questionnaire.created_at.desc()).all()
    return render_template("questionnaires.html", tags=[t.to_dict() for t in db.query(Tag).all()], questionnaires=[q.to_dict() for q in qs])

@app.route("/api/questionnaires")
def api_questionnaires():
    db = get_db(); search = request.args.get("search","").strip()
    query = db.query(Questionnaire)
    if search: query = query.filter(Questionnaire.title.ilike("%"+search+"%"))
    tag_id = request.args.get('tag_id', type=int)
    if tag_id:
        ids = db.query(ItemTag.item_id).filter(ItemTag.item_type == 'questionnaire', ItemTag.tag_id == tag_id)
        query = query.filter(Questionnaire.id.in_(ids))
    page = max(1, request.args.get('page', 1, type=int))
    per_page = max(1, min(100, request.args.get('per_page', 20, type=int)))
    total = query.count()
    items = query.order_by(Questionnaire.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    return jsonify(items=[q.to_dict() for q in items], total=total, page=page,
                   pages=(total + per_page - 1) // per_page, per_page=per_page)


def questionnaire_values(data):
    if not isinstance(data, dict) or not isinstance(data.get('title'), str) or not data['title'].strip():
        raise ValueError('标题不能为空')
    try:
        count = int(data.get('respondent_count', 0))
        tags = list({int(value) for value in data.get('tag_ids', [])})
    except (ValueError, TypeError):
        raise ValueError('回答数或标签格式无效')
    if not 0 <= count <= 2147483647:
        raise ValueError('回答数须为 0 到 2147483647 的整数')
    if tags and get_db().query(Tag).filter(Tag.id.in_(tags)).count() != len(tags):
        raise ValueError('所选标签不存在')
    return data['title'].strip(), str(data.get('description') or ''), count, tags


@app.route('/api/questionnaires/<int:qid>', methods=['GET', 'PUT', 'DELETE'])
def api_questionnaire_item(qid):
    db = get_db()
    item = db.get(Questionnaire, qid)
    if not item:
        return jsonify(error='问卷不存在'), 404
    if request.method == 'GET':
        return jsonify(item=item.to_dict())
    if request.method == 'DELETE':
        trash_id = move_to_trash(db, item, 'questionnaires')
        return jsonify(ok=True, trash_id=trash_id)
    try:
        title, description, count, tags = questionnaire_values(request.get_json(silent=True))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    item.title, item.description, item.respondent_count = title, description, count
    db.query(ItemTag).filter_by(item_id=qid, item_type='questionnaire').delete()
    for tag_id in tags:
        db.add(ItemTag(item_id=qid, item_type='questionnaire', tag_id=tag_id))
    db.commit()
    return jsonify(item=item.to_dict())


@app.route('/api/export/<item_type>')
def api_export_table(item_type):
    from openpyxl import Workbook
    models = {'questionnaires': Questionnaire, 'documents': Document, 'interviews': InterviewRecording}
    if item_type not in models:
        return jsonify(error='不支持的导出类型'), 404
    model = models[item_type]
    query = get_db().query(model)
    if model is Document:
        query = query.filter(Document.category != 'source')
    columns = [column.name for column in model.__table__.columns if column.name not in ('file_data', 'thumbnail')]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = item_type
    sheet.append(columns)
    overflow = None
    for index, row in enumerate(query.order_by(model.id).all(), 1):
        values = []
        for name in columns:
            value = getattr(row, name)
            if isinstance(value, str) and len(value) > 30000:
                if overflow is None:
                    overflow = workbook.create_sheet('_long_text')
                    overflow.append(['ref', 'part', 'total', 'sha256', 'text'])
                reference = f'{index}:{name}'
                chunks = [value[start:start + 30000] for start in range(0, len(value), 30000)]
                checksum = hashlib.sha256(value.encode('utf-8')).hexdigest()
                for part, chunk in enumerate(chunks, 1):
                    overflow.append([reference, part, len(chunks), checksum, chunk])
                    overflow.cell(overflow.max_row, 5).data_type = 's'
                value = '@long-text:' + reference
            values.append(value)
        sheet.append(values)
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = 's'
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name=item_type + '.xlsx')

@app.route("/api/questionnaires",methods=["POST"])
def api_create_questionnaire():
    try:
        title, description, count, tags = questionnaire_values(request.get_json(silent=True))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    db = get_db()
    q = Questionnaire(title=title, description=description, respondent_count=count)
    db.add(q)
    db.flush()
    for tag_id in tags:
        db.add(ItemTag(item_id=q.id, item_type='questionnaire', tag_id=tag_id))
    db.commit()
    return jsonify({"item":q.to_dict()}),201

@app.route("/api/interviews",methods=["POST"])
def api_create_interview():
    title = request.form.get("title","").strip()
    if not title: return jsonify({"error":"标题不能为空"}),400
    category = request.form.get("category") or "student"
    if category not in ("student", "teacher"): category = "student"
    db = get_db(); iv = InterviewRecording(title=title, category=category)
    iv.interviewee = request.form.get("interviewee","")
    iv.notes = request.form.get("notes","")
    iv.interviewer = request.form.get('interviewer', '')
    iv.transcript = request.form.get('transcript', '')
    try:
        iv.duration = int(request.form.get('duration') or 0)
        iv.interview_date = date.fromisoformat(request.form['interview_date']) if request.form.get('interview_date') else None
    except ValueError:
        return jsonify(error='日期或时长格式无效'), 400
    file = request.files.get("file")
    if file and file.filename:
        file_data = file.read()
        if len(file_data) > 50*1024*1024: return jsonify({"error":"文件过大"}),400
        iv.file_data = file_data; iv.file_name = file.filename
        iv.file_type = file.content_type or "application/octet-stream"
        iv.file_size = len(file_data)
    db.add(iv)
    db.flush()
    for tag in db.query(Tag).filter(Tag.id.in_(request.form.getlist('tag_ids[]'))).all():
        db.add(ItemTag(item_type='interview', item_id=iv.id, tag_id=tag.id))
    db.commit()
    return jsonify({"item":iv.to_dict()}),201

@app.route("/analysis")
def analysis_page():
    frame = _load_survey_frame(nrows=0)
    return render_template("analysis.html", has_survey=frame is not None,
                           field_count=len(frame.columns) if frame is not None else 0)

@app.route("/api/analysis/data")
def api_analysis_data():
    df = _load_survey_frame()
    if df is None:
        return jsonify({"error": "No data file"})
    col_map = {
        "school": "1.\u4f60\u6240\u5728\u7684\u5b66\u6821\u662f\uff1a",
        "gender": "2.\u4f60\u7684\u6027\u522b:",
        "hometown": "7.\u4f60\u7684\u751f\u6e90\u5730:",
        "belonging": "28.\u4f60\u5bf9\u9752\u5c9b\u7684\u5f52\u5c5e\u611f\u5982\u4f55?",
        "stay": "29.\u6bd5\u4e1a\u540e\u4f60\u662f\u5426\u613f\u610f\u7559\u5728\u9752\u5c9b\u53d1\u5c55?",
        "contact": "30.\u4f60\u4e0e\u9752\u5c9b\u672c\u5730\u5c45\u6c11\uff08\u975e\u5b66\u751f\uff09\u7684\u4ea4\u5f80\u9891\u7387\u662f?",
        "intern": "31.\u4f60\u662f\u5426\u6709\u5728\u9752\u5c9b\u4f01\u4e1a\u5b9e\u4e60\u6216\u5b9e\u8df5\u7684\u7ecf\u5386?",
        "policy": "33.\u4f60\u5bf9\u9752\u5c9b\u4e3a\u9ad8\u6821\u6bd5\u4e1a\u751f\u63d0\u4f9b\u7684\u4eba\u624d\u653f\u7b56\uff08\u5982\u4f4f\u623f\u8865\u8d34\u3001\u521b\u4e1a\u652f\u6301\u7b49\uff09\u4e86\u89e3\u591a\u5c11?",
        "job_location": "36.\u4f60\u9996\u9009\u7684\u672a\u6765\u5c31\u4e1a\u5730\u70b9\u662f?",
        "job_path": "37.\u4f60\u9996\u9009\u7684\u5c31\u4e1a\u8def\u5f84\u662f?",
    }
    result = {}
    for key, col in col_map.items():
        if col in df.columns:
            counts = df[col].value_counts()
            result[key] = {"total":len(df),"items":[{"label":str(k),"count":int(v),"pct":round(v/len(df)*100,1)} for k,v in counts.items()]}
    school_col = col_map["school"]; stay_col = col_map["stay"]
    if school_col in df.columns and stay_col in df.columns:
        cross = df.groupby([school_col, stay_col]).size().unstack(fill_value=0)
        result["cross_stay_school"] = {"schools":list(cross.index),"categories":list(cross.columns),"data":cross.values.tolist()}
    result["total_responses"] = len(df)
    return jsonify(result)


# ─── Upload / batch import routes ─────────────────────────────────

@app.route("/api/interviews/<int:iid>", methods=["PUT"])
def api_update_interview(iid):
    db = get_db()
    item = db.query(InterviewRecording).get(iid)
    if not item:
        return jsonify({"error": "\u672a\u627e\u5230"}), 404
    data = request.get_json(silent=True) if request.is_json else request.form
    data = data or {}
    if data.get('category') in ('student', 'teacher'):
        item.category = data['category']
    if 'interview_date' in data:
        try:
            item.interview_date = date.fromisoformat(data['interview_date']) if data['interview_date'] else None
        except ValueError:
            return jsonify(error='日期格式无效'), 400
    tag_ids = data.get('tag_ids') if request.is_json else request.form.getlist('tag_ids[]')
    if tag_ids is not None:
        db.query(ItemTag).filter_by(item_type='interview', item_id=iid).delete()
        for tag in db.query(Tag).filter(Tag.id.in_(tag_ids)).all():
            db.add(ItemTag(item_type='interview', item_id=iid, tag_id=tag.id))
    if request.content_type and "application/json" in request.content_type:
        data = request.get_json() or {}
        if data.get("title"):
            item.title = data["title"]
        for field in ("interviewee", "interviewer", "notes", "transcript"):
            if field in data:
                setattr(item, field, data.get(field, ""))
        if "duration" in data:
            item.duration = data.get("duration") or 0
        if "interview_date" in data:
            try:
                item.interview_date = date.fromisoformat(data["interview_date"])
            except Exception:
                item.interview_date = None
    else:
        if request.form.get("title"):
            item.title = request.form["title"]
        for field in ("interviewee", "interviewer", "notes", "transcript"):
            if field in request.form:
                setattr(item, field, request.form.get(field, ""))
        if "duration" in request.form:
            item.duration = request.form.get("duration", 0, type=int)
        upload = request.files.get("file")
        if upload and upload.filename:
            blob = upload.read()
            if len(blob) > MAX_FILE_SIZE:
                return jsonify({"error": "\u6587\u4ef6\u8fc7\u5927"}), 400
            item.file_data = blob
            item.file_name = attachment_name(upload.filename)
            item.file_type = upload.content_type or mimetypes.guess_type(upload.filename)[0] or "application/octet-stream"
            item.file_size = len(blob)
    db.commit()
    return jsonify({"item": item.to_dict()})


@app.route("/api/interviews/<int:iid>", methods=["DELETE"])
def api_delete_interview(iid):
    db = get_db()
    item = db.query(InterviewRecording).get(iid)
    if not item:
        return jsonify({"error": "\u672a\u627e\u5230"}), 404
    trash_id = move_to_trash(db, item, 'interviews')
    return jsonify(ok=True, trash_id=trash_id)


def _store_upload(db, upload, force_kind=None, category="student"):
    if category not in ("student", "teacher"):
        category = "student"
    blob = upload.read()
    if not blob or len(blob) > MAX_FILE_SIZE:
        return None
    ctype = upload.content_type or mimetypes.guess_type(upload.filename)[0] or "application/octet-stream"
    is_media = ctype.startswith("audio/") or ctype.startswith("video/")
    title = os.path.splitext(upload.filename)[0]
    safe_name = attachment_name(upload.filename)

    if force_kind == "interviews" or (force_kind is None and is_media):
        return InterviewRecording(
            title=title, file_data=blob, file_name=safe_name,
            file_type=ctype, file_size=len(blob), category=category,
        )

    doc = Document(
        title=title, file_data=blob, file_name=safe_name,
        file_type=ctype, file_size=len(blob), category=category,
    )
    if ctype.startswith("image/"):
        try:
            from PIL import Image as PILImage
            img = PILImage.open(io.BytesIO(blob))
            doc.width, doc.height = img.size
            img.thumbnail((300, 300), PILImage.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=80)
            doc.thumbnail = buf.getvalue()
        except Exception:
            pass
    return doc


@app.route("/api/batch-import-files", methods=["POST"])
def api_batch_import_files():
    files = request.files.getlist("files[]") or request.files.getlist("files")
    if not files:
        return jsonify({"error": "\u8bf7\u9009\u62e9\u6587\u4ef6"}), 400

    force_kind = request.form.get("type") or None
    if force_kind not in ("interviews", "documents"):
        force_kind = None
    category = request.form.get("category") or "student"
    if category not in ("student", "teacher"):
        category = "student"

    db = get_db()
    saved = 0
    skipped = []
    for upload in files:
        if not upload or not upload.filename:
            continue
        try:
            obj = _store_upload(db, upload, force_kind, category)
        except Exception:
            obj = None
        if obj is None:
            skipped.append(upload.filename)
            continue
        db.add(obj)
        saved += 1
    db.commit()
    return jsonify({"ok": True, "count": saved, "skipped": skipped})


@app.route("/api/import/<item_type>", methods=["POST"])
def api_import_table(item_type):
    if item_type not in ('questionnaires', 'interviews', 'documents'):
        return jsonify(error='类型无效'), 400
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "\u8bf7\u9009\u62e9\u6587\u4ef6"}), 400
    if not upload.filename.lower().endswith('.xlsx'):
        return jsonify(error='请选择 .xlsx 文件；旧版 .xls 请先另存为 .xlsx。'), 400
    raw = upload.stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        return jsonify(error='Excel 文件不能超过 10 MB。'), 413
    try:
        import pandas as pd
    except Exception:
        return jsonify({"error": "pandas unavailable"}), 500

    db = get_db()
    try:
        from local_tools import office_archive
        with office_archive(raw):
            pass
        frame = pd.read_excel(io.BytesIO(raw), keep_default_na=False).dropna(how='all').fillna('')
        frame.columns = [str(column).strip() for column in frame.columns]
        # Excel cells hold at most 32767 characters; exported long fields live in chunks.
        references = {value for column in frame for value in frame[column]
                      if isinstance(value, str) and value.startswith('@long-text:')}
        if references:
            chunks = pd.read_excel(io.BytesIO(raw), sheet_name='_long_text', dtype=str, keep_default_na=False).fillna('')
            if not {'ref', 'part', 'total', 'sha256', 'text'} <= set(chunks.columns):
                raise ValueError('长文本附表不完整，请使用完整的导出工作簿。')
            restored = {}
            for reference in references:
                records = chunks[chunks['ref'] == reference[len('@long-text:'):]].copy()
                parts = pd.to_numeric(records['part'], errors='raise')
                total = pd.to_numeric(records['total'], errors='raise')
                if records.empty or len(set(parts)) != len(parts) or set(parts) != set(range(1, len(parts) + 1)) or not (total == len(parts)).all():
                    raise ValueError('长文本分段缺失或重复，未导入任何记录。')
                records = records.assign(part=parts).sort_values('part')
                value = ''.join(records['text'])
                checksum = hashlib.sha256(value.encode('utf-8')).hexdigest()
                if not (records['sha256'] == checksum).all():
                    raise ValueError('长文本校验失败，未导入任何记录。')
                restored[reference] = value
            for column in frame:
                frame[column] = frame[column].map(lambda value: restored.get(value, value) if isinstance(value, str) else value)
    except Exception as exc:
        return jsonify({"error": "\u5bfc\u5165\u5931\u8d25: %s" % exc}), 400

    if item_type == 'questionnaires':
        mode = request.form.get('mode', 'auto')
        if mode not in {'auto', 'responses', 'catalog'}:
            return jsonify(error='导入类型无效。'), 400
        is_catalog = mode == 'catalog' or (mode == 'auto' and ('标题' in frame or 'title' in frame))
        if not is_catalog:
            try:
                sheets = read_workbook(raw)
            except ValueError as error:
                return jsonify(error=str(error)), 400
            items = []
            filename = attachment_name(upload.filename)
            stem = os.path.splitext(filename)[0]
            for sheet, responses in sheets:
                content = {'survey_table': json.loads(responses.to_json(orient='split', force_ascii=False, index=False)),
                           'source_name': filename, 'sheet': sheet}
                title = (stem if len(sheets) == 1 else f'{stem} / {sheet}')[:200]
                item = Questionnaire(title=title, respondent_count=len(responses),
                                     description=f'导入文件：{filename}；工作表：{sheet}',
                                     content=json.dumps(content, ensure_ascii=False))
                db.add(item)
                items.append(item)
            db.commit()
            return jsonify(ok=True, count=len(items), response_count=sum(row.respondent_count for row in items),
                           ids=[row.id for row in items], mode='responses')
        if '标题' not in frame and 'title' not in frame:
            return jsonify(error='问卷列表需包含“标题”列；答卷结果表请选择“答卷数据表”。未导入任何记录。'), 400
        column = '回答数' if '回答数' in frame else 'respondent_count'
        if column in frame:
            counts = pd.to_numeric(frame[column].replace('', 0), errors='coerce')
            if counts.isna().any() or (counts < 0).any() or (counts > 2147483647).any() or ((counts % 1) != 0).any():
                return jsonify(error='回答数须为 0 到 2147483647 的整数；未导入任何记录'), 400
            frame[column] = counts
        # Validate the complete batch before adding any records.
        if 'content' in frame:
            for value in frame['content']:
                try:
                    questionnaire_frame(Questionnaire(content=str(value or '{}')))
                except ValueError:
                    return jsonify(error='问卷列表中的 content 数据无效；未导入任何记录。'), 400
    default_category = request.form.get('category', 'student')
    if default_category not in ('student', 'teacher'):
        return jsonify(error='所属分类无效。'), 400
    if item_type in ('documents', 'interviews'):
        categories = frame.get('分类', frame.get('category', pd.Series(default_category, index=frame.index)))
        categories = categories.map(lambda value: {'学生访谈': 'student', '教师访谈': 'teacher'}.get(str(value).strip(), str(value).strip()) or default_category)
        if not categories.isin(['student', 'teacher']).all():
            return jsonify(error='分类必须为 student / teacher 或学生访谈 / 教师访谈；未导入任何记录。'), 400
        frame['category'] = categories
    if item_type == 'interviews':
        durations = pd.to_numeric(frame.get('时长', frame.get('duration', pd.Series(0, index=frame.index))).replace('', 0), errors='coerce')
        if durations.isna().any() or (durations < 0).any() or (durations > 2147483647).any() or ((durations % 1) != 0).any():
            return jsonify(error='时长必须为非负整数；未导入任何记录。'), 400
        frame['duration'] = durations
        dates = frame.get('访谈日期', frame.get('interview_date', pd.Series('', index=frame.index)))
        try:
            frame['interview_date'] = dates.map(lambda value: date.fromisoformat(str(value)[:10]) if str(value).strip() else None)
        except ValueError:
            return jsonify(error='访谈日期无效；未导入任何记录。'), 400
    count = 0
    for _, row in frame.iterrows():
        if item_type == "questionnaires":
            title = str(row.get("\u6807\u9898", row.get("title", "")) or "").strip()
            if not title:
                continue
            db.add(Questionnaire(
                title=title,
                description=str(row.get("\u63cf\u8ff0", row.get("description", "")) or ""),
                respondent_count=int(row.get("\u56de\u7b54\u6570", row.get("respondent_count", 0)) or 0),
                content=str(row.get('content', '{}') or '{}'),
            ))
        elif item_type == "interviews":
            title = str(row.get("\u6807\u9898", row.get("title", "")) or "").strip()
            if not title:
                continue
            db.add(InterviewRecording(
                title=title,
                interviewee=str(row.get("\u53d7\u8bbf\u8005", row.get("interviewee", "")) or ""),
                interviewer=str(row.get("\u91c7\u8bbf\u8005", row.get("interviewer", "")) or ""),
                notes=str(row.get("\u5907\u6ce8", row.get("notes", "")) or ""),
                transcript=str(row.get('转录文本', row.get('transcript', '')) or ''),
                category=row['category'], duration=int(row['duration']), interview_date=row['interview_date'],
            ))
        elif item_type == "documents":
            title = str(row.get("\u6807\u9898", row.get("title", "")) or "").strip()
            if not title:
                continue
            db.add(Document(
                title=title,
                description=str(row.get("\u63cf\u8ff0", row.get("description", "")) or ""),
                category=row['category'],
            ))
        else:
            return jsonify({"error": "\u7c7b\u578b\u65e0\u6548"}), 400
        count += 1
    if count == 0:
        return jsonify(error='没有可导入的记录，请检查表头及标题是否为空。'), 400
    db.commit()
    return jsonify({"ok": True, "count": count})


@app.get('/api/questionnaires/<int:qid>/download')
def api_download_questionnaire(qid):
    item = get_db().get(Questionnaire, qid)
    if item is None:
        return jsonify(error='问卷不存在'), 404
    frame = questionnaire_frame(item)
    if frame is None:
        return jsonify(error='该问卷没有导入答卷数据表'), 404
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '答卷数据'
    sheet.append(list(frame.columns))
    for row in frame.itertuples(index=False, name=None):
        sheet.append(list(row))
    for row in sheet:
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = 's'
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=attachment_name(item.title) + '.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.route("/api/questionnaires/<int:qid>/preview")
def api_questionnaire_preview(qid):
    """Render the survey sheet as an HTML table preview."""
    db = get_db()
    q = db.query(Questionnaire).get(qid)
    if not q:
        return '<div>\u672a\u627e\u5230</div>'

    try:
        frame = _questionnaire_frame(q)
    except Exception as exc:
        return '<div>\u8bfb\u53d6\u5931\u8d25: %s</div>' % h.escape(str(exc))
    if frame is None:
        return '<div>\u6570\u636e\u6587\u4ef6\u4e0d\u5b58\u5728</div>' 

    cols = list(frame.columns)
    rows = min(30, len(frame))
    back = '<div style="margin-bottom:12px"><button class="btn btn-sm btn-outline" onclick="loadQuestionnaireDetail(' + str(qid) + ')">\u2190 \u8fd4\u56de\u6982\u8981</button></div>'
    out = [back, '<div class="detail-section"><div class="detail-label">\u6570\u636e\u9884\u89c8\uff08\u524d %d \u6761 / \u5171 %d \u6761\uff0c%d \u4e2a\u5b57\u6bb5\uff09</div>' % (rows, len(frame), len(cols))]
    out.append('<div style="overflow:auto;max-height:420px;border:1px solid var(--border);border-radius:8px">')
    out.append('<table class="data-table" style="font-size:12px;white-space:nowrap">')
    out.append('<thead><tr>')
    for c in cols:
        out.append('<th>%s</th>' % h.escape(str(c)))
    out.append('</tr></thead><tbody>')
    for _, row in frame.head(rows).iterrows():
        out.append('<tr>')
        for c in cols:
            out.append('<td>%s</td>' % h.escape(str(row[c])))
        out.append('</tr>')
    out.append('</tbody></table></div></div>')
    return "".join(out)


@app.route("/api/questionnaires/<int:qid>/columns")
def api_questionnaire_columns(qid):
    """List the survey questions (columns of the sheet)."""
    q = get_db().get(Questionnaire, qid)
    if q is None:
        return '问卷不存在', 404
    try:
        frame = _questionnaire_frame(q, nrows=0)
    except Exception as exc:
        return '<div>\u8bfb\u53d6\u5931\u8d25: %s</div>' % h.escape(str(exc))
    if frame is None:
        return '<div>\u6570\u636e\u6587\u4ef6\u4e0d\u5b58\u5728</div>' 

    back = '<div style="margin-bottom:12px"><button class="btn btn-sm btn-outline" onclick="loadQuestionnaireDetail(' + str(qid) + ')">\u2190 \u8fd4\u56de\u6982\u8981</button></div>'
    out = [back, '<div class="detail-section"><div class="detail-label">\u95ee\u5377\u9898\u76ee\uff08\u5171 %d \u9879\uff09</div><div class="detail-value">' % len(frame.columns)]
    out.append('<ol style="padding-left:22px;line-height:2">')
    for c in frame.columns:
        out.append('<li>%s</li>' % h.escape(str(c)))
    out.append('</ol></div></div>')
    return "".join(out)


# ─── Authentication ────────────────────────────────────────────────

PUBLIC_ENDPOINTS = {"login", "static"}


@app.before_request
def _require_login():
    """Gate every page and API behind a session, except the login screen."""
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return None
    if session.get("uid"):
        if get_db().get(User, session['uid']):
            return None
        session.clear()
    if request.path.startswith("/api/"):
        return jsonify({"error": "\u672a\u767b\u5f55", "login_required": True}), 401
    return redirect(url_for("login", next=request.path))


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return get_db().query(User).get(uid)


@app.context_processor
def _inject_user():
    return {"current_username": session.get("uname", ""), "current_is_admin": session.get("admin", False)}


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    nxt = request.args.get("next", "") or request.form.get("next", "")
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        db = get_db()
        user = db.query(User).filter(User.username == username).first()
        if user and user.check_password(password):
            session.clear()
            session["uid"] = user.id
            session["uname"] = user.username
            session["admin"] = bool(user.is_admin)
            session.permanent = True
            if nxt.startswith("/") and not nxt.startswith("//"):
                return redirect(nxt)
            return redirect("/analysis")
        error = "\u7528\u6237\u540d\u6216\u5bc6\u7801\u9519\u8bef"
    return render_template("login.html", error=error, next=nxt)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/account", methods=["GET", "POST"])
def account():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    message = None
    error = None
    if request.method == "POST":
        old = request.form.get("old_password") or ""
        new = request.form.get("new_password") or ""
        again = request.form.get("confirm_password") or ""
        if not user.check_password(old):
            error = "\u539f\u5bc6\u7801\u4e0d\u6b63\u786e"
        elif len(new) < 6:
            error = "\u65b0\u5bc6\u7801\u81f3\u5c11 6 \u4f4d"
        elif new != again:
            error = "\u4e24\u6b21\u8f93\u5165\u7684\u65b0\u5bc6\u7801\u4e0d\u4e00\u81f4"
        else:
            user.set_password(new)
            get_db().commit()
            message = "\u5bc6\u7801\u5df2\u4fee\u6539"
    return render_template("account.html", user=user, message=message, error=error,
                           users=get_db().query(User).order_by(User.id).all() if user.is_admin else [])


@app.route("/api/users", methods=["POST"])
def api_create_user():
    me = current_user()
    if not me or not me.is_admin:
        return jsonify({"error": "\u9700\u8981\u7ba1\u7406\u5458\u6743\u9650"}), 403
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username:
        return jsonify({"error": "\u7528\u6237\u540d\u4e0d\u80fd\u4e3a\u7a7a"}), 400
    if len(password) < 6:
        return jsonify({"error": "\u5bc6\u7801\u81f3\u5c11 6 \u4f4d"}), 400
    db = get_db()
    if db.query(User).filter(User.username == username).first():
        return jsonify({"error": "\u7528\u6237\u540d\u5df2\u5b58\u5728"}), 409
    user = User(username=username, is_admin=1 if data.get("is_admin") else 0)
    user.set_password(password)
    db.add(user)
    db.commit()
    return jsonify({"item": user.to_dict()}), 201


@app.route("/api/users/<int:uid>", methods=["DELETE"])
def api_delete_user(uid):
    me = current_user()
    if not me or not me.is_admin:
        return jsonify({"error": "\u9700\u8981\u7ba1\u7406\u5458\u6743\u9650"}), 403
    if uid == me.id:
        return jsonify({"error": "\u4e0d\u80fd\u5220\u9664\u81ea\u5df1"}), 400
    db = get_db()
    user = db.query(User).get(uid)
    if not user:
        return jsonify({"error": "\u672a\u627e\u5230"}), 404
    if db.query(User).count() <= 1:
        return jsonify({"error": "\u81f3\u5c11\u4fdd\u7559\u4e00\u4e2a\u8d26\u53f7"}), 400
    db.delete(user)
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/users/<int:uid>/password", methods=["PUT"])
def api_reset_user_password(uid):
    me = current_user()
    if not me or not me.is_admin:
        return jsonify({"error": "\u9700\u8981\u7ba1\u7406\u5458\u6743\u9650"}), 403
    data = request.get_json() or {}
    password = data.get("password") or ""
    if len(password) < 6:
        return jsonify({"error": "\u5bc6\u7801\u81f3\u5c11 6 \u4f4d"}), 400
    db = get_db()
    user = db.query(User).get(uid)
    if not user:
        return jsonify({"error": "\u672a\u627e\u5230"}), 404
    user.set_password(password)
    db.commit()
    return jsonify({"ok": True})
