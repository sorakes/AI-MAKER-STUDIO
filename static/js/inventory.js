// Inventário: todos os arquivos da conta (gerados e enviados) com seleção em massa,
// apagar em lote e "Enviar para o nó" (botão direito): abre a área do nó já com a imagem nele.

const INV = { files: [], items: [], sel: new Set(), last: null, kind: '', origin: '', q: '', loaded: false };
const INV_WS_LABEL = { video: 'Video Studio', character: 'Personagens', product: 'Produtos' };
// Nós que recebem imagens como referência (porta R / referências do card).
const INV_TARGETS = { shot: (n) => (n.gen === 'image' ? 'Gerar imagem' : 'Gerar vídeo'), cast: () => 'Elenco', chargen: () => 'Personagem', prodgen: () => 'Produto' };

function initInventory() {
  const v = $('#view-inventory');
  if (!v || v.dataset.ready) return;
  v.dataset.ready = '1';
  v.querySelectorAll('[data-ikind]').forEach((b) => { b.onclick = () => { INV.kind = b.dataset.ikind; renderInventory(); }; });
  $('#invOrigin').onchange = (e) => { INV.origin = e.target.value; renderInventory(); };
  $('#invSearch').oninput = (e) => { INV.q = e.target.value.trim().toLowerCase(); renderInventory(); };
  $('#invAll').onclick = () => {
    const vis = invVisible();
    const all = vis.length && vis.every((f) => INV.sel.has(f.url));
    vis.forEach((f) => (all ? INV.sel.delete(f.url) : INV.sel.add(f.url)));
    renderInventory();
  };
  $('#invNone').onclick = () => { INV.sel.clear(); renderInventory(); };
  $('#invDel').onclick = () => invDelete([...INV.sel]);
  $('#invSend').onclick = (e) => invMenu(e.clientX, e.clientY, [...INV.sel], e.currentTarget);
  $('#invRefresh').onclick = () => loadInventory();
  window.addEventListener('keydown', (e) => {
    if (!document.body.classList.contains('on-inventory') || ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;
    if (e.key === 'Delete' && INV.sel.size) invDelete([...INV.sel]);
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); invVisible().forEach((f) => INV.sel.add(f.url)); renderInventory(); }
    if (e.key === 'Escape') { closeInvMenu(); INV.sel.clear(); renderInventory(); }
  });
}

async function loadInventory() {
  initInventory();
  const [data] = await Promise.all([api('/api/files'), loadAssets().catch(() => [])]);
  INV.files = data.files.filter((f) => ['image', 'video', 'audio'].includes(f.kind)).map((f) => ({
    ...f, thumb: f.kind === 'video' || /\.gif$/i.test(f.url) ? f.url.replace(/\.[^.]+$/, '_first.png') : f.kind === 'image' ? f.url : null,
    last_frame: f.kind === 'video' ? f.url.replace(/\.[^.]+$/, '_last.png') : undefined,
  }));
  INV.used = data.used;
  INV.limit = data.limit_mb;
  const urls = new Set(INV.files.map((f) => f.url));
  [...INV.sel].forEach((u) => { if (!urls.has(u)) INV.sel.delete(u); });
  INV.loaded = true;
  renderInventory();
}

// Soltos = não pertencem a um personagem/produto (esses aparecem dentro do item).
function invVisible() {
  return INV.files.filter((f) => !f.asset
    && (!INV.kind || f.kind === INV.kind)
    && (!INV.origin || (INV.origin === 'gen' ? f.folder !== 'uploads' : f.folder === 'uploads'))
    && (!INV.q || f.name.toLowerCase().includes(INV.q)));
}

