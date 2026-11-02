// Visualizador em modal (um componente só): duplo clique em imagem ou vídeo (painel "Meus arquivos", inventário,
// preview e takes de qualquer node) abre o item ampliado. Imagem: ajustar à tela, zoom (roda, pinça, + −) e arrastar
// quando ampliada; duplo clique alterna ajustar/100%. Vídeo: controles nativos. ← → percorrem a lista de origem.
// Esc fecha (o <dialog> prende o foco e devolve o foco ao elemento que abriu).

// lista do painel "Meus arquivos" (atualizada por canvas.js em renderLibrary)
let LIBRARY_ITEMS = [];

const VIEWER = { items: [], i: 0, opener: null, el: null, nat: null, fit: true, zoom: 1, pointers: new Map(), pan: null, pinch: 0 };

function viewerOpen(items, startUrl, opener = document.activeElement) {
  const list = (items || []).filter((x) => x && x.url && (x.kind === 'image' || x.kind === 'video'));
  if (!list.length) return false;
  const at = Math.max(0, list.findIndex((x) => x.url === startUrl));
  VIEWER.items = list;
  VIEWER.opener = opener;
  // o foco volta a quem abriu: se o elemento não recebe foco (prévia, item do painel), ele passa a receber por script
  if (opener && opener.nodeType === 1 && !opener.hasAttribute('tabindex') && !opener.matches('a,button,input,select,textarea')) {
    opener.setAttribute('tabindex', '-1');
  }
  const dlg = $('#viewerDialog');
  if (!dlg.open) dlg.showModal();
  viewerShow(at);
  $('#viewerClose').focus();
  return true;
}

function viewerClose() {
  const dlg = $('#viewerDialog');
  if (dlg.open) dlg.close();
}

function viewerShow(i) {
  const list = VIEWER.items;
  VIEWER.i = i;
  const it = list[i];
  const stage = $('#viewerStage');
  stage.textContent = '';  // sem innerHTML: o nome e a URL nunca viram marcação
  let el;
  if (it.kind === 'video') {
    el = document.createElement('video');
    el.className = 'viewer-media';
    el.controls = true;
    el.playsInline = true;
    el.loop = $('#viewerLoop').checked;
    if ($('#viewerAutoplay').checked) { el.muted = true; el.autoplay = true; }  // autoplay só mudo
    if (it.thumb) el.poster = it.thumb;
    el.src = it.url;
    el.addEventListener('loadedmetadata', () => viewerMeta(`${Math.round(el.duration * 10) / 10}s · ${el.videoWidth}×${el.videoHeight}`));
  } else {
    el = document.createElement('img');
    el.className = 'viewer-media fit';
    el.alt = it.name || '';
    el.draggable = false;
    el.addEventListener('load', () => {
      VIEWER.nat = [el.naturalWidth, el.naturalHeight];
      viewerMeta(`${el.naturalWidth}×${el.naturalHeight}`);
      viewerZoomApply();
    });
    el.src = it.url;
  }
  stage.appendChild(el);
  VIEWER.el = el;
  VIEWER.nat = null;
  VIEWER.fit = true;
  VIEWER.zoom = 1;
  $('#viewerName').textContent = it.name || '';
  $('#viewerOpen').href = it.url;
  $('#viewerDownload').href = it.url;
  $('#viewerDownload').setAttribute('download', it.name || '');
  $('#viewerPrev').disabled = i === 0;
  $('#viewerNext').disabled = i >= list.length - 1;
  viewerMeta(list.length > 1 ? `${i + 1} / ${list.length}` : '');
  viewerZoomApply();
}

function viewerMeta(extra) {
  const it = VIEWER.items[VIEWER.i];
  const count = VIEWER.items.length > 1 ? `${VIEWER.i + 1} / ${VIEWER.items.length} · ` : '';
  $('#viewerMeta').textContent = [count + (it?.kind === 'video' ? 'vídeo' : 'imagem'), extra].filter(Boolean).join(' · ');
}

