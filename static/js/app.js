// Toast
function showToast(msg, type) {
    var el = document.getElementById('toast');
    var m = document.getElementById('toast-message');
    el.className = 'toast';
    if (type) el.classList.add(type);
    m.textContent = msg;
    el.classList.add('show');
    clearTimeout(el._hide);
    el._hide = setTimeout(function () {
        el.classList.remove('show');
    }, 3000);
}
// Modal
function openModal(title, html) {
    document.getElementById("dialog-title").textContent = title;
    document.getElementById("dialog-body").innerHTML = html;
    document.getElementById("modal-dialog").showModal();
}
function closeModal() {
    var dlg = document.getElementById('modal-dialog');
    if (dlg && dlg.open) dlg.close();
}
document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeModal();
});
// Format helpers
function fmtSize(bytes) {
    if (!bytes) return '0 B';
    var u = ['B','KB','MB','GB']; var i = 0; var s = bytes;
    while (s >= 1024 && i < 3) { s /= 1024; i++; }
    return s.toFixed(1) + ' ' + u[i];
}
function fmtDate(s) {
    if (!s) return '-';
    var d = new Date(s);
    return d.getFullYear() + '-' +
        ('0' + (d.getMonth() + 1)).slice(-2) + '-' +
        ('0' + d.getDate()).slice(-2) + ' ' +
        ('0' + d.getHours()).slice(-2) + ':' +
        ('0' + d.getMinutes()).slice(-2);
}
function esc(s) {
    var d = document.createElement('div');
    d.textContent = s || ''; return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
// Tag helpers
function tagBadge(t) {
    var c = t.color;
    return '<span class="tag-badge" style="background:' + c + '22;color:' + c + ';border:1px solid ' + c + '44">' + esc(t.name) + '</span>';
}
function tagBar(tags) {
    if (!tags || !tags.length)
        return '<span class="text-muted">\u65e0\u6807\u7b7e</span>';
    return tags.map(tagBadge).join('');
}
function loadTagFilter(id, lb) {
    var sel = document.getElementById(id);
    if (!sel) return;
    fetch('/api/tags').then(function(r){return r.json()}).then(function(d){
        sel.innerHTML = '<option value="">' + (lb || '\u5168\u90e8\u6807\u7b7e') + '</option>';
        d.items.forEach(function(t){
            var o = document.createElement('option');
            o.value = t.id; o.textContent = t.name;
            sel.appendChild(o);
        });
    });
}
function tagSelect(tags, selIds) {
    if (!selIds) selIds = [];
    if (!Array.isArray(selIds)) selIds = [selIds];
    var h = '<div class="tag-select">';
    (tags || []).forEach(function(t){
        var s = selIds.indexOf(t.id) !== -1 ? 'selected' : '';
        h += '<span class="tag-option ' + s + '" data-id="' + t.id + '" style="color:' + t.color + ';background:' + t.color + '18" onclick="this.classList.toggle(\'selected\')">' + esc(t.name) + '</span>';
    });
    return h + '</div>';
}
function getTagIds(container) {
    var ids = [];
    container.querySelectorAll('.tag-option.selected').forEach(function(el){
        ids.push(parseInt(el.getAttribute('data-id')));
    });
    return ids;
}
// Pagination
function pg(page, pages, total, cb) {
    if (pages <= 1) return '';
    var h = '<button onclick="(' + cb + ')(' + (page-1) + ')" ' + (page<=1?'disabled':'') + '>◀ 上一页</button>';
    var s = Math.max(1, page-2); var e = Math.min(pages, page+2);
    if (s > 1) {
        h += '<button onclick="(' + cb + ')(1)">1</button>';
        if (s > 2) h += '<span class="pagination-info">...</span>';
    }
    for (var i = s; i <= e; i++) {
        h += '<button onclick="(' + cb + ')(' + i + ')" class="' + (i===page?'active':'') + '">' + i + '</button>';
    }
    if (e < pages) {
        if (e < pages-1) h += '<span class="pagination-info">...</span>';
        h += '<button onclick="(' + cb + ')(' + pages + ')">' + pages + '</button>';
    }
    h += '<button onclick="(' + cb + ')(' + (page+1) + ')" ' + (page>=pages?'disabled':'') + '>下一页 ▶</button>';
    return h + '<span class="pagination-info">共 ' + total + ' 条</span>';
}
// Drop zone
function setupDrop(dzId, inpId, prevId) {
    var dz = document.getElementById(dzId);
    var inp = document.getElementById(inpId);
    var prev = document.getElementById(prevId);
    if (!dz || !inp) return;
    dz.addEventListener('click', function(){inp.click()});
    inp.addEventListener('change', function(){handleFiles(this.files, prev)});
    dz.addEventListener('dragover', function(e){
        e.preventDefault();dz.classList.add('dragover')});
    dz.addEventListener('dragleave', function(){
        dz.classList.remove('dragover')});
    dz.addEventListener('drop', function(e){
        e.preventDefault();
        dz.classList.remove('dragover');
        var dropped = e.dataTransfer.files;
        try {
            var dt = new DataTransfer();
            for (var i = 0; i < dropped.length; i++) dt.items.add(dropped[i]);
            inp.files = dt.files;
        } catch (err) {
            inp.files = dropped;
        }
        handleFiles(inp.files, prev);
    });
}
function handleFiles(files, prev) {
    if (!files || !files.length) return;
    var f = files[0];
    if (prev) {
        var h = '<div class="upload-preview">';
        if (f.type && f.type.startsWith('image/'))
            h += '<img src="' + URL.createObjectURL(f) + '">';
        else if (f.type && f.type.startsWith('video/'))
            h += '<span style="font-size:32px">🎬</span>';
        else if (f.type && f.type.startsWith('audio/'))
            h += '<span style="font-size:32px">🎤</span>';
        else
            h += '<span style="font-size:32px">📄</span>';
        var names = [];
        var total = 0;
        for (var i = 0; i < files.length; i++) { names.push(files[i].name); total += files[i].size; }
        h += '<div class="file-info"><div class="file-name">已选 ' + files.length + ' 个：' + esc(names.join('\u3001')) + '</div>';
        h += '<div class="file-size">' + fmtSize(total) + '</div></div></div>';
        prev.innerHTML = h;
    }
}
// Export / Import
function exportData(t) {
    window.location.href = '/api/export/' + t;
}
function showImport(t) {
    var names = {questionnaires:'\u95ee\u5377', interviews:'\u8bbf\u8c08', documents:'\u6587\u6863'};
    var h = '<form id="imp-form" enctype="multipart/form-data">';
    if (t !== 'questionnaires') {
        h += '<input type="hidden" name="category" value="' + (typeof activeCat !== 'undefined' && activeCat === 'teacher' ? 'teacher' : 'student') + '">';
    }
    if (t === 'questionnaires') {
        h += '<div class="form-group"><label>导入类型</label><select class="form-control" name="mode">';
        h += '<option value="responses">答卷数据表</option><option value="catalog">问卷列表（导出格式）</option></select></div>';
    }
    h += '<div class="form-group"><label>\u9009\u62e9 Excel \u6587\u4ef6</label>';
    h += '<input type="file" class="form-control" name="file" accept=".xlsx" required></div>';
    h += '<div class="form-actions">';
    h += '<button type="button" class="btn btn-outline" onclick="closeModal()">\u53d6\u6d88</button>';
    h += '<button type="submit" class="btn btn-primary">\u5bfc\u5165</button></div></form>';
    openModal('\u5bfc\u5165' + names[t] + '\u6570\u636e', h);
    document.getElementById('imp-form').addEventListener('submit', function(e){
        e.preventDefault();
        var button = this.querySelector('button[type="submit"]');
        if (button.disabled) return;
        button.disabled = true;
        button.textContent = '正在导入…';
        var fd = new FormData(this);
        fetch('/api/import/' + t, {method:'POST', body:fd})
            .then(function(r){return r.json()})
            .then(function(d){
                if (d.ok) {
                    showToast(d.mode === 'responses' ? '已导入 ' + d.count + ' 份问卷，共 ' + d.response_count + ' 条答卷' : '成功导入 ' + d.count + ' 条数据', 'success');
                    closeModal();
                    if (typeof loadData === 'function') loadData(1); else location.reload();
                } else {
                    showToast(d.error || '\u5bfc\u5165\u5931\u8d25', 'error');
                }
            })
            .catch(function(){showToast('导入失败，请检查文件或连接后重试', 'error')})
            .finally(function(){button.disabled = false; button.textContent = '导入';});
    });
}
