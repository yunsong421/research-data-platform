(() => {
    'use strict';
    const el = id => document.getElementById(id);
    let datasets = [];
    let detail = null;
    let revision = 0;
    let busy = false;
    const status = (message, error = false) => {
        el('viz-status').textContent = message;
        el('viz-status').classList.toggle('error', error);
    };
    async function api(path, options = {}) {
        const response = await fetch('/api/visualizations/' + path, options);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
        return data;
    }
    function clearChart() {
        revision += 1;
        el('chart-image').hidden = true;
        el('chart-image').removeAttribute('src');
        el('download-chart').hidden = true;
        el('download-chart').removeAttribute('href');
        el('chart-empty').hidden = false;
        el('chart-empty').textContent = '暂无图表';
        el('chart-statistics').textContent = '';
        el('result-details').hidden = true;
    }
    function table(target, columns, rows) {
        const table = document.createElement('table');
        const head = table.createTHead().insertRow();
        for (const name of columns) {
            const th = document.createElement('th');
            th.textContent = name;
            head.append(th);
        }
        const body = table.createTBody();
        for (const row of rows) {
            const tr = body.insertRow();
            for (const value of row) tr.insertCell().textContent = value ?? '—';
        }
        el(target).replaceChildren(table);
    }
    function modes() {
        const type = el('chart-type').value;
        const raw = type === 'histogram' || type === 'scatter';
        const needsY = type === 'scatter' || (!raw && el('aggregation').value !== 'count');
        el('aggregation-wrap').hidden = raw;
        el('y-wrap').hidden = !needsY;
        el('y-field').required = needsY;
        el('y-field').disabled = !needsY;
        el('top-wrap').hidden = raw;
        el('top-count').disabled = raw;
        el('bins-wrap').hidden = type !== 'histogram';
        el('bin-count').disabled = type !== 'histogram';
    }
    function recommend() {
        if (!detail) return;
        const suggestion = detail.suggestion;
        el('chart-type').value = suggestion.type;
        el('x-field').value = suggestion.x;
        if (suggestion.y) el('y-field').value = suggestion.y;
        el('aggregation').value = suggestion.aggregation;
        el('chart-title').value = '';
        clearChart();
        modes();
    }
    async function selectDataset() {
        clearChart();
        const current = revision;
        detail = null;
        el('chart-fields').disabled = true;
        el('delete-data').disabled = true;
        el('dataset-summary').textContent = '';
        el('preview-count').textContent = '';
        el('preview-table').textContent = '暂无数据';
        const key = el('dataset').value;
        if (!key) { status('暂无数据集'); return; }
        status('正在读取数据…');
        try {
            const data = await api('datasets/' + encodeURIComponent(key));
            if (current !== revision) return;
            detail = data;
            el('dataset-summary').textContent = `${detail.rows} 条记录 · ${detail.fields.length} 个字段`;
            el('preview-count').textContent = `前 ${detail.preview.data.length} 条`;
            const labels = {number: '数值', category: '分类', date: '日期'};
            for (const id of ['x-field', 'y-field']) {
                el(id).replaceChildren();
                for (const field of detail.fields) {
                    const option = new Option(`${field.name} · ${labels[field.type]}`, field.name);
                    option.title = `${field.name}；缺失 ${field.missing} 条；${field.unique} 个不同值`;
                    el(id).append(option);
                }
            }
            const number = detail.fields.find(field => field.type === 'number');
            if (number) el('y-field').value = number.name;
            table('preview-table', detail.preview.columns, detail.preview.data);
            el('chart-fields').disabled = false;
            el('delete-data').disabled = !datasets.find(item => item.id === key)?.removable;
            recommend();
            status('数据已就绪');
        } catch (error) { if (current === revision) status(error.message, true); }
    }
    async function refresh(selected) {
        const data = await api('datasets');
        datasets = data.datasets;
        const previous = selected || el('dataset').value;
        el('dataset').replaceChildren();
        for (const item of datasets) el('dataset').append(new Option(`${item.name} (${item.rows} 条)`, item.id));
        if (!datasets.length) el('dataset').append(new Option('暂无数据集', ''));
        if (datasets.some(item => item.id === previous)) el('dataset').value = previous;
        await selectDataset();
        if (data.warnings.length) status(data.warnings.join('；'), true);
    }
    el('dataset').addEventListener('change', selectDataset);
    el('refresh-data').addEventListener('click', () => refresh().catch(error => status(error.message, true)));
    el('recommend').addEventListener('click', () => { recommend(); status('已根据字段类型自动选择图表和数据'); });
    el('chart-form').addEventListener('change', () => { clearChart(); modes(); });
    el('chart-title').addEventListener('input', clearChart);
    el('upload-form').addEventListener('submit', async event => {
        event.preventDefault();
        const file = el('upload-file').files[0];
        if (!file) return;
        if (file.size > 10 * 1024 * 1024) { status('Excel 文件不能超过 10 MB。', true); return; }
        el('upload-button').disabled = true;
        status('正在上传…');
        try {
            const body = new FormData();
            body.append('file', file);
            const result = await api('datasets', {method: 'POST', body});
            await refresh(result.ids[0]);
            el('upload-form').reset();
            status(`上传成功，已保存 ${result.ids.length} 个工作表`);
        } catch (error) { status(error.message, true); }
        finally { el('upload-button').disabled = false; }
    });
    el('delete-data').addEventListener('click', async () => {
        const key = el('dataset').value;
        if (!key.startsWith('saved-') || !confirm('确认删除这个自定义数据集？')) return;
        el('delete-data').disabled = true;
        try {
            await api('datasets/' + key.slice(6), {method: 'DELETE'});
            await refresh();
            status('数据集已删除');
        } catch (error) { status(error.message, true); el('delete-data').disabled = false; }
    });
    el('chart-form').addEventListener('submit', async event => {
        event.preventDefault();
        if (busy || !detail) return;
        clearChart();
        const current = revision;
        busy = true;
        el('generate').disabled = true;
        el('chart-empty').textContent = '正在生成图表…';
        status('正在生成…');
        const payload = {dataset: el('dataset').value, type: el('chart-type').value,
            x: el('x-field').value, y: el('y-field').value, aggregation: el('aggregation').value,
            top: Number(el('top-count').value), bins: Number(el('bin-count').value), title: el('chart-title').value.trim()};
        try {
            const result = await api('chart', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
            if (current !== revision) return;
            const img = el('chart-image');
            img.src = result.image;
            await img.decode();
            if (current !== revision) return;
            img.alt = result.title;
            img.hidden = false;
            el('chart-empty').hidden = true;
            el('download-chart').href = result.image;
            el('download-chart').download = result.title.replace(/[\\/:*?"<>|]/g, '_') + '.png';
            el('download-chart').hidden = false;
            const stats = result.statistics;
            el('chart-statistics').textContent = `共 ${stats.total} 条 · 有效 ${stats.used} 条 · 空值 / 非数值剔除 ${stats.excluded} 条` +
                (stats.omitted_categories ? ` · 另有 ${stats.omitted_categories} 个类别未显示` : '');
            const rows = stats.x.map((x, index) => [x, stats.y[index]]);
            table('result-table', [stats.x_field, stats.y_field || '统计值'], rows.slice(0, 100));
            el('result-details').hidden = stats.type === 'histogram' || stats.type === 'scatter';
            status('图表已生成');
        } catch (error) {
            if (current === revision) { status(error.message, true); el('chart-empty').textContent = '图表生成失败'; }
        } finally { busy = false; el('generate').disabled = false; }
    });
    refresh().catch(error => status(error.message, true));
})();
