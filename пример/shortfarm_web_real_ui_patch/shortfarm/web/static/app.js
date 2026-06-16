
const api = {
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw await makeError(r);
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body || {})});
    if (!r.ok) throw await makeError(r);
    return r.json();
  }
};

async function makeError(r) {
  try {
    const data = await r.json();
    return new Error(data?.detail?.message || data?.message || JSON.stringify(data));
  } catch { return new Error(await r.text() || `HTTP ${r.status}`); }
}

function esc(v){ return String(v ?? '').replace(/[&<>\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function fmtNum(v){ return Number(v || 0).toString(); }
function shortPath(v){ if(!v) return '—'; const s=String(v); return s.length>44 ? '…' + s.slice(-43) : s; }
function percent(done,total,status){ if(status==='done') return 100; if(status==='failed') return 100; if(!total) return status==='running'?50:0; return Math.round((done||0)*100/total); }
function ruStatus(v){ return ({queued:'в очереди',running:'в работе',done:'готово',failed:'ошибка',rendering:'рендер',inbox:'входящие',reviewing:'на просмотре',reviewed:'просмотрено',skipped:'пропущено',ok:'готово',preview:'план'}[v] || v || '—'); }
function badgeClass(s){ return s==='done'||s==='reviewed'||s==='ok'?'b-ok':s==='failed'?'b-err':s==='running'||s==='queued'||s==='reviewing'?'b-info':s==='rendering'?'b-warn':'b-dim'; }
function badge(s, pulse=false){ return `<span class="badge ${badgeClass(s)}">${pulse?'<span class="dot pulse" style="background:currentColor"></span>':''}${esc(ruStatus(s))}</span>`; }
function showError(target, err){ const el = typeof target === 'string' ? document.getElementById(target) : target; el.innerHTML = `<div class="err-line">${esc(err.message || err)}</div>`; }

let currentView = 'dashboard';
let secsVal = 60;
let splitMode = 'file';
const skipList = [];
let lastJobs = [];
let lastVideos = [];
let lastClips = [];

function nav(id, btn) {
  currentView = id;
  document.querySelectorAll('.v').forEach(el => el.classList.remove('on'));
  document.getElementById('v-' + id).classList.add('on');
  document.querySelectorAll('.nb').forEach(b => b.classList.remove('on'));
  if (btn) btn.classList.add('on');
  if (id === 'dashboard') loadDashboard();
  if (id === 'queue') loadJobs();
  if (id === 'videos') loadVideos();
  if (id === 'clips') loadClips();
}

async function loadDashboard() {
  try {
    const s = await api.get('/api/status');
    const jobs = s.jobs || {};
    const clips = s.clips || {};
    const videos = s.videos_by_status || {};
    const runningJobs = jobs.running || 0;
    const queuedJobs = jobs.queued || 0;
    const queuedClips = clips.queued || 0;
    const failedClips = clips.failed || 0;

    document.getElementById('st-videos').textContent = fmtNum(s.videos_total);
    document.getElementById('st-segments').textContent = fmtNum(s.segments_total);
    document.getElementById('st-jobs').textContent = fmtNum(runningJobs + queuedJobs);
    document.getElementById('st-clips').textContent = fmtNum(queuedClips);
    document.getElementById('st-videos-sub').textContent = `${videos.inbox||0} входящие · ${videos.reviewed||0} просмотрено`;
    document.getElementById('st-jobs-sub').textContent = `${runningJobs} в работе · ${queuedJobs} в очереди`;
    document.getElementById('st-clips-sub').textContent = `${clips.done||0} готово · ${failedClips} ошибок`;
    document.getElementById('nav-jobs').textContent = runningJobs + queuedJobs || '';
    document.getElementById('nav-videos').textContent = s.videos_total || '';
    document.getElementById('nav-clips').textContent = queuedClips || '';
    document.getElementById('job-pulse').classList.toggle('pulse', runningJobs > 0);

    renderRunningBanner(s.latest_jobs || []);
    renderJobsTable('dash-jobs', s.latest_jobs || [], false);
    renderVideoStatusBars(videos, s.videos_total || 0);
    renderErrors(s.recent_errors || []);
  } catch (err) { showError('dash-jobs', err); }
}

function renderRunningBanner(jobs) {
  const running = jobs.find(j => j.status === 'running');
  const el = document.getElementById('running-banner');
  if (!running) { el.innerHTML = ''; return; }
  const p = percent(running.done_items, running.total_items, running.status);
  el.innerHTML = `<div class="banner"><div class="banner-top"><div class="banner-left"><span class="dot pulse" style="background:var(--acc);box-shadow:0 0 7px var(--acc);"></span><span style="font-size:11px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.05em;">Задача #${running.id} · В работе</span><span class="mono mid">${esc(running.type)}</span></div><span class="mono inf" style="font-size:12px;font-weight:700;">${p}%</span></div><div class="pbar"><div class="pf pf-info" style="width:${p}%"></div></div><div class="mono mid" style="margin-top:6px;">${esc(running.current_file || '—')} · ${running.done_items||0}/${running.total_items||'?'}</div></div>`;
}

function renderVideoStatusBars(counts, total) {
  const labels = [['inbox','входящие','b-dim'],['reviewing','на просмотре','b-info'],['reviewed','просмотрено','b-ok'],['skipped','пропущено','b-dim']];
  document.getElementById('video-status-bars').innerHTML = labels.map(([key,label,cls]) => {
    const n = counts[key] || 0;
    const w = total ? Math.round(n*100/total) : 0;
    return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><span class="badge ${cls}" style="min-width:96px;justify-content:center">${label}</span><div class="pbar" style="flex:1"><div class="pf" style="width:${w}%;background:var(--acc)"></div></div><span class="mono txt" style="min-width:22px;text-align:right">${n}</span></div>`;
  }).join('') || '<div class="empty">Нет данных</div>';
}

function renderErrors(errors) {
  const el = document.getElementById('recent-errors');
  if (!errors.length) { el.innerHTML = '<div class="empty">Ошибок нет</div>'; return; }
  el.innerHTML = errors.map(e => `<div style="padding:10px 15px;border-bottom:1px solid var(--border)"><div style="display:flex;gap:6px;align-items:center;margin-bottom:4px"><span class="badge b-err" style="font-size:9px">${esc(e.kind)} #${esc(e.id)}</span><span class="mono dim">${esc(e.at || '')}</span></div><div style="font-size:11px;color:var(--mid);line-height:1.4">${esc(e.error || 'Ошибка без текста')}</div></div>`).join('');
}

function setSecs(v) { secsVal = Math.min(300, Math.max(10, v)); document.getElementById('secs-val').textContent = secsVal + 's'; document.getElementById('secs-range').value = secsVal; }
function adjSecs(d) { setSecs(secsVal + d); }
function setMode(mode) { splitMode = mode; document.getElementById('mode-file').classList.toggle('on', mode === 'file'); document.getElementById('mode-folder').classList.toggle('on', mode === 'folder'); document.getElementById('path-lbl').textContent = mode === 'file' ? 'Путь к видеофайлу' : 'Путь к папке'; document.getElementById('split-path').placeholder = mode === 'file' ? '/home/user/videos/my_video.mp4' : '/home/user/videos/'; }
function addChip() { const inp = document.getElementById('skip-in'); const v = inp.value.trim(); if (!v) return; skipList.push(v); renderChips(); inp.value = ''; }
function removeChip(i) { skipList.splice(i, 1); renderChips(); }
function renderChips() { document.getElementById('chips').innerHTML = skipList.map((s, i) => `<span class="chip">${esc(s)}<button class="chip-del" onclick="removeChip(${i})">×</button></span>`).join(''); }
function resetSplit() { document.getElementById('split-message').classList.remove('on'); document.getElementById('split-btn').disabled = false; document.getElementById('split-path').value = ''; skipList.length = 0; renderChips(); document.getElementById('split-preview').innerHTML = '<div class="empty">План появится после запуска с галкой «Только план».</div>'; }

async function doSplit() {
  const path = document.getElementById('split-path').value.trim();
  if (!path) { document.getElementById('split-path').focus(); return; }
  const dry = document.getElementById('dry-run').checked;
  const overwrite = document.getElementById('overwrite').checked;
  const btn = document.getElementById('split-btn');
  btn.disabled = true;
  btn.textContent = dry ? 'Считаю план…' : 'Нарезаю…';
  try {
    const data = await api.post('/api/split', {kind: splitMode, path, seconds: secsVal, skip: skipList, dry_run: dry, overwrite});
    renderSplitResult(data);
    document.getElementById('split-msg-title').textContent = dry ? 'План готов' : 'Готово';
    document.getElementById('split-msg-sub').textContent = dry ? `${data.segments_count || 0} сегментов` : `Создано сегментов: ${data.segments_count || 0}`;
    document.getElementById('split-message').classList.add('on');
    await loadDashboard();
  } catch (err) { showError('split-preview', err); }
  finally { btn.disabled = false; btn.textContent = 'Запустить'; }
}

function renderSplitResult(data) {
  const el = document.getElementById('split-preview');
  if (data.kind === 'folder') {
    const files = data.files || [];
    if (!files.length) { el.innerHTML = '<div class="empty">Видео в папке не найдены</div>'; return; }
    el.innerHTML = `<table class="tbl"><thead><tr><th>Файл</th><th>Статус</th><th>Сегменты</th><th>Ошибка</th></tr></thead><tbody>${files.map(f => `<tr><td><span class="mono mid ov">${esc(shortPath(f.path))}</span></td><td>${badge(f.status)}</td><td class="mono txt">${esc(f.result?.segments_count || 0)}</td><td class="err ov">${esc(f.error || '')}</td></tr>`).join('')}</tbody></table>`;
    return;
  }
  const segs = (data.segments || []).slice(0, 80);
  el.innerHTML = `<div style="padding:10px 15px"><div class="mono mid">Файл: ${esc(shortPath(data.source_path))}</div><div class="mono mid">Длительность: ${esc(data.duration_text)} · Сегментов: ${esc(data.segments_count)} · Вывод: ${esc(shortPath(data.output_dir))}</div></div><table class="tbl"><thead><tr><th>#</th><th>Начало</th><th>Конец</th><th>Длит.</th><th>Файл</th></tr></thead><tbody>${segs.map(s => `<tr><td class="mono dim">${s.index}</td><td class="mono mid">${s.start_sec.toFixed(2)}</td><td class="mono mid">${s.end_sec.toFixed(2)}</td><td class="mono txt">${s.duration_sec.toFixed(2)}</td><td><span class="mono mid ov">${esc(shortPath(s.path || '—'))}</span></td></tr>`).join('')}</tbody></table>`;
}

async function loadJobs() { try { const data = await api.get('/api/jobs'); lastJobs = data.jobs || []; renderJobCounts(data.counts || {}); renderJobsTable('jobs-table', lastJobs, true); } catch (err) { showError('jobs-table', err); } }
function renderJobCounts(counts) { const total = Object.values(counts).reduce((a,b)=>a+b,0); for (const k of ['all','queued','running','done','failed']) { const el=document.getElementById('jobs-cnt-'+k); if(el) el.textContent = k==='all' ? total : (counts[k] || ''); } }
function filterJobs(tab, status) { tab.closest('.tabs').querySelectorAll('.tab').forEach(t=>t.classList.remove('on')); tab.classList.add('on'); const rows = status==='all' ? lastJobs : lastJobs.filter(j => j.status === status); renderJobsTable('jobs-table', rows, true); }
function renderJobsTable(targetId, rows, full) { const el = document.getElementById(targetId); if (!rows.length) { el.innerHTML = '<div class="empty">Нет задач</div>'; return; } el.innerHTML = `<table class="tbl"><thead><tr><th>#</th><th>Тип</th><th>Статус</th><th>Прогресс</th><th>Файл</th>${full?'<th>Ошибка</th><th>Старт</th>':'<th>Создано</th>'}</tr></thead><tbody>${rows.map(j=>{ const p=percent(j.done_items,j.total_items,j.status); return `<tr data-s="${esc(j.status)}"><td class="mono dim">#${j.id}</td><td class="mono mid">${esc(j.type)}</td><td>${badge(j.status, j.status==='running')}</td><td style="min-width:100px"><div class="pbar-row"><span class="mono dim">${j.done_items||0}/${j.total_items||'?'}</span><span class="mono dim">${p}%</span></div><div class="pbar"><div class="pf ${j.status==='failed'?'pf-err':j.status==='done'?'pf-ok':'pf-info'}" style="width:${p}%"></div></div></td><td><span class="mono mid ov">${esc(j.current_file || '—')}</span></td>${full?`<td class="err ov" style="max-width:180px">${esc(j.error||'')}</td><td class="mono dim">${esc(j.started_at||j.created_at||'')}</td>`:`<td class="mono dim">${esc(j.created_at||'')}</td>`}</tr>`; }).join('')}</tbody></table>`; }

async function loadVideos() { try { const data = await api.get('/api/videos'); lastVideos = data.videos || []; renderVideoCounts(data.counts || {}); renderVideosTable(lastVideos); } catch (err) { showError('videos-table', err); } }
function renderVideoCounts(counts) { const total = lastVideos.length; for (const k of ['all','inbox','reviewing','reviewed','skipped']) { const el=document.getElementById('vid-cnt-'+k); if(el) el.textContent = k==='all' ? total : (counts[k] || ''); } }
function filterVideos(tab, status) { tab.closest('.tabs').querySelectorAll('.tab').forEach(t=>t.classList.remove('on')); tab.classList.add('on'); renderVideosTable(status==='all' ? lastVideos : lastVideos.filter(v => v.review_status === status)); }
function renderVideosTable(rows) { const el = document.getElementById('videos-table'); if(!rows.length){ el.innerHTML='<div class="empty">Нет видео</div>'; return; } el.innerHTML = `<table class="tbl"><thead><tr><th>#</th><th>Название</th><th>Длительность</th><th>Статус</th><th class="r">Метки</th><th class="r">Клипы</th><th>Источник</th></tr></thead><tbody>${rows.map(v=>`<tr data-s="${esc(v.review_status)}"><td class="mono dim">#${v.id}</td><td class="mono txt">${esc(v.title)}</td><td class="mono mid">${esc(v.duration_text)}</td><td>${badge(v.review_status)}</td><td class="mono txt r">${v.mark_count}</td><td class="mono warn r">${v.clip_count}</td><td><span class="mono dim ov">${esc(shortPath(v.source_path))}</span></td></tr>`).join('')}</tbody></table>`; }

async function loadClips() { try { const data = await api.get('/api/clips'); lastClips = data.clips || []; renderClipCounts(data.counts || {}); renderClipsTable(lastClips); } catch (err) { showError('clips-table', err); } }
function renderClipCounts(counts) { const total = Object.values(counts).reduce((a,b)=>a+b,0); for (const k of ['all','queued','rendering','done','failed']) { const el=document.getElementById('clip-cnt-'+k); if(el) el.textContent = k==='all' ? total : (counts[k] || ''); } }
function filterClips(tab, status) { tab.closest('.tabs').querySelectorAll('.tab').forEach(t=>t.classList.remove('on')); tab.classList.add('on'); renderClipsTable(status==='all' ? lastClips : lastClips.filter(c => c.status === status)); }
function renderClipsTable(rows) { const el=document.getElementById('clips-table'); if(!rows.length){ el.innerHTML='<div class="empty">Нет клипов</div>'; return; } el.innerHTML = `<table class="tbl"><thead><tr><th>#</th><th>Видео</th><th>Метка</th><th>Статус</th><th>Режим</th><th>Вывод / ошибка</th></tr></thead><tbody>${rows.map(c=>`<tr data-s="${esc(c.status)}"><td class="mono dim">#${c.id}</td><td class="mono txt">${esc(c.video_title)}</td><td class="mono mid">${c.mark_id?'#'+c.mark_id:'—'}</td><td>${badge(c.status, c.status==='rendering')}</td><td class="mono dim">${esc(c.cut_mode)}</td><td><span class="mono ${c.status==='failed'?'err':c.status==='done'?'ok':'mid'} ov">${esc(shortPath(c.error || c.output_path || c.temp_path || '—'))}</span></td></tr>`).join('')}</tbody></table>`; }
async function renderQueued(){ try { const data = await api.post('/api/render', {limit: 10}); alert(`Готово. Отрендерено: ${data.count}`); await loadClips(); await loadDashboard(); } catch(err){ alert(err.message); } }
async function retryFailedClips(){ try { const data = await api.post('/api/retry-failed', {}); alert(`Сброшено в очередь: ${data.reset_count}`); await loadClips(); await loadDashboard(); } catch(err){ alert(err.message); } }

async function loadDoctor(){ try { const data=await api.get('/api/doctor'); document.getElementById('doctor-box').innerHTML = `<table class="tbl"><tbody>${Object.entries(data).map(([k,v])=>`<tr><td class="mono dim">${esc(k)}</td><td class="mono ${String(v).startsWith('ERROR')?'err':'mid'}">${esc(v)}</td></tr>`).join('')}</tbody></table>`; } catch(err){ showError('doctor-box',err); } }

window.addEventListener('DOMContentLoaded', () => { setSecs(60); loadDashboard(); });
setInterval(() => { if (currentView === 'dashboard') loadDashboard(); if (currentView === 'queue') loadJobs(); if (currentView === 'clips') loadClips(); }, 5000);
