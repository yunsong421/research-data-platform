import importlib
import base64
import io
import os
from pathlib import Path
import secrets
import tempfile
import unittest
import pandas as pd
from PIL import Image, ImageStat
from unittest.mock import patch


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {'DATA_DIR': cls.temp.name})
        cls.env.start()
        cls.module = importlib.import_module('app')
        cls.models = importlib.import_module('models')
        cls.module.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cls.models.db_session.remove()
        cls.models.engine.dispose()
        cls.env.stop()
        cls.temp.cleanup()

    def test_empty_install_and_login(self):
        models = self.models
        db = models.get_db()
        for model in (models.Questionnaire, models.Document, models.InterviewRecording, models.User):
            self.assertEqual(db.query(model).count(), 0)
        self.assertEqual(Path(models.engine.url.database).parent, Path(self.temp.name))
        client = self.module.app.test_client()
        self.assertEqual(client.get('/api/documents').status_code, 401)
        password = secrets.token_urlsafe(20)
        admin = importlib.import_module('init_admin')
        with patch('builtins.input', return_value='test-admin'), patch.object(admin, 'getpass', return_value=password):
            admin.main()
        self.assertEqual(client.post('/login', data={'username': 'test-admin', 'password': password}).status_code, 302)
        for path in ('/questionnaires', '/documents', '/interviews', '/analysis', '/visualizations', '/account', '/search'):
            self.assertEqual(client.get(path).status_code, 200, path)
        self.assertNotIn('/api/analysis/chart/', client.get('/analysis').get_data(as_text=True))
        self.assertIsNone(self.module._load_survey_frame())
        for path in ('/api/questionnaires', '/api/documents', '/api/interviews'):
            self.assertEqual(client.get(path).get_json()['total'], 0)
        from config import session_secret
        self.assertEqual(session_secret(), session_secret())
        self.assertGreaterEqual(len(session_secret()), 32)
        with self.assertRaises(SystemExit):
            admin.main()

    def test_visualization_workflow(self):
        client = self.module.app.test_client()
        self.assertEqual(client.get('/api/visualizations/datasets').status_code, 401)
        self.assertEqual(client.post('/api/visualizations/chart', json={}).status_code, 401)
        with client.session_transaction() as session:
            session['uid'] = 1
        frame = pd.DataFrame({'群体': ['甲', '甲', '乙', '乙', None],
                              '得分': [2, 4, 6, None, 10], '年份': [2024, 2023, 2025, 2024, 2025],
                              '日期': ['2024-01-01', '2023-01-01', '2025-01-01', '2024-01-01', '2025-01-01']})
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            frame.to_excel(writer, index=False, sheet_name='示例')
            frame.head(2).to_excel(writer, index=False, sheet_name='第二张表')
        raw = output.getvalue()
        response = client.post('/api/visualizations/datasets', data={'file': (io.BytesIO(raw), 'demo.xlsx')})
        self.assertEqual(response.status_code, 201, response.get_json())
        keys = response.get_json()['ids']
        self.assertEqual(len(keys), 2)
        key = keys[0]
        self.models.db_session.remove()
        detail = client.get('/api/visualizations/datasets/' + key).get_json()
        self.assertEqual(detail['rows'], 5)
        self.assertEqual(detail['suggestion']['x'], '群体')
        self.assertEqual([field['type'] for field in detail['fields']], ['category', 'number', 'number', 'date'])
        base = dict(dataset=key, x='群体', type='bar', aggregation='count', top=12)
        for kind, x, y in [('bar', '群体', ''), ('barh', '群体', ''), ('pie', '群体', ''),
                           ('line', '日期', ''), ('histogram', '得分', ''), ('scatter', '年份', '得分')]:
            response = client.post('/api/visualizations/chart', json={**base, 'type': kind, 'x': x, 'y': y})
            self.assertEqual(response.status_code, 200, response.get_json())
            data = response.get_json()
            image = Image.open(io.BytesIO(base64.b64decode(data['image'].split(',')[1])))
            self.assertEqual(image.format, 'PNG')
            self.assertGreater(image.width, 500)
            self.assertGreater(sum(ImageStat.Stat(image.convert('RGB')).var), 100)
            if kind == 'bar':
                self.assertEqual(data['statistics']['y'], [2, 2])
                self.assertEqual(data['statistics']['excluded'], 1)
            if kind == 'line':
                self.assertEqual(data['statistics']['x'], ['2023-01-01', '2024-01-01', '2025-01-01'])
            if kind == 'scatter':
                self.assertEqual(data['statistics']['used'], 4)
        for aggregation, expected in [('sum', [6, 6]), ('mean', [6, 3])]:
            result = client.post('/api/visualizations/chart', json={**base, 'aggregation': aggregation, 'y': '得分'}).get_json()
            self.assertEqual(result['statistics']['y'], expected)
            self.assertEqual(result['statistics']['excluded'], 2)
        result = client.post('/api/visualizations/chart', json={**base, 'top': 1}).get_json()
        self.assertEqual(result['statistics']['omitted_categories'], 1)
        for changes in ({'type': 'pie', 'top': 1}, {'type': 'histogram', 'x': '群体'},
                        {'type': 'line'}, {'type': 'unknown'}, {'aggregation': 'sum', 'y': 'missing'},
                        {'x': []}, {'type': []}, {'dataset': 'saved-99999'}, {'bins': 100}):
            self.assertEqual(client.post('/api/visualizations/chart', json={**base, **changes}).status_code, 400, changes)
        self.assertEqual(client.post('/api/visualizations/datasets', data={'file': (io.BytesIO(b'bad'), 'bad.xlsx')}).status_code, 400)
        document = self.models.Document(title='Synthetic existing workbook', file_name='demo.xlsx',
                                        file_data=raw, category='source')
        db = self.models.get_db()
        db.add(document)
        db.commit()
        document_id = document.id
        listed = client.get('/api/visualizations/datasets').get_json()['datasets']
        source = next(item for item in listed if item['id'] == f'document-{document_id}-0')
        self.assertFalse(source['removable'])
        self.assertEqual(client.get('/api/visualizations/datasets/' + source['id']).get_json()['rows'], 5)
        for key in keys:
            self.assertEqual(client.delete('/api/visualizations/datasets/' + key[6:]).status_code, 200)
            self.assertEqual(client.get('/api/visualizations/datasets/' + key).status_code, 400)
        self.assertIsNotNone(db.get(self.models.Document, document_id))
        db.delete(db.get(self.models.Document, document_id))
        db.commit()
        self.assertEqual(db.query(self.models.ChartDataset).count(), 0)

    def test_management_crud_and_exports(self):
        from openpyxl import load_workbook
        client = self.module.app.test_client()
        self.assertEqual(client.get('/api/export/documents').status_code, 401)
        with client.session_transaction() as session:
            session['uid'] = 1
        created = client.post('/api/questionnaires', json={'title': 'Synthetic survey', 'respondent_count': 5})
        self.assertEqual(created.status_code, 201)
        qid = created.get_json()['item']['id']
        result = client.put(f'/api/questionnaires/{qid}', json={'title': '=1+1', 'description': 'Synthetic update', 'respondent_count': 8})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(client.get(f'/api/questionnaires/{qid}').get_json()['item']['respondent_count'], 8)
        self.assertEqual(client.get('/api/questionnaires?per_page=1').get_json()['pages'], 1)
        self.assertEqual(client.put(f'/api/questionnaires/{qid}', json={'title': 'invalid', 'respondent_count': -1}).status_code, 400)
        for kind in ('documents', 'interviews'):
            created = client.post('/api/' + kind, data={'title': 'Synthetic attachment', 'category': 'teacher',
                                   'file': (io.BytesIO(b'synthetic file content'), 'fixture.txt')})
            self.assertEqual(created.status_code, 201)
            identifier = created.get_json()['item']['id']
            self.assertEqual(created.get_json()['item']['category'], 'teacher')
            self.assertEqual(client.get(f'/api/{kind}/{identifier}/download').data, b'synthetic file content')
            self.assertEqual(client.get(f'/api/{kind}/{identifier}/view').status_code, 200)
            if kind == 'documents':
                updated = client.put(f'/api/{kind}/{identifier}', json={'title': 'Renamed'})
            else:
                updated = client.put(f'/api/{kind}/{identifier}', data={'title': 'Renamed', 'category': 'teacher'})
            self.assertEqual(updated.status_code, 200)
            exported = client.get('/api/export/' + kind)
            self.assertEqual(exported.status_code, 200)
            workbook = load_workbook(io.BytesIO(exported.data))
            self.assertEqual(workbook.active.max_row, 2)
            self.assertNotIn('file_data', [cell.value for cell in workbook.active[1]])
            workbook.close()
            self.assertEqual(client.delete(f'/api/{kind}/{identifier}').status_code, 200)
        exported = client.get('/api/export/questionnaires')
        self.assertEqual(exported.status_code, 200)
        workbook = load_workbook(io.BytesIO(exported.data))
        headers = [cell.value for cell in workbook.active[1]]
        title = workbook.active.cell(2, headers.index('title') + 1)
        self.assertEqual(title.value, '=1+1')
        self.assertEqual(title.data_type, 's')
        workbook.close()
        self.assertEqual(client.delete(f'/api/questionnaires/{qid}').status_code, 200)
        self.assertEqual(client.get(f'/api/questionnaires/{qid}').status_code, 404)

    def test_visualization_validation(self):
        from visualizations import prepare_chart, read_workbook
        for values in ([0, 0], [-2, 4]):
            with self.assertRaises(ValueError):
                prepare_chart(pd.DataFrame({'a': ['x', 'y'], 'b': values}),
                              {'x': 'a', 'y': 'b', 'type': 'pie', 'aggregation': 'sum'})
        with self.assertRaises(ValueError):
            prepare_chart(pd.DataFrame({'a': [None]}), {'x': 'a'})
        result = prepare_chart(pd.DataFrame({'a': [10, 2, 1], 'b': [4, 5, 6]}),
                               {'x': 'a', 'type': 'line'})
        self.assertEqual(result['x'], ['1', '2', '10'])
        output = io.BytesIO()
        pd.DataFrame(columns=['empty']).to_excel(output, index=False)
        with self.assertRaises(ValueError):
            read_workbook(output.getvalue())

    def test_chart_axis_and_small_share_labels(self):
        from visualizations import render_chart, prepare_chart
        from matplotlib.figure import Figure
        figures = []
        def capture(*args, **kwargs):
            figure = Figure(*args, **kwargs)
            figures.append(figure)
            return figure
        with patch('visualizations.Figure', side_effect=capture):
            data = prepare_chart(pd.DataFrame({'x': [1, 2, 100], 'y': [1, 2, 100]}),
                                 {'type': 'line', 'x': 'x', 'y': 'y', 'aggregation': 'mean'})
            render_chart(data, 'Irregular numeric axis')
            self.assertEqual(list(figures[-1].axes[0].lines[0].get_xdata()), [1, 2, 100])
            data = prepare_chart(pd.DataFrame({'x': ['large', 'small'], 'y': [999, 1]}),
                                 {'type': 'pie', 'x': 'x', 'y': 'y', 'aggregation': 'sum'})
            render_chart(data, 'Small shares')
            axes = figures[-1].axes[0]
            self.assertNotIn('0.1%', [text.get_text() for text in axes.texts])
            self.assertTrue(any('0.10%' in text.get_text() for text in axes.get_legend().get_texts()))

    def test_regression_search_import_and_attachments(self):
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session['uid'] = 1
        models = self.models
        db = models.get_db()
        tag = models.Tag(name='Regression tag')
        db.add(tag)
        db.flush()
        tag_id = tag.id
        for model in (models.Questionnaire, models.Document, models.InterviewRecording):
            db.add(model(id=90001, title='sharedregression'))
        db.commit()
        found = client.get('/api/search?q=sharedregression').get_json()['items']
        self.assertEqual(len(found), 3)
        for kind in ('documents', 'interviews'):
            self.assertEqual(client.get(f'/api/{kind}/90001').status_code, 200)
            self.assertEqual(client.get(f'/api/{kind}?per_page=0&page=-1').status_code, 200)
        payload = {'title': '访谈"测试', 'category': 'teacher', 'interviewer': 'Synthetic interviewer',
                   'interview_date': '2026-11-15', 'duration': '120', 'tag_ids[]': str(tag_id)}
        result = client.post('/api/interviews', data=payload).get_json()['item']
        iid = result['id']
        self.assertEqual(result['interviewer'], payload['interviewer'])
        self.assertEqual(result['interview_date'], '2026-11-15')
        self.assertEqual(result['duration'], 120)
        self.assertEqual(result['tags'][0]['id'], tag_id)
        result = client.put(f'/api/interviews/{iid}', data={'interview_date': '2026-11-16', 'category': 'student'}).get_json()['item']
        self.assertEqual(result['interview_date'], '2026-11-16')
        self.assertEqual(result['category'], 'student')
        self.assertEqual(result['tags'], [])
        png = io.BytesIO()
        Image.new('RGB', (20, 20), 'red').save(png, format='PNG')
        result = client.post('/api/documents', data={'title': '<img src=x onerror=alert(1)>',
                             'file': (io.BytesIO(png.getvalue()), '中文图片.png', 'image/png')}).get_json()['item']
        did = result['id']
        self.assertEqual(result['file_name'], '中文图片.png')
        detail = client.get(f'/documents/{did}/preview').get_data(as_text=True)
        self.assertNotIn('<img src=x', detail)
        self.assertIn(f'/api/documents/{did}/view', detail)
        for count, expected in [(None, 200), ('invalid', 400)]:
            stream = io.BytesIO()
            pd.DataFrame({'标题': ['Import regression', None], '回答数': [count, 2]}).to_excel(stream, index=False)
            response = client.post('/api/import/questionnaires', data={'file': (io.BytesIO(stream.getvalue()), 'import.xlsx')})
            self.assertEqual(response.status_code, expected)
            if expected == 200:
                self.assertEqual(response.get_json()['count'], 1)
        db = models.get_db()
        for model in (models.Questionnaire, models.Document, models.InterviewRecording):
            for row in db.query(model).all():
                db.delete(row)
        db.query(models.ItemTag).delete()
        db.delete(db.get(models.Tag, tag_id))
        db.commit()
        with client.session_transaction() as session:
            session['uid'] = 999999
        self.assertEqual(client.get('/api/documents').status_code, 401)

    def test_s_questionnaire_response_import(self):
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session['uid'] = 1
        frame = pd.DataFrame({'群体': ['学生', '教师', None], '得分': [3, 5, None],
                              '备注': ['<script>alert(1)</script>', '=1+1', None]})
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            frame.to_excel(writer, index=False, sheet_name='答卷')
            pd.DataFrame({'另一题': ['仅第二份']}).to_excel(writer, index=False, sheet_name='第二份')
        raw = output.getvalue()
        imported = client.post('/api/import/questionnaires', data={'mode': 'responses',
                               'file': (io.BytesIO(raw), '中文问卷.xlsx')})
        self.assertEqual(imported.status_code, 200, imported.get_json())
        result = imported.get_json()
        self.assertEqual((result['count'], result['response_count']), (2, 3))
        qid, second = result['ids']
        preview = client.get(f'/api/questionnaires/{qid}/preview').get_data(as_text=True)
        self.assertIn('学生', preview)
        self.assertNotIn('<script>alert', preview)
        self.assertIn('&lt;script&gt;', preview)
        self.assertNotIn('仅第二份', preview)
        self.assertIn('仅第二份', client.get(f'/api/questionnaires/{second}/preview').get_data(as_text=True))
        self.assertIn('群体', client.get(f'/api/questionnaires/{qid}/columns').get_data(as_text=True))
        self.assertEqual(client.get('/api/questionnaires/999999/columns').status_code, 404)
        self.assertIn(f'/api/questionnaires/{qid}/download', client.get(f'/api/detail/questionnaires/{qid}').get_data(as_text=True))
        download = client.get(f'/api/questionnaires/{qid}/download')
        self.assertEqual(download.status_code, 200)
        self.assertEqual(pd.read_excel(io.BytesIO(download.data))['群体'].tolist(), ['学生', '教师'])
        keys = [item['id'] for item in client.get('/api/visualizations/datasets').get_json()['datasets']]
        self.assertIn(f'questionnaire-{qid}', keys)
        detail = client.get(f'/api/visualizations/datasets/questionnaire-{qid}').get_json()
        self.assertEqual(detail['rows'], 2)
        chart = client.post('/api/visualizations/chart', json={'dataset': f'questionnaire-{qid}',
                            'type': 'bar', 'x': '群体', 'aggregation': 'count', 'top': 12})
        self.assertEqual(chart.status_code, 200, chart.get_json())
        before = client.get('/api/questionnaires').get_json()['total']
        invalid = client.post('/api/import/questionnaires', data={'mode': 'catalog',
                              'file': (io.BytesIO(raw), 'bad.xlsx')})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(client.get('/api/questionnaires').get_json()['total'], before)
        for payload, filename in [(b'not excel', 'bad.xlsx'), (b'', 'bad.xls')]:
            self.assertEqual(client.post('/api/import/questionnaires', data={
                'file': (io.BytesIO(payload), filename)}).status_code, 400)
        empty = io.BytesIO()
        pd.DataFrame(columns=['题目']).to_excel(empty, index=False)
        self.assertEqual(client.post('/api/import/questionnaires', data={'mode': 'responses',
                         'file': (io.BytesIO(empty.getvalue()), 'empty.xlsx')}).status_code, 400)
        exported = client.get('/api/export/questionnaires').data
        again = client.post('/api/import/questionnaires', data={'mode': 'catalog',
                            'file': (io.BytesIO(exported), 'catalog.xlsx')})
        self.assertEqual(again.status_code, 200)
        db = self.models.get_db()
        copies = db.query(self.models.Questionnaire).all()
        self.assertEqual(len(copies), 4)
        for item in copies:
            self.assertIsNotNone(self.module.questionnaire_frame(item))
        deleted = client.delete(f'/api/questionnaires/{qid}').get_json()['trash_id']
        self.assertNotIn(f'questionnaire-{qid}', [item['id'] for item in client.get('/api/visualizations/datasets').get_json()['datasets']])
        self.assertEqual(client.post(f'/api/local/trash/{deleted}/restore').status_code, 200)
        self.assertIn('学生', client.get(f'/api/questionnaires/{qid}/preview').get_data(as_text=True))
        db = self.models.get_db()
        db.query(self.models.Questionnaire).delete()
        db.commit()

    def test_t_long_export_and_category_roundtrip(self):
        import json
        from datetime import date
        from openpyxl import load_workbook
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session['uid'] = 1
        db = self.models.get_db()
        content = json.dumps({'survey_table': {'columns': ['答案'], 'data': [['中文' * 40] for _ in range(778)]}}, ensure_ascii=False)
        db.add(self.models.Questionnaire(title='Long roundtrip', content=content, respondent_count=778))
        transcript = '教师访谈正文\n' * 6000
        db.add(self.models.InterviewRecording(title='Teacher roundtrip', category='teacher',
               transcript=transcript, duration=120, interview_date=date(2026, 11, 16)))
        db.add(self.models.Document(title='Teacher document', category='teacher'))
        db.commit()
        exports = {kind: client.get('/api/export/' + kind).data for kind in ('questionnaires', 'interviews', 'documents')}
        for kind, raw in exports.items():
            response = client.post('/api/import/' + kind, data={'mode': 'catalog', 'file': (io.BytesIO(raw), kind + '.xlsx')})
            self.assertEqual(response.status_code, 200, response.get_json())
        db = self.models.get_db()
        copies = db.query(self.models.Questionnaire).filter_by(title='Long roundtrip').all()
        self.assertEqual(len(copies), 2)
        self.assertTrue(all(row.content == content for row in copies))
        self.assertTrue(all(len(self.module.questionnaire_frame(row)) == 778 for row in copies))
        interviews = db.query(self.models.InterviewRecording).filter_by(title='Teacher roundtrip').all()
        self.assertEqual(len(interviews), 2)
        for row in interviews:
            self.assertEqual((row.category, row.transcript, row.duration, row.interview_date), ('teacher', transcript, 120, date(2026, 11, 16)))
        self.assertEqual(db.query(self.models.Document).filter_by(title='Teacher document', category='teacher').count(), 2)
        workbook = load_workbook(io.BytesIO(exports['questionnaires']))
        workbook['_long_text'].delete_rows(2)
        output = io.BytesIO()
        workbook.save(output)
        workbook.close()
        before = db.query(self.models.Questionnaire).count()
        response = client.post('/api/import/questionnaires', data={'mode': 'catalog', 'file': (io.BytesIO(output.getvalue()), 'broken.xlsx')})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.models.get_db().query(self.models.Questionnaire).count(), before)
        for model in (self.models.Questionnaire, self.models.InterviewRecording, self.models.Document):
            self.models.get_db().query(model).delete()
        self.models.get_db().commit()
        output = io.BytesIO()
        pd.DataFrame({'title': ['Uses selected category']}).to_excel(output, index=False)
        result = client.post('/api/import/interviews', data={'category': 'teacher', 'file': (io.BytesIO(output.getvalue()), 'teacher.xlsx')})
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(client.get('/api/interviews').get_json()['items'][0]['category'], 'teacher')
        self.models.get_db().query(self.models.InterviewRecording).delete()
        self.models.get_db().commit()

    def test_u_text_boundaries_and_invalid_survey(self):
        import json
        from local_tools import document_content
        for encoding in ('utf-8', 'gb18030'):
            raw = ('a' * (1024 * 1024 - 1) + '中文').encode(encoding)
            preview = document_content(self.models.Document(file_name='boundary.txt', file_data=raw))
            self.assertEqual(preview['kind'], 'text')
            self.assertEqual(preview['text'], 'a' * (1024 * 1024 - 1))
        for table in ({'columns': [], 'data': []}, {'columns': ['a'], 'data': [[{}]]},
                      {'columns': ['a', 'a'], 'data': [[1, 2]]}, {'columns': ['a'], 'data': [[1, 2]]}):
            with self.assertRaises(ValueError):
                self.module.questionnaire_frame(self.models.Questionnaire(content=json.dumps({'survey_table': table})))
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session['uid'] = 1
        self.assertEqual(client.post('/api/questionnaires', json={'title': 'Overflow', 'respondent_count': 10 ** 30}).status_code, 400)
        output = io.BytesIO()
        pd.DataFrame({'title': ['Overflow'], 'respondent_count': [1e30]}).to_excel(output, index=False)
        self.assertEqual(client.post('/api/import/questionnaires', data={'mode': 'catalog', 'file': (io.BytesIO(output.getvalue()), 'overflow.xlsx')}).status_code, 400)

    def test_v_media_ranges_and_input_validation(self):
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session['uid'] = 1
        media = b'0123456789abcdef'
        item = client.post('/api/interviews', data={'title': 'Range fixture', 'category': 'teacher',
                           'file': (io.BytesIO(media), 'clip.mp4', 'application/octet-stream')}).get_json()['item']
        iid = item['id']
        self.assertEqual(item['file_type'], 'video/mp4')
        response = client.get(f'/api/interviews/{iid}/view', headers={'Range': 'bytes=4-7'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b'4567')
        self.assertEqual(response.headers['Content-Range'], 'bytes 4-7/16')
        self.assertEqual(client.get(f'/api/interviews/{iid}/view', headers={'Range': 'bytes=99-'}).status_code, 416)
        for duration in ('-1', '1.5', '999999999999999999999'):
            self.assertEqual(client.post('/api/interviews', data={'title': 'Bad duration', 'duration': duration}).status_code, 400)
            self.assertEqual(client.put(f'/api/interviews/{iid}', json={'duration': duration}).status_code, 400)
        self.assertEqual(client.post('/api/questionnaires', json={'title': 'Bad count', 'respondent_count': 1.5}).status_code, 400)
        found = client.get('/api/search?q=Range%20fixture').get_json()['items']
        self.assertIn('category=teacher', found[0]['_url'])
        for model in (self.models.Questionnaire, self.models.InterviewRecording, self.models.Document):
            self.models.get_db().query(model).delete()
        self.models.get_db().commit()

    def test_w_response_literal_and_finite_values(self):
        import json
        from visualizations import read_workbook
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session['uid'] = 1
        stream = io.BytesIO()
        pd.DataFrame({'答案': ['@long-text:literal', 'NA', 'NULL']}).to_excel(stream, index=False)
        imported = client.post('/api/import/questionnaires', data={'mode': 'responses', 'file': (io.BytesIO(stream.getvalue()), 'literal.xlsx')})
        self.assertEqual(imported.status_code, 200, imported.get_json())
        qid = imported.get_json()['ids'][0]
        saved = client.get(f'/api/questionnaires/{qid}').get_json()['item']
        self.assertEqual(json.loads(saved['content'])['survey_table']['data'], [['@long-text:literal'], ['NA'], ['NULL']])
        for value in (float('inf'), float('nan'), 'x' * 10001):
            with self.assertRaises(ValueError):
                self.module.questionnaire_frame(self.models.Questionnaire(content=json.dumps({'survey_table': {'columns': ['x'], 'data': [[value]]}})))
        stream = io.BytesIO()
        pd.DataFrame({'答案': ['  ', '\t'], '分数': [None, None]}).to_excel(stream, index=False)
        with self.assertRaises(ValueError):
            read_workbook(stream.getvalue())
        self.models.get_db().query(self.models.Questionnaire).delete()
        self.models.get_db().commit()

    def test_z_local_backup_trash_and_preview(self):
        import sqlite3
        import zipfile
        from contextlib import closing
        from backup_store import create_backup, daily_backup, runtime_lock
        from restore_backup import restore
        client = self.module.app.test_client()
        self.assertEqual(client.get('/api/local/trash').status_code, 401)
        with client.session_transaction() as session:
            session['uid'] = 1
        models = self.models
        db = models.get_db()
        tag = models.Tag(name='Restore fixture')
        db.add(tag)
        db.commit()
        tag_id = tag.id
        fixture = b'local fixture contents'
        created = client.post('/api/documents', data={'title': 'Recover me', 'category': 'teacher',
                              'tag_ids[]': str(tag_id), 'file': (io.BytesIO(fixture), 'fixture.txt')}).get_json()['item']
        identifier = created['id']
        deleted = client.delete(f'/api/documents/{identifier}').get_json()
        self.assertTrue(deleted['trash_id'])
        self.assertEqual(client.get(f'/api/documents/{identifier}/download').status_code, 404)
        self.assertEqual(client.get('/api/local/trash').get_json()['items'][0]['title'], 'Recover me')
        db = models.get_db()
        newer = models.Document(id=identifier, title='Keep newer record')
        db.add(newer)
        db.commit()
        restored = client.post(f'/api/local/trash/{deleted["trash_id"]}/restore').get_json()
        recovered_id = restored['restored_id']
        self.assertNotEqual(recovered_id, identifier)
        self.assertEqual(client.get(f'/api/documents/{recovered_id}/download').data, fixture)
        item = client.get(f'/api/documents/{recovered_id}').get_json()['item']
        self.assertEqual(item['category'], 'teacher')
        self.assertEqual(item['tags'][0]['id'], tag_id)
        output = io.BytesIO()
        with pd.ExcelWriter(output) as writer:
            pd.DataFrame({'标题': [f'row-{i}' for i in range(110)]}).to_excel(writer, index=False, sheet_name='第一页')
            pd.DataFrame({'标题': ['中文第二表']}).to_excel(writer, index=False, sheet_name='第二页')
        workbook_id = client.post('/api/documents', data={'title': 'Workbook',
                                  'file': (io.BytesIO(output.getvalue()), 'fixture.xlsx')}).get_json()['item']['id']
        first = client.get(f'/documents/{workbook_id}/preview').get_data(as_text=True)
        self.assertIn('下一页', first)
        self.assertIn('row-0', first)
        self.assertNotIn('row-60', first)
        second = client.get(f'/documents/{workbook_id}/preview?page=2').get_data(as_text=True)
        self.assertIn('row-60', second)
        other = client.get(f'/documents/{workbook_id}/preview?sheet=第二页').get_data(as_text=True)
        self.assertIn('中文第二表', other)
        word = io.BytesIO()
        with zipfile.ZipFile(word, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>中文访谈正文 &lt;script&gt;</w:t></w:r></w:p></w:body></w:document>')
        word_id = client.post('/api/documents', data={'title': 'Word fixture',
                             'file': (io.BytesIO(word.getvalue()), 'fixture.docx')}).get_json()['item']['id']
        page = client.get(f'/documents/{word_id}/preview').get_data(as_text=True)
        self.assertIn('中文访谈正文', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<script>', page)
        response = client.post('/api/local/backups')
        self.assertEqual(response.status_code, 201)
        name = response.get_json()['name']
        self.assertTrue(daily_backup())
        self.assertIsNone(daily_backup())
        backup = Path(self.temp.name) / 'backups' / name
        with closing(sqlite3.connect(backup)) as connection:
            self.assertEqual(connection.execute('PRAGMA quick_check').fetchone()[0], 'ok')
            self.assertEqual(connection.execute('SELECT file_data FROM documents WHERE id=?', (recovered_id,)).fetchone()[0], fixture)
        with runtime_lock():
            with self.assertRaises(RuntimeError):
                restore(name)
        db = models.get_db()
        db.add(models.Questionnaire(title='Added after backup'))
        db.commit()
        db.remove()
        models.engine.dispose()
        preserved = restore(name)
        self.assertTrue(preserved.is_file())
        self.assertEqual(models.get_db().query(models.Questionnaire).filter_by(title='Added after backup').count(), 0)
        self.assertEqual(client.get(f'/api/documents/{recovered_id}/download').data, fixture)
        deleted = client.delete(f'/api/documents/{recovered_id}').get_json()
        self.assertEqual(client.delete(f'/api/local/trash/{deleted["trash_id"]}').status_code, 200)
        self.assertEqual(client.post(f'/api/local/trash/{deleted["trash_id"]}/restore').status_code, 404)


if __name__ == '__main__':
    unittest.main()