function viewerStep(d) {
  const next = VIEWER.i + d;
  if (next >= 0 && next < VIEWER.items.length) viewerShow(next);
}

function viewerZoomApply() {
  const el = VIEWER.el;
  if (!el || el.tagName !== 'IMG') { $('#viewerZoomLabel').textContent = ''; return; }
  if (VIEWER.fit || !VIEWER.nat) {
    el.classList.add('fit');
    el.style.width = '';
    el.style.height = '';
    $('#viewerZoomLabel').textContent = 'Ajustar';
  } else {
    el.classList.remove('fit');
    el.style.width = `${Math.round(VIEWER.nat[0] * VIEWER.zoom)}px`;
    el.style.height = 'auto';
    $('#viewerZoomLabel').textContent = `${Math.round(VIEWER.zoom * 100)}%`;
  }
}

// fator de zoom sobre o tamanho atual (a partir do ajuste à tela, se for o caso)
function viewerZoomBy(f) {
  const el = VIEWER.el;
  if (!el || el.tagName !== 'IMG' || !VIEWER.nat) return;
  if (VIEWER.fit) VIEWER.zoom = el.getBoundingClientRect().width / VIEWER.nat[0];
  VIEWER.zoom = Math.max(0.1, Math.min(8, VIEWER.zoom * f));
  VIEWER.fit = false;
  viewerZoomApply();
}

function viewerZoomReset(toFull) {
  VIEWER.fit = !toFull;
  VIEWER.zoom = 1;
  viewerZoomApply();
}

function viewerSources(startEl) {
  // origem da lista: painel, ou o node (takes; ou a própria mídia do nó de mídia)
  const lib = startEl.closest('.cv-lib-item');
  if (lib) {
    const idx = +lib.dataset.i;
    return { items: LIBRARY_ITEMS, url: LIBRARY_ITEMS[idx]?.url };
  }
  const nodeEl = startEl.closest('.cv-node');
  const n = nodeEl && typeof cv !== 'undefined' ? cv.nodes.find((x) => x.id === nodeEl.dataset.id) : null;
  if (!n) return null;
  const media = (u, kind, thumb, name) => ({ url: u, kind, thumb: thumb || '', name: name || u.split('/').pop().replace(/^[0-9a-f]{8}_/, '') });
  if (n.type === 'media' && n.media) return { items: [media(n.media.url, n.media.kind, n.media.thumb, n.name)], url: n.media.url };
  const takes = (n.takes || []).filter((t) => t && t.url).map((t) => media(t.url, t.kind, t.thumb));
  if (n.output?.url && !takes.some((t) => t.url === n.output.url)) takes.push(media(n.output.url, n.output.kind, n.output.thumb));
  const img = startEl.closest('.cv-preview')?.querySelector('img:not(.ghost), video');
  const url = img ? (img.currentSrc || img.src) : n.output?.url;
  return { items: takes, url: url ? new URL(url, location.href).pathname : null };
}