function renderInventory() {
  const vis = invVisible();
  const items = (state.assets || []).filter((a) => a.saved && (!INV.q || a.name.toLowerCase().includes(INV.q)));
  document.querySelectorAll('#view-inventory [data-ikind]').forEach((b) => b.classList.toggle('active', b.dataset.ikind === INV.kind));
  const loose = INV.files.filter((f) => !f.asset);
  $('#invStats').textContent = `${loose.length} arquivo(s) soltos · ${items.length} item(ns) salvos · ${fmtBytes(INV.used || 0)}${INV.limit != null ? ` de ${INV.limit} MB` : ''}`;
  const n = INV.sel.size;
  $('#invSelCount').textContent = n ? `${n} selecionado(s) · ${fmtBytes([...INV.sel].reduce((t, u) => t + (INV.files.find((f) => f.url === u)?.size || 0), 0))}` : 'clique para selecionar · Shift = intervalo · botão direito = enviar para um nó';
  $('#invDel').disabled = !n;
  $('#invSend').disabled = !n;
  $('#invNone').hidden = !n;
  $('#invAll').innerHTML = `${ic('check')} ${vis.length && vis.every((f) => INV.sel.has(f.url)) ? 'Desmarcar visíveis' : 'Selecionar visíveis'}`;

  $('#invItems').innerHTML = items.length ? items.map((a) => {
    const files = INV.files.filter((f) => f.asset?.id === a.id);
    return `<button class="inv-item" data-asset="${a.id}" title="Abrir ${esc(a.name)} no canvas">
      <span class="inv-item-cover" style="${a.cover ? `background-image:url('${a.cover}')` : ''}"></span>
      <span class="inv-item-meta"><b>${esc(a.name)}</b><small>${a.kind === 'product' ? 'Produto' : 'Personagem'} · ${files.length} imagem(ns) dentro</small></span>
    </button>`;
  }).join('') : '<p class="note">Nenhum personagem ou produto salvo ainda.</p>';
  $('#invItemsBox').hidden = !items.length && !!INV.q;
  document.querySelectorAll('.inv-item').forEach((b) => { b.onclick = () => focusAsset(b.dataset.asset); });

  $('#invGrid').innerHTML = vis.length ? vis.map((f, i) => `
    <div class="inv-card ${INV.sel.has(f.url) ? 'sel' : ''} ${f.kind}" data-i="${i}" draggable="true" title="${esc(f.name)}">
      <div class="inv-thumb" ${f.thumb ? `style="background-image:url('${f.thumb}')"` : ''}>${f.kind === 'audio' ? ic('music') : ''}
        <span class="inv-check">${ic('check')}</span>
        <span class="inv-kind">${f.kind === 'video' ? ic('play') : f.kind === 'audio' ? 'áudio' : ic('image')}</span>
      </div>
      <div class="inv-meta"><b>${esc(f.name.replace(/^[0-9a-f]{8}_/, ''))}</b><small>${f.folder === 'uploads' ? 'enviado' : 'gerado'} · ${fmtBytes(f.size)} · ${new Date(f.mtime * 1000).toLocaleDateString(LOC)}</small></div>
    </div>`).join('') : `<p class="note">${INV.files.length ? 'Nada com esses filtros.' : 'Nenhum arquivo ainda: gere imagens/vídeos ou envie arquivos.'}</p>`;
  document.querySelectorAll('.inv-card').forEach((el) => {
    const f = vis[+el.dataset.i];
    el.addEventListener('click', (e) => {
      if (e.shiftKey && INV.last != null) {
        const [a, b] = [INV.last, +el.dataset.i].sort((x, y) => x - y);
        vis.slice(a, b + 1).forEach((x) => INV.sel.add(x.url));
      } else if (INV.sel.has(f.url)) INV.sel.delete(f.url);
      else INV.sel.add(f.url);
      INV.last = +el.dataset.i;
      renderInventory();
    });
    el.addEventListener('dblclick', () => window.open(f.url, '_blank'));
    el.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      if (!INV.sel.has(f.url)) { INV.sel.clear(); INV.sel.add(f.url); INV.last = +el.dataset.i; renderInventory(); }
      invMenu(e.clientX, e.clientY, [...INV.sel]);
    });
    el.addEventListener('dragstart', (e) => e.dataTransfer.setData(DRAG_MEDIA, JSON.stringify(f)));
  });
}

