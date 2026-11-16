(() => {
    const el = id => document.getElementById(id);
    const status = (text, error = false) => { el('local-status').textContent = text; el('local-status').classList.toggle('error', error); };
    async function api(path, options = {}) {
        const response = await fetch('/api/local/' + path, options);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || '操作失败');
        return data;
    }
    const cell = (row, text) => { const td = row.insertCell(); td.textContent = text; return td; };
    function empty(body, columns, text) { const row = body.insertRow(); const td = cell(row, text); td.colSpan = columns; }
    async function backups() {
        const data = await api('backups');
        const body = el('backup-body'); body.replaceChildren();
        if (!data.items.length) empty(body, 3, '暂无备份');
        data.items.forEach(item => { const row = body.insertRow(); cell(row, item.name); cell(row, (item.bytes / 1024 / 1024).toFixed(2) + ' MB'); cell(row, item.created_at.replace('T', ' ')); });
    }
    async function trash() {
        const data = await api('trash');
        const body = el('trash-body'); body.replaceChildren();
        const names = {documents: '文档', interviews: '访谈', questionnaires: '问卷', datasets: '数据集'};
        if (!data.items.length) empty(body, 5, '回收站为空');
        data.items.forEach(item => {
            const row = body.insertRow(); cell(row, names[item.item_type] || item.item_type); cell(row, item.title); cell(row, item.original_id); cell(row, item.deleted_at.replace('T', ' ').slice(0, 19));
            const actions = cell(row, ''); const group = document.createElement('div'); group.className = 'local-actions'; actions.append(group);
            for (const [label, method, suffix] of [['恢复', 'POST', '/restore'], ['彻底删除', 'DELETE', '']]) {
                const button = document.createElement('button'); button.textContent = label; button.className = 'btn btn-sm ' + (suffix ? 'btn-outline' : 'btn-danger'); group.append(button);
                button.addEventListener('click', async () => {
                    if (!suffix && !confirm('彻底删除“' + item.title + '”？回收站将无法恢复。')) return;
                    button.disabled = true;
                    try { const result = await api('trash/' + item.id + suffix, {method}); await trash(); status(suffix ? '已恢复，ID：' + result.restored_id : '已彻底删除'); }
                    catch (error) { status(error.message, true); button.disabled = false; }
                });
            }
        });
    }
    el('backup-now').addEventListener('click', async () => {
        el('backup-now').disabled = true; status('正在备份…');
        try { await api('backups', {method: 'POST'}); await backups(); status('备份完成'); }
        catch (error) { status(error.message, true); }
        finally { el('backup-now').disabled = false; }
    });
    el('refresh-trash').addEventListener('click', () => trash().catch(error => status(error.message, true)));
    Promise.all([backups(), trash()]).catch(error => status(error.message, true));
})();