function viewerInit() {
  const dlg = $('#viewerDialog');
  if (!dlg) return;
  $('#viewerClose').addEventListener('click', viewerClose);
  $('#viewerPrev').addEventListener('click', () => viewerStep(-1));
  $('#viewerNext').addEventListener('click', () => viewerStep(1));
  $('#viewerZoomIn').addEventListener('click', () => viewerZoomBy(1.25));
  $('#viewerZoomOut').addEventListener('click', () => viewerZoomBy(1 / 1.25));
  $('#viewerFit').addEventListener('click', () => viewerZoomReset(false));
  $('#viewerLoop').addEventListener('change', (e) => { if (VIEWER.el?.tagName === 'VIDEO') VIEWER.el.loop = e.target.checked; });
  // fechar: Esc, botão ou clique fora (no fundo) — sempre devolve o foco
  dlg.addEventListener('click', (e) => { if (e.target === dlg) viewerClose(); });
  dlg.addEventListener('close', () => {
    const v = VIEWER.el;
    if (v?.tagName === 'VIDEO') { v.pause(); v.removeAttribute('src'); v.load(); }
    $('#viewerStage').textContent = '';
    VIEWER.el = null;
    VIEWER.items = [];
    const back = VIEWER.opener;
    VIEWER.opener = null;
    if (back?.isConnected && typeof back.focus === 'function') back.focus();
  });
  dlg.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') { e.preventDefault(); viewerStep(-1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); viewerStep(1); }
    else if (e.key === '+' || e.key === '=') { e.preventDefault(); viewerZoomBy(1.25); }
    else if (e.key === '-') { e.preventDefault(); viewerZoomBy(1 / 1.25); }
    else if (e.key === '0') { e.preventDefault(); viewerZoomReset(false); }
  });
  // roda do mouse: zoom
  const stage = $('#viewerStage');
  stage.addEventListener('wheel', (e) => {
    if (!VIEWER.el || VIEWER.el.tagName !== 'IMG') return;
    e.preventDefault();
    viewerZoomBy(e.deltaY < 0 ? 1.15 : 1 / 1.15);
  }, { passive: false });
  // arrastar (ampliada) e pinça (dois dedos)
  stage.addEventListener('pointerdown', (e) => {
    VIEWER.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (VIEWER.pointers.size === 2) {
      const [a, b] = [...VIEWER.pointers.values()];
      VIEWER.pinch = Math.hypot(a.x - b.x, a.y - b.y);
      VIEWER.pan = null;
    } else if (!VIEWER.fit) {
      VIEWER.pan = { x: e.clientX, y: e.clientY };
      stage.classList.add('dragging');
    }
  });
  stage.addEventListener('pointermove', (e) => {
    if (!VIEWER.pointers.has(e.pointerId)) return;
    const prev = VIEWER.pointers.get(e.pointerId);
    VIEWER.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (VIEWER.pointers.size === 2 && VIEWER.el?.tagName === 'IMG') {
      const [a, b] = [...VIEWER.pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (VIEWER.pinch > 0 && d > 0) viewerZoomBy(d / VIEWER.pinch);
      VIEWER.pinch = d;
    } else if (VIEWER.pan && !VIEWER.fit) {
      stage.scrollLeft -= e.clientX - prev.x;
      stage.scrollTop -= e.clientY - prev.y;
      VIEWER.pan = { x: e.clientX, y: e.clientY };
    }
  });
  const release = (e) => {
    VIEWER.pointers.delete(e.pointerId);
    if (VIEWER.pointers.size < 2) VIEWER.pinch = 0;
    if (VIEWER.pointers.size === 0) { VIEWER.pan = null; stage.classList.remove('dragging'); }
  };
  stage.addEventListener('pointerup', release);
  stage.addEventListener('pointercancel', release);
  stage.addEventListener('dblclick', (e) => {
    if (VIEWER.el?.tagName !== 'IMG') return;
    e.preventDefault();
    viewerZoomReset(!VIEWER.fit);  // alterna ajustar / 100%
  });
  // duplo clique em mídia, em qualquer lugar do canvas, do painel ou do inventário (inventário chama viewerOpen)
  document.addEventListener('dblclick', (e) => {
    const t = e.target;
    if (t.closest?.('[data-del]')) return;  // botão de apagar do painel
    const libItem = t.closest?.('.cv-lib-item');
    if (libItem) {
      const item = LIBRARY_ITEMS[+libItem.dataset.i];
      if (item && (item.kind === 'image' || item.kind === 'video')) viewerOpen(LIBRARY_ITEMS, item.url, libItem);
      return;
    }
    const media = t.closest?.('.cv-preview img:not(.ghost), .cv-preview video');
    if (media) {
      const src = viewerSources(media);
      if (src?.items?.length) viewerOpen(src.items, src.url, media);
    }
  });
}

document.addEventListener('DOMContentLoaded', viewerInit);