async function invDelete(urls) {
  if (!urls.length) return;
  const bytes = urls.reduce((t, u) => t + (INV.files.find((f) => f.url === u)?.size || 0), 0);
  const inUse = urls.filter((u) => Object.values(cv.docs).concat([{ nodes: cv.nodes }]).some((d) => JSON.stringify(d.nodes || []).includes(u))).length;
  if (!confirm(`Apagar ${urls.length} arquivo(s) (${fmtBytes(bytes)})?${inUse ? `\n${inUse} deles estão em uso em algum canvas.` : ''}\nNão dá para desfazer.`)) return;
  const r = await api('/api/files/delete', { urls });
  urls.forEach((u) => INV.sel.delete(u));
  toast(`${urls.length} arquivo(s) apagados · ${fmtBytes(r.freed)} liberados`);
  renderLibrary._sig = null;
  renderLibrary();
  loadInventory();
}

// ---------------- Enviar para um nó ----------------

async function invTargets() {
  const out = [];
  if (!cv.tabsLoaded) await loadTabs();
  for (const ws of ['video', 'character', 'product'].filter(wsAllowed)) {
    const tabs = cv.tabs[ws] || [{ key: ws }];
    for (const [ti, t] of tabs.entries()) {
      const d = t.key === cv.doc ? { nodes: cv.nodes } : await loadWorkspaceDoc(t.key).catch(() => ({ nodes: [] }));
      for (const n of d.nodes || []) {
        const label = INV_TARGETS[n.type]?.(n);
        if (label) out.push({ ws, tab: t.key, tabName: tabs.length > 1 ? tabTitle(t, ti) : '', id: n.id, label, name: n.name || label });
      }
    }
  }
  // nomes repetidos ganham número para dar para escolher ("Novo vídeo", "Novo vídeo 2"...)
  const seen = {};
  out.forEach((t) => { const k = `${t.ws}|${t.name}`; seen[k] = (seen[k] || 0) + 1; if (seen[k] > 1) t.name = `${t.name} ${seen[k]}`; });
  return out;
}

function closeInvMenu() { document.querySelector('.inv-menu')?.remove(); }

async function invMenu(x, y, urls, anchor) {
  closeInvMenu();
  if (!urls.length) return;
  const files = urls.map((u) => INV.files.find((f) => f.url === u)).filter(Boolean);
  const images = files.filter((f) => f.kind === 'image');
  const menu = document.createElement('div');
  menu.className = 'inv-menu';
  menu.innerHTML = `<div class="inv-menu-head">${files.length} selecionado(s)</div><div class="inv-menu-load">${ic('loader', 'spin')} buscando nós…</div>`;
  document.body.appendChild(menu);
  const place = () => {
    const r = anchor ? anchor.getBoundingClientRect() : null;
    const left = r ? r.left : x, top = r ? r.bottom + 6 : y;
    menu.style.left = `${Math.max(8, Math.min(window.innerWidth - menu.offsetWidth - 8, left))}px`;
    menu.style.top = `${Math.max(8, Math.min(window.innerHeight - menu.offsetHeight - 8, top))}px`;
  };
  place();
  const targets = await invTargets();
  if (!document.body.contains(menu)) return;
  const byWs = {};
  targets.forEach((t) => { (byWs[t.ws] = byWs[t.ws] || []).push(t); });
  const canMedia = files.some((f) => f.kind !== 'audio');
  menu.innerHTML = `<div class="inv-menu-head">${files.length} selecionado(s)${images.length < files.length ? ` · ${images.length} imagem(ns)` : ''}</div>
    <div class="inv-menu-sec">Enviar como referência para o nó</div>
    ${images.length ? (Object.keys(byWs).length ? Object.entries(byWs).map(([ws, list]) => `<div class="inv-menu-ws">${INV_WS_LABEL[ws]}</div>${list.map((t) => `
      <button data-target="${t.id}" data-ws="${t.ws}" data-tab="${t.tab}">${ic(t.label === 'Gerar vídeo' ? 'video' : t.label === 'Gerar imagem' ? 'image' : t.label === 'Elenco' ? 'users' : t.label === 'Produto' ? 'logo' : 'user')} <span>${esc(t.name)}</span><small>${esc(t.label)}${t.tabName ? ` · ${esc(t.tabName)}` : ''}</small></button>`).join('')}`).join('')
      : '<p class="inv-menu-empty">Nenhum nó que recebe imagens nos seus canvas ainda.</p>') : '<p class="inv-menu-empty">Só imagens viram referência.</p>'}
    ${canMedia ? `<div class="inv-menu-sec">Colocar no canvas como mídia</div>${['video', 'character', 'product'].filter(wsAllowed).map((ws) => `<button data-media="${ws}">${ic('plus')} <span>Novo nó de mídia</span><small>${INV_WS_LABEL[ws]}</small></button>`).join('')}` : ''}
    <div class="inv-menu-sep"></div>
    ${files.length === 1 ? `<button data-open>${ic('external')} <span>Abrir em nova aba</span></button>` : ''}
    ${state.me?.permissions?.delete_files !== false ? `<button data-del class="danger">${ic('trash')} <span>Apagar ${files.length > 1 ? `${files.length} arquivos` : 'arquivo'}</span></button>` : ''}`;
  place();
  menu.querySelectorAll('[data-target]').forEach((b) => { b.onclick = () => { closeInvMenu(); invSendToNode(b.dataset.ws, b.dataset.target, images, b.dataset.tab).catch((e) => toast(e.message)); }; });
  menu.querySelectorAll('[data-media]').forEach((b) => { b.onclick = () => { closeInvMenu(); invSendAsMedia(b.dataset.media, files.filter((f) => f.kind !== 'audio')).catch((e) => toast(e.message)); }; });
  menu.querySelector('[data-open]')?.addEventListener('click', () => { closeInvMenu(); window.open(files[0].url, '_blank'); });
  menu.querySelector('[data-del]')?.addEventListener('click', () => { closeInvMenu(); invDelete(files.map((f) => f.url)); });
  const outside = (e) => { if (!menu.contains(e.target)) { closeInvMenu(); window.removeEventListener('mousedown', outside, true); } };
  setTimeout(() => window.addEventListener('mousedown', outside, true));
}

