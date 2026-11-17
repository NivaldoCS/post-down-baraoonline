/* Post-Down Baraoonline.oficial — interface conectada à API real (ver docs/CONTRATO_API.md). */
(() => {
  'use strict';
  const $ = sel => document.querySelector(sel);
  const input = $('#url');
  const status = $('#form-status');
  const results = $('#results');
  const grid = $('#media-grid');
  const dialog = $('#help-dialog');
  const searchBtn = $('#search');
  const isIOS = /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const SHARE_PREFETCH_LIMIT = 150 * 1024 * 1024;

  let job = null;
  let pollTimer = null;
  const shareFiles = new Map(); // index -> File (pré-carregado para o botão Compartilhar)

  // ---------- utilidades ----------
  function tell(el, message, kind = '') {
    el.textContent = message;
    el.classList.toggle('error', kind === 'error');
    el.classList.toggle('ok', kind === 'ok');
  }
  function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) if (c !== null && c !== undefined) el.append(c instanceof Node ? c : String(c));
    return el;
  }
  function icon(id) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS(ns, 'use');
    use.setAttribute('href', '#' + id);
    svg.append(use);
    return svg;
  }
  function formatBytes(n) {
    if (!n && n !== 0) return null;
    if (n < 1024 * 1024) return `${(n / 1024).toLocaleString('pt-BR', { maximumFractionDigits: 0 })} KB`;
    return `${(n / 1024 / 1024).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} MB`;
  }
  function formatDuration(s) {
    if (!s) return null;
    const m = Math.floor(s / 60), r = Math.round(s % 60);
    return `${m}:${String(r).padStart(2, '0')}`;
  }
  async function api(path, options = {}) {
    const opts = { credentials: 'same-origin', headers: { 'Accept': 'application/json' }, ...options };
    if (opts.method && opts.method !== 'GET') {
      opts.headers = { ...opts.headers, 'X-Post-Down': '1', 'Content-Type': 'application/json' };
    }
    let resp;
    try {
      resp = await fetch(path, opts);
    } catch {
      throw { code: 'NETWORK', message: 'Sem conexão com o servidor. Verifique a internet e tente novamente.' };
    }
    let data = null;
    try { data = await resp.json(); } catch { /* resposta sem JSON */ }
    if (!resp.ok) {
      const err = (data && data.error) || { code: 'HTTP_' + resp.status, message: 'O servidor não conseguiu concluir o pedido.' };
      if (err.code === 'UNAUTHORIZED' && path !== '/api/login') showLogin('Sua sessão expirou. Entre novamente.');
      throw err;
    }
    return data;
  }

  // ---------- autenticação ----------
  function showLogin(message = '') {
    stopPolling();
    $('#login-panel').hidden = false;
    $('#download-panel').hidden = true;
    $('#logout').hidden = true;
    results.hidden = true;
    tell($('#login-status'), message, message ? 'error' : '');
    setTimeout(() => $('#password').focus(), 50);
  }
  function showApp() {
    $('#login-panel').hidden = true;
    $('#download-panel').hidden = false;
    $('#logout').hidden = false;
  }
  $('#login-form').addEventListener('submit', async event => {
    event.preventDefault();
    const pw = $('#password');
    const st = $('#login-status');
    if (!pw.value) { tell(st, 'Digite a senha de acesso.', 'error'); pw.focus(); return; }
    const btn = $('#login-button');
    btn.disabled = true;
    tell(st, 'Verificando…');
    try {
      await api('/api/login', { method: 'POST', body: JSON.stringify({ password: pw.value }) });
      pw.value = '';
      tell(st, '');
      showApp();
      input.focus();
    } catch (err) {
      tell(st, err.message, 'error');
      pw.select();
    } finally {
      btn.disabled = false;
    }
  });
  $('#logout').addEventListener('click', async () => {
    try { await api('/api/logout', { method: 'POST', body: '{}' }); } catch { /* segue para o login */ }
    job = null;
    showLogin('Você saiu. Os arquivos temporários desta sessão foram apagados.');
  });

  // ---------- busca ----------
  function platformOf(value) {
    try {
      const url = new URL(/^https?:\/\//i.test(value) ? value : 'https://' + value);
      const host = url.hostname.toLowerCase();
      if (['instagram.com', 'www.instagram.com', 'm.instagram.com', 'instagr.am', 'www.instagr.am'].includes(host)) return 'Instagram';
      if (['threads.com', 'www.threads.com', 'threads.net', 'www.threads.net'].includes(host)) return 'Threads';
    } catch { /* tratado abaixo */ }
    return null;
  }
  function setSearching(on) {
    searchBtn.disabled = on;
    searchBtn.setAttribute('aria-busy', on ? 'true' : 'false');
    $('#search-label').textContent = on ? 'Consultando publicação…' : 'Buscar mídia';
  }
  $('#download-form').addEventListener('submit', async event => {
    event.preventDefault();
    const value = input.value.trim();
    if (!value) { tell(status, 'Cole o link da publicação para continuar.', 'error'); input.focus(); return; }
    if (!platformOf(value)) {
      tell(status, 'Use um link de publicação do Instagram ou do Threads. Confira o endereço e tente novamente.', 'error');
      input.focus();
      return;
    }
    stopPolling();
    shareFiles.clear();
    setSearching(true);
    tell(status, 'Consultando publicação…');
    try {
      job = await api('/api/extract', { method: 'POST', body: JSON.stringify({ url: value }) });
      const n = job.items.length;
      tell(status, `${n} ${n === 1 ? 'mídia encontrada' : 'mídias encontradas'}. Escolha o que deseja baixar.`, 'ok');
      renderJob(true);
    } catch (err) {
      // O link digitado é preservado no campo.
      tell(status, err.message || 'Falha na extração.', 'error');
      results.hidden = true;
    } finally {
      setSearching(false);
    }
  });
  $('#paste').addEventListener('click', async () => {
    try {
      if (!navigator.clipboard?.readText) throw new Error('indisponível');
      const text = await navigator.clipboard.readText();
      input.value = text.trim();
      tell(status, input.value ? 'Link colado. Toque em “Buscar mídia”.' : 'A área de transferência está vazia. Cole um link manualmente.');
      input.focus();
    } catch {
      tell(status, 'Toque e segure no campo e selecione “Colar”. O navegador não liberou a colagem automática.');
      input.focus();
    }
  });
  $('#clear').addEventListener('click', () => {
    input.value = '';
    clearResults();
    tell(status, 'Campo limpo. Cole um novo link.');
    input.focus();
  });
  $('#new-search').addEventListener('click', () => {
    clearResults();
    input.select();
    input.focus();
  });
  function clearResults() {
    stopPolling();
    job = null;
    shareFiles.clear();
    results.hidden = true;
    grid.replaceChildren();
    $('#step-tag').textContent = '01 / 02';
  }

  // ---------- resultados ----------
  function describeFile(item) {
    const f = item.file;
    if (f) {
      const parts = [];
      if (f.width && f.height) parts.push(`${f.width}×${f.height}`);
      parts.push((f.ext || '').toUpperCase());
      const size = formatBytes(f.size_bytes); if (size) parts.push(size);
      const dur = formatDuration(f.duration); if (dur) parts.push(dur);
      if (item.kind === 'video') parts.push(f.has_audio ? 'com áudio' : 'sem faixa de áudio');
      return parts.filter(Boolean).join(' · ') + ' — medido no arquivo';
    }
    const r = item.reported || {};
    const parts = [];
    if (r.width && r.height) parts.push(`${r.width}×${r.height} informado pela plataforma`);
    const dur = formatDuration(r.duration); if (dur) parts.push(dur);
    return parts.length ? parts.join(' · ') : 'Resolução e tamanho conhecidos após preparar o arquivo';
  }

  function itemCard(item, total) {
    const isVideo = item.kind === 'video';
    const noun = isVideo ? 'vídeo' : 'foto';
    const title = `${isVideo ? 'Vídeo' : 'Foto'}${total > 1 ? ` ${item.index + 1} de ${total}` : ''}`;
    const art = h('div', { class: 'media-art' + (isVideo ? ' video-art' : ' photo-art') });
    if (item.thumb_url) {
      art.append(h('img', { src: item.thumb_url, alt: `Miniatura: ${title}`, loading: 'lazy', decoding: 'async' }));
    } else {
      art.append(icon(isVideo ? 'play' : 'photo'));
    }
    art.append(h('span', { class: 'media-label' }, icon(isVideo ? 'play' : 'photo'), isVideo ? 'VÍDEO' : 'FOTO'));

    const info = h('p', { class: 'file-info' + (item.error ? ' error' : '') },
      item.error ? item.error.message : describeFile(item));
    const actions = h('div', { class: 'media-actions' });

    if (item.state === 'ready' && item.download_url) {
      actions.append(h('a', { class: 'download-link', href: item.download_url, download: item.file.file_name },
        `Baixar ${noun}`, icon('arrow')));
      const shared = shareFiles.get(item.index);
      if (shared) {
        actions.append(h('button', { type: 'button', class: 'secondary', onclick: () => shareFile(shared) },
          'Compartilhar', icon('share')));
      }
    } else if (item.state === 'preparing') {
      actions.append(h('button', { type: 'button', disabled: true, 'aria-busy': 'true' },
        'Preparando arquivo…', h('span', { class: 'spinner', 'aria-hidden': 'true' })));
    } else {
      const label = item.state === 'error' ? 'Tentar novamente' : `Preparar ${noun}`;
      actions.append(h('button', { type: 'button', onclick: () => prepareItem(item.index) }, label, icon('arrow')));
    }
    return h('article', { class: 'media-card' }, art,
      h('div', { class: 'media-body' }, h('h3', {}, title), info, actions));
  }

  function zipCard(zip, total) {
    const state = h('p', { class: 'batch-state' + (zip.error ? ' error' : '') });
    let action;
    if (zip.state === 'ready') {
      state.textContent = `Download disponível · ${zip.file.count} arquivos · ${formatBytes(zip.file.size_bytes)}`;
      action = h('a', { class: 'download-link', href: zip.download_url, download: zip.file.file_name }, 'Baixar todos em ZIP', icon('arrow'));
    } else if (zip.state === 'preparing') {
      state.textContent = 'Preparando todos os arquivos…';
      action = h('button', { type: 'button', disabled: true, 'aria-busy': 'true' }, 'Preparando ZIP…', h('span', { class: 'spinner', 'aria-hidden': 'true' }));
    } else {
      state.textContent = zip.error ? zip.error.message : 'Arquivos originais, na ordem do carrossel.';
      action = h('button', { type: 'button', onclick: prepareZip }, zip.error ? 'Tentar ZIP novamente' : 'Baixar todos em ZIP');
    }
    return h('article', { class: 'batch-card' },
      h('span', { class: 'batch-number' }, String(total).padStart(2, '0')),
      h('h3', {}, 'Tudo do mesmo post. ', h('br'), 'Em um só lugar.'),
      h('p', {}, 'Baixe cada item ou o carrossel completo em ZIP.'), state, action);
  }

  function renderJob(scroll = false) {
    if (!job) return;
    const total = job.items.length;
    $('#result-title').textContent = job.author
      ? `${total} ${total === 1 ? 'mídia' : 'mídias'} de @${job.author}`
      : `${total} ${total === 1 ? 'mídia encontrada' : 'mídias encontradas'}`;
    const link = $('#source-link');
    link.href = job.source_url;
    const caption = $('#caption');
    caption.textContent = job.caption || '';
    caption.hidden = !job.caption;
    $('#copy-caption').hidden = !job.caption;
    const cards = job.items.map(item => itemCard(item, total));
    if (job.zip.available) cards.push(zipCard(job.zip, total));
    grid.replaceChildren(...cards);
    $('#ios-tip').hidden = !isIOS;
    const mins = Math.max(1, Math.round((job.expires_at * 1000 - Date.now()) / 60000));
    $('#expiry-note').textContent = `Os arquivos desta busca são apagados do servidor em cerca de ${mins} min. Extrator: ${job.extractor}.`;
    $('#step-tag').textContent = '02 / 02';
    results.hidden = false;
    if (scroll) {
      results.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
    }
  }

  $('#copy-caption').addEventListener('click', async () => {
    const text = job?.caption || '';
    try {
      await navigator.clipboard.writeText(text);
      tell(status, 'Legenda copiada.', 'ok');
    } catch {
      const range = document.createRange();
      range.selectNodeContents($('#caption'));
      const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
      tell(status, 'Legenda selecionada. Toque em “Copiar” no menu do navegador.');
    }
  });

  // ---------- preparo e polling ----------
  async function prepareItem(index) {
    if (!job) return;
    tell(status, 'Preparando arquivo…');
    try {
      job = await api(`/api/jobs/${encodeURIComponent(job.job_id)}/items/${index}/prepare`, { method: 'POST', body: '{}' });
      renderJob();
      startPolling();
    } catch (err) { tell(status, err.message, 'error'); if (err.code === 'JOB_EXPIRED') clearResults(); }
  }
  async function prepareZip() {
    if (!job) return;
    tell(status, 'Preparando arquivo ZIP…');
    try {
      job = await api(`/api/jobs/${encodeURIComponent(job.job_id)}/zip`, { method: 'POST', body: '{}' });
      renderJob();
      startPolling();
    } catch (err) { tell(status, err.message, 'error'); if (err.code === 'JOB_EXPIRED') clearResults(); }
  }
  function signature(j) { return JSON.stringify([j.items.map(i => i.state), j.zip.state]); }
  function busy(j) { return j.items.some(i => i.state === 'preparing') || j.zip.state === 'preparing'; }
  function stopPolling() { if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; } }
  function startPolling() {
    stopPolling();
    const tick = async () => {
      if (!job) return;
      const before = job;
      try {
        job = await api(`/api/jobs/${encodeURIComponent(job.job_id)}`);
      } catch (err) {
        tell(status, err.message, 'error');
        if (err.code === 'JOB_EXPIRED') clearResults();
        return;
      }
      announceChanges(before, job);
      if (signature(before) !== signature(job)) renderJob();
      if (busy(job)) pollTimer = setTimeout(tick, 1200);
    };
    pollTimer = setTimeout(tick, 900);
  }
  function announceChanges(before, after) {
    for (const item of after.items) {
      const old = before.items.find(i => i.index === item.index);
      if (old?.state === 'preparing' && item.state === 'ready') {
        tell(status, `Download disponível: ${item.kind === 'video' ? 'vídeo' : 'foto'} ${item.index + 1}.`, 'ok');
        maybePrefetchForShare(item);
      } else if (old?.state === 'preparing' && item.state === 'error') {
        tell(status, item.error.message, 'error');
      }
    }
    if (before.zip.state === 'preparing' && after.zip.state === 'ready') tell(status, 'Download disponível: ZIP com todos os itens.', 'ok');
    if (before.zip.state === 'preparing' && after.zip.state === 'error') tell(status, after.zip.error.message, 'error');
  }

  // ---------- compartilhamento (iPhone/Android) ----------
  async function maybePrefetchForShare(item) {
    if (!navigator.canShare || !item.file || item.file.size_bytes > SHARE_PREFETCH_LIMIT) return;
    try {
      const resp = await fetch(item.download_url, { credentials: 'same-origin' });
      if (!resp.ok) return;
      const blob = await resp.blob();
      const file = new File([blob], item.file.file_name, { type: blob.type });
      if (navigator.canShare({ files: [file] })) {
        shareFiles.set(item.index, file);
        renderJob();
      }
    } catch { /* compartilhamento é opcional; o download continua disponível */ }
  }
  async function shareFile(file) {
    try {
      await navigator.share({ files: [file] });
    } catch (err) {
      if (err && err.name !== 'AbortError') tell(status, 'O navegador não permitiu compartilhar. Use “Baixar”.', 'error');
    }
  }

  // ---------- ajuda ----------
  document.querySelectorAll('[data-help]').forEach(b => b.addEventListener('click', () => dialog.showModal()));
  $('#close-help').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const r = dialog.getBoundingClientRect();
    if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
  });

  // ---------- início ----------
  api('/api/session').then(s => (s.authenticated ? showApp() : showLogin()))
    .catch(() => showLogin('Não foi possível falar com o servidor.'));
})();