function focusNode(n) {
  const r = $('#cvBoard').getBoundingClientRect();
  cv.pan = { x: r.width / 2 - (n.x + 180) * cv.zoom, y: Math.max(40, r.height / 3 - n.y * cv.zoom) };
  applyView();
  select(n.id);
  const el = document.querySelector(`.cv-node[data-id="${n.id}"]`);
  el?.classList.add('inv-flash');
  setTimeout(() => el?.classList.remove('inv-flash'), 1400);
}

// Vai para a área do nó, coloca as imagens nas referências dele e centraliza.
async function invSendToNode(ws, nodeId, images, tab) {
  await showStudioView(VIEW_OF_WS[ws]);
  if (tab && tab !== cv.doc) await switchWorkspace(ws, tab);
  const n = byId(nodeId);
  if (!n) throw new Error('O nó não existe mais');
  const have = new Set((n.refs || []).map((r) => r.url));
  const add = images.filter((f) => !have.has(f.url)).map((f) => ({ url: f.url, name: f.name.replace(/^[0-9a-f]{8}_/, ''), note: '' }));
  n.refs = [...(n.refs || []), ...add];
  n.autoKey = null;
  if (typeof invalidate === 'function') invalidate(n);
  renderNode(n);
  save();
  focusNode(n);
  sparkleBurst(n.x + 180, n.y + 60, { count: 12, spread: 140, colors: ['#10b981', '#facc15', '#ffffff'] });
  toast(add.length ? `${add.length} imagem(ns) em "${n.name || 'nó'}"` : 'Essas imagens já estavam nesse nó');
}

async function invSendAsMedia(ws, files) {
  await showStudioView(VIEW_OF_WS[ws]);
  const base = centerWorld();
  for (const [i, f] of files.entries()) {
    await addMediaNode({ ...f }, { x: base.x + (i % 4) * 320, y: base.y + Math.floor(i / 4) * 260 });
  }
  const last = cv.nodes[cv.nodes.length - 1];
  if (last) focusNode(last);
  toast(`${files.length} nó(s) de mídia criados`);
}
