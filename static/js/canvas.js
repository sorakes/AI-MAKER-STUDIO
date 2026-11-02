// Canvas do estúdio
// Nós: mídia (imagem/vídeo/áudio), gerar imagem, gerar vídeo, transição, frame, aprovação, montagem.
// Ligações: mídia/nó → vídeo = primeiro frame (último frame se for vídeo); vídeos → montagem.
// Cada nó escolhe o modelo entre os liberados no painel admin; o prompt técnico é escrito por trás.

// ws = área (video | character | product); doc = aba aberta dela ("video" = principal, "video~id" = extra).
const cv = { nodes: [], edges: [], pan: { x: 60, y: 40 }, zoom: 0.9, sel: null, saveT: null, link: null, ws: 'video', doc: 'video', docs: {}, tabs: {} };

// Cada área tem o próprio canvas e a própria paleta de nós.
const WS_PALETTE = {
  video: ['brief', 'cast', 'video', 'image', 'transition', 'frame', 'upscale', 'relight', 'edit', 'review', 'montage'],
  character: ['chargen', 'save', 'sheet', 'image', 'upscale', 'relight', 'edit'],
  product: ['prodgen', 'save', 'sheet', 'image', 'upscale', 'relight', 'edit'],
};
const WS_EMPTY = {
  video: 'Seu estúdio está vazio.<br>Envie fotos/vídeos ou adicione um nó <b>Gerar vídeo</b>, <b>Gerar imagem</b> ou <b>Roteiro</b>.',
  character: 'Nenhum personagem ainda.<br>Adicione um nó <b>Personagem</b>: ele é a ficha. Solte fotos (camisa, logo, rosto) no canvas e ligue na porta <b>R</b>.',
  product: 'Nenhum produto ainda.<br>Adicione um nó <b>Produto</b>: ele é a ficha. Solte fotos do produto, rótulo e logo e ligue na porta <b>R</b>.',
};
const PORT_Y = 23; // centro das portas (casar com .cv-port no CSS)
const DRAG_MEDIA = 'application/x-studio-media';
const DRAG_NODE = 'application/x-studio-node';
const START_MODES = { auto: 'Automático', still: 'Gerar imagem primeiro', text: 'Direto do texto' };
const DEFAULT_DURATIONS = [3, 4, 5, 6, 8, 10];
const uid = (p) => `${p}-${Math.random().toString(36).slice(2, 8)}`;
const byId = (id) => cv.nodes.find((n) => n.id === id);

document.addEventListener('DOMContentLoaded', async () => {
  await loadMe();
  [state.catalog, state.models] = await Promise.all([api('/api/catalog'), api('/api/models')]);
  if (!state.models.image.length && !state.models.video.length) toast('Nenhum modelo liberado ainda: peça ao administrador para configurar as keys');
  await loadAssets().catch(() => {});
  await initCanvas();
  initStudioTabs();
  setInterval(() => loadMe().catch(() => {}), 30000);
});

// ---------------- Modelos ----------------

const fits = (m, need) => Object.entries(need || {}).every(([k, v]) => !v || m.caps[k]);

// Mesmo critério do servidor: padrão do admin que atende, senão o mais barato.
function autoModel(kind, need) {
  return (state.models[kind] || []).filter((m) => fits(m, need))
    .sort((a, b) => (b.is_default - a.is_default) || (a.cost - b.cost))[0] || null;
}

// Automático com muitas referências: o mais barato que aceite todas (senão o que aceita mais).
function imageFor(id, need, count) {
  if ((id && id !== 'auto') || !need?.refs || !count) return chosenModel('image', id, need);
  const list = (state.models.image || []).filter((m) => fits(m, need));
  const fit = list.filter((m) => (m.caps.max_refs || 4) >= count).sort((a, b) => (b.is_default - a.is_default) || (a.cost - b.cost))[0];
  return fit || list.sort((a, b) => (b.caps.max_refs || 4) - (a.caps.max_refs || 4))[0] || null;
}

function chosenModel(kind, id, need, preferRefs = false) {
  if (preferRefs && kind === 'video' && (!id || id === 'auto')) {
    const good = (state.models.video || []).filter((x) => fits(x, { ...need, refs: true }) && !x.caps.refs_weak)
      .sort((a, b) => (b.is_default - a.is_default) || (a.cost - b.cost))[0];
    const m = good || autoModel(kind, { ...need, refs: true });
    if (m) return m;
  }
  if (id && id !== 'auto') {
    const m = (state.models[kind] || []).find((x) => String(x.id) === String(id));
    if (m && fits(m, need)) return m;
  }
  return autoModel(kind, need);
}

const costLabel = (m, secs) => (!m || !m.cost ? '' : m.cost_unit === 'second'
  ? `~${fmtTok(m.cost * (secs || 5))} (${fmtTok(m.cost)}/s)` : `~${fmtTok(m.cost)}`);

function modelSelect(key, kind, value, need, autoNeed = need) {
  const list = state.models[kind] || [];
  const auto = autoModel(kind, autoNeed);
  const autoLabel = 'Automático';
  const tail = (m) => (kind === 'image' ? (m.caps.refs ? ` · até ${m.caps.max_refs || 4} referências` : ' · sem referências')
    : kind !== 'video' ? '' : m.caps.refs ? ` · até ${m.caps.max_refs || 4} imagens${m.caps.refs_weak ? ` · ${m.caps.refs_weak}` : ''}` : ' · ignora referências');
  const o = (m) => `<option value="${m.id}" ${String(m.id) === String(value) ? 'selected' : ''} ${fits(m, need) ? '' : 'disabled'}>${esc(m.label)}${fits(m, need) ? tail(m) : ' — não serve aqui'}</option>`;
  let body = list.map(o).join('');
  if (kind === 'video') {
    // Recomendados: recebem várias imagens + texto (elenco, produto, desenho de câmera)
    const rec = list.filter((m) => m.caps.refs && !m.caps.refs_weak);
    const weak = list.filter((m) => m.caps.refs && m.caps.refs_weak);
    const rest = list.filter((m) => !m.caps.refs);
    body = `<optgroup label="★ Recomendados · várias imagens + texto">${rec.length ? rec.map(o).join('')
      : '<option disabled>nenhum instalado · o admin adiciona em Keys & Modelos</option>'}</optgroup>`
      + (weak.length ? `<optgroup label="◐ Aceitam referências, mas oscilam">${weak.map(o).join('')}</optgroup>` : '')
      + (rest.length ? `<optgroup label="⚠ Outros · só 1º/último frame, ignoram referências e desenho">${rest.map(o).join('')}</optgroup>` : '');
  }
  return `<select data-k="${key}"><option value="auto">${esc(autoLabel)}${auto ? ` (${esc(auto.label)})` : ''}</option>${body}</select>`;
}

// Passos que o servidor vai rodar para este nó (espelho de jobs.plan) e custo estimado.
function planOf(n) {
  if (n.gen === 'image') {
    const need = refsOf(n).length ? { refs: true } : { t2i: true };
    const m = imageFor(n.model, need, refsOf(n).length) || chosenModel('image', n.model, { t2i: true });
    return { steps: m ? [{ kind: 'image', m }] : [], missing: m ? null : 'Nenhum modelo de imagem disponível' };
  }
  const start = startFrameOf(n);
  const pref = wantsRefs(n);
  if (start && composeOn(n)) {
    const img = imageFor(n.imageModel, { refs: true }, refsOf(n).length + 1);
    const v = chosenModel('video', n.model, { i2v: true }, pref);
    return { steps: [img && { kind: 'image', m: img }, v && { kind: 'video', m: v }].filter(Boolean),
      missing: !img ? 'Falta um modelo de imagem com referências para colocar o elenco no 1º frame' : !v ? 'Nenhum modelo imagem→vídeo disponível' : null };
  }
  if (start) {
    const v = chosenModel('video', n.model, { i2v: true }, pref);
    return { steps: v ? [{ kind: 'video', m: v }] : [], missing: v ? null : 'Nenhum modelo imagem→vídeo disponível' };
  }
  let direct = n.startMode !== 'still' && chosenModel('video', n.model, { t2v: true }, pref);
  if (direct && refsOf(n).length && !direct.caps.refs && n.startMode !== 'text') direct = null; // ignoraria as referências
  if (direct) return { steps: [{ kind: 'video', m: direct }] };
  if (n.startMode === 'text') return { steps: [], missing: 'Nenhum modelo texto→vídeo disponível' };
  const img = imageFor(n.imageModel, refsOf(n).length ? { refs: true } : { t2i: true }, refsOf(n).length) || chosenModel('image', n.imageModel, { t2i: true });
  const v = chosenModel('video', n.model, { i2v: true }, pref);
  return { steps: [img && { kind: 'image', m: img }, v && { kind: 'video', m: v }].filter(Boolean),
    missing: !img ? 'Nenhum modelo de imagem para o 1º frame' : !v ? 'Nenhum modelo imagem→vídeo disponível' : null };
}

// Com 1º frame + elenco/referências: o app gera antes um 1º frame com eles dentro da cena (padrão: ligado).
// Os modelos de vídeo do OpenRouter só recebem 1º/último frame: referência solta seria ignorada.
// O modelo de vídeo escolhido recebe imagens de referência (elenco, desenho de câmera)?
function videoModelOf(n) { return (planOf(n).steps || []).find((s) => s.kind === 'video')?.m || null; }
const videoTakesRefs = (n) => !!videoModelOf(n)?.caps?.refs;
const hasComposable = (n) => castNodesOf(n).length > 0 || refsOf(n).length > 0;
const wantsRefs = (n) => refsOf(n).length > 0 || castNodesOf(n).length > 0 || !!diagramOf(n);
const diagramOf = (n) => { const f = startFrameOf(n); return f && n.direction?.diagram_url && n.direction.frame === f && n.sendDiagram !== false ? n.direction.diagram_url : null; };
// escolha do modelo de vídeo sem depender do plano (evita laço planOf -> composeOn)
const videoPick = (n) => chosenModel('video', n.model, startFrameOf(n) ? { i2v: true } : {}, wantsRefs(n));
// Modelo que recebe referências leva o frame + referências direto: não precisa gerar o 1º frame com elas.
const composeOn = (n) => n.gen !== 'image' && !!startFrameOf(n) && hasComposable(n) && n.compose !== 'off' && !videoPick(n)?.caps?.refs;
// Imagens que vão para o vídeo: frame (vira imagem 1), desenho da câmera, referências
function refsLoad(n) {
  const m = videoModelOf(n);
  if (!m?.caps?.refs) return null;
  const frame = startFrameOf(n) ? 1 : 0;
  const diag = diagramOf(n) ? 1 : 0;
  const refs = refsOf(n).length;
  return { m, max: m.caps.max_refs || 4, frame, diag, refs, total: frame + diag + refs };
}

// Aviso quando o modelo de vídeo não recebe referências: o que deixa de ir e o que o app faz no lugar.
function noRefsWarning(n) {
  const m = videoModelOf(n);
  const load = refsLoad(n);
  if (load && load.total > load.max) {
    const parts = [load.frame && '1 frame', load.diag && '1 desenho de câmera', load.refs && `${load.refs} referência(s)`].filter(Boolean).join(' + ');
    const cut = load.total - load.max;
    return `<div class="cv-warn cv-norefs over">${ic('alert')} <span><b>${esc(m.label)}</b> aceita até <b>${load.max} imagens</b> e este nó tem <b>${load.total}</b> (${parts}). As ${cut} última(s) referência(s) não vão: remova ${cut} ou escolha um modelo que aceite mais.</span></div>`;
  }
  if (load && m.caps.refs_weak) return `<div class="cv-warn cv-norefs">${ic('alert')} <span><b>${esc(m.label)}</b> recebe as referências, mas no teste ${esc(m.caps.refs_weak)}. Para cena + produto + texto juntos, prefira um ★ Recomendado.</span></div>`;
  if (load) return '';
  if (!m || m.caps?.refs) return '';
  const frame = !!startFrameOf(n);
  const refs = refsOf(n).length > 0;
  const diagram = frame && n.direction?.diagram_url && n.direction.frame === startFrameOf(n);
  if (!refs && !diagram) return '';
  const lost = [refs && 'as referências', diagram && 'o desenho de câmera'].filter(Boolean).join(' e ');
  const tips = [
    diagram && 'a câmera segue só pela descrição do desenho no prompt',
    refs && (frame ? (composeOn(n) ? 'as referências entram no 1º frame antes do vídeo' : 'ligue "referências entram no 1º frame" para elas aparecerem') : 'as referências entram no 1º frame gerado'),
  ].filter(Boolean).join('; ');
  const rec = (state.models.video || []).filter((x) => x.caps.refs && !x.caps.refs_weak);
  const sug = rec.length ? ` Para mandar tudo junto, escolha um ★ Recomendado (ex.: ${esc(rec[0].label)}).` : ' Para mandar tudo junto, o admin pode instalar um modelo ★ Recomendado (várias imagens + texto).';
  return `<div class="cv-warn cv-norefs">${ic('alert')} <span><b>${esc(m.label)}</b> não recebe imagens de referência: ${lost} não vão para o vídeo. ${tips ? `${tips.charAt(0).toUpperCase()}${tips.slice(1)}.` : ''}${sug}</span></div>`;
}

// Imagem: avisa quando o nó manda mais referências do que o modelo aceita (as últimas ficam de fora).
function imageRefsWarning(m, count, withScene = false) {
  if (!m || !m.caps?.refs || !count) return '';
  const max = m.caps.max_refs || 4;
  if (count <= max) return '';
  const cut = count - max;
  return `<div class="cv-warn cv-norefs over">${ic('alert')} <span><b>${esc(m.label)}</b> aceita até <b>${max} referências</b> e ${withScene ? 'o 1º frame usaria' : 'este nó tem'} <b>${count}</b>${withScene ? ' (frame conectado + referências)' : ''}. As ${cut} última(s) não vão: remova ${cut} ou escolha um modelo que aceite mais.</span></div>`;
}

function planCost(plan, secs) {
  return plan.steps.reduce((t, s) => t + (s.m.cost || 0) * (s.m.cost_unit === 'second' ? secs : 1), 0);
}

function durationsFor(n) {
  const m = chosenModel('video', n.model, startFrameOf(n) ? { i2v: true } : {});
  return m?.caps?.durations?.length ? m.caps.durations : DEFAULT_DURATIONS;
}

// ---------------- Init / persistência ----------------

async function initCanvas() {
  bindBoard();
  initDirection();
  renderPalette();
  $('#cvFile').onchange = (e) => { uploadFiles([...e.target.files], centerWorld()); e.target.value = ''; };
  $('#cvRunAll').onclick = runAll;
  $('#cvFit').onclick = fitView;
  $('#cvClear').onclick = () => {
    if (!confirm('Limpar o canvas? (os arquivos continuam em Meus arquivos)')) return;
    cv.nodes.forEach((n) => animateRemove(n, () => {}));
    setTimeout(() => { cv.nodes = []; cv.edges = []; ropes.reset(); renderAll(); save(); }, 360);
  };

  await switchWorkspace(firstAllowedWs());
  renderLibrary();
  setInterval(renderLibrary, 6000);
}

// Carrega (uma vez) o canvas de uma área e retoma os renders que estavam rodando nela.
async function loadWorkspaceDoc(ws) {
  if (cv.docs[ws]) return cv.docs[ws];
  const doc = await api(`/api/canvas?ws=${ws}`);
  const d = { nodes: doc.nodes || [], edges: doc.edges || [], pan: doc.pan || { x: 60, y: 40 }, zoom: doc.zoom || 0.9, kit: doc.kit,
    title: doc.title || '', created: doc.created || 0, migratedGen: doc.migratedGen };
  cv.docs[ws] = d;
  for (const n of d.nodes.filter((x) => x.jobId && x.status !== 'done')) {
    const j = await api(`/api/jobs/${n.jobId}`).catch(() => null);
    if (!j) continue;
    if (j.status === 'done') Object.assign(n, { status: 'done', output: j.media, progress: 100, error: null });
    else if (['running', 'queued'].includes(j.status)) { n.status = 'running'; watchJob(n); }
  }
  return d;
}

// Nós de personagem/produto que ficaram no Video Studio (versão antiga) mudam para a área certa.
async function migrateGenNodesOutOfVideo(d) {
  const moved = { character: [], product: [] };
  for (const g of d.nodes.filter((x) => GEN_KIND[x.type])) {
    const kids = d.nodes.filter((x) => x.type === 'pose' && x.parentId === g.id);
    const sheets = d.nodes.filter((x) => x.type === 'sheet' && d.edges.some((e) => e.to === x.id && kids.some((k) => k.id === e.from)));
    const refs = d.nodes.filter((x) => x.type === 'media' && d.edges.some((e) => e.from === x.id && e.to === g.id));
    moved[GEN_KIND[g.type]].push(g, ...kids, ...sheets, ...refs);
  }
  const orphanSheets = d.nodes.filter((x) => x.type === 'sheet' && !Object.values(moved).flat().includes(x));
  moved.character.push(...orphanSheets);
  const all = Object.values(moved).flat();
  if (!all.length) return;
  for (const [ws, nodes] of Object.entries(moved)) {
    if (!nodes.length) continue;
    const target = await loadWorkspaceDoc(ws);
    const ids = new Set(nodes.map((x) => x.id));
    const dy = Math.max(0, ...target.nodes.map((x) => x.y + 900));
    nodes.forEach((x) => { x.y += dy; });
    target.nodes.push(...nodes);
    target.edges.push(...d.edges.filter((e) => ids.has(e.from) && ids.has(e.to)));
  }
  const gone = new Set(all.filter((x) => x.type !== 'media' || !d.edges.some((e) => e.from === x.id && !all.some((y) => y.id === e.to))).map((x) => x.id));
  d.nodes = d.nodes.filter((x) => !gone.has(x.id));
  d.edges = d.edges.filter((e) => !gone.has(e.from) && !gone.has(e.to));
  toast(`${[...gone].length} nó(s) de personagem/produto foram movidos para as abas Personagens/Produtos`);
  setTimeout(saveNow, 500);
}

// Abre a área ws na aba key (padrão: a última usada nela).
async function switchWorkspace(ws, key) {
  if (!cv.tabsLoaded) await loadTabs();
  key = key || lastTab(ws);
  if (!(cv.tabs[ws] || []).some((t) => t.key === key)) key = ws;
  if (cv.loaded && ws === cv.ws && key === cv.doc) return;
  if (cv.loaded) { stashWorkspace(); saveNow(); }
  const d = await loadWorkspaceDoc(key);
  if (key === 'video' && !d.migratedGen) { d.migratedGen = true; await migrateGenNodesOutOfVideo(d); }
  Object.assign(cv, { ws, doc: key, nodes: d.nodes, edges: d.edges, pan: d.pan, zoom: d.zoom, sel: null, loaded: true });
  rememberTab(ws, key);
  if (ws === 'video') migrateTransitions();
  if (ws !== 'video' && key === ws) {
    // itens da biblioteca que não estão em nenhuma aba da área entram na aba principal
    const others = [];
    for (const t of (cv.tabs[ws] || []).filter((x) => x.key !== key)) others.push(...(await loadWorkspaceDoc(t.key)).nodes);
    syncAssetNodes(ws, others);
  }
  ropes.reset();
  renderPalette();
  renderAll();
  renderAssetPanel();
  renderTabs();
  $('#cvEmpty').innerHTML = WS_EMPTY[ws];
}

function stashWorkspace() {
  cv.docs[cv.doc] = { ...(cv.docs[cv.doc] || {}), nodes: cv.nodes, edges: cv.edges, pan: cv.pan, zoom: cv.zoom };
}

// ---------------- Abas (tipo navegador) dentro de cada área ----------------

const tabStoreKey = (ws) => `studio.tab.${ws}`;
function lastTab(ws) { try { return localStorage.getItem(tabStoreKey(ws)) || ws; } catch { return ws; } }
function rememberTab(ws, key) { try { localStorage.setItem(tabStoreKey(ws), key); } catch { /* sem storage: tudo bem */ } }
const tabTitle = (t, i) => t.title || (i === 0 ? 'Principal' : `Aba ${i + 1}`);

async function loadTabs() {
  cv.tabs = await api('/api/canvas/tabs').catch(() => ({ video: [{ key: 'video', title: '' }], character: [{ key: 'character', title: '' }], product: [{ key: 'product', title: '' }] }));
  cv.tabsLoaded = true;
}

function renderTabs() {
  const box = $('#cvTabs');
  if (!box) return;
  const tabs = cv.tabs[cv.ws] || [{ key: cv.ws, title: '' }];
  box.innerHTML = tabs.map((t, i) => `<div class="cv-tab ${t.key === cv.doc ? 'active' : ''}" data-tab="${t.key}" title="${esc(tabTitle(t, i))} · duplo clique para renomear">
      <span class="cv-tab-name">${esc(tabTitle(t, i))}</span>${i ? `<button class="cv-tab-x" data-close="${t.key}" title="Fechar aba" aria-label="Fechar aba">${ic('x')}</button>` : ''}
    </div>`).join('') + `<button class="cv-tab-add" id="cvTabAdd" title="Nova aba" aria-label="Nova aba">${ic('plus')}</button>`;
  box.querySelectorAll('.cv-tab').forEach((el) => {
    el.addEventListener('click', (e) => { if (!e.target.closest('[data-close]')) switchWorkspace(cv.ws, el.dataset.tab).catch((err) => toast(err.message)); });
    el.addEventListener('dblclick', () => renameTab(el.dataset.tab));
    el.addEventListener('auxclick', (e) => { if (e.button === 1 && el.dataset.tab.includes('~')) closeTab(el.dataset.tab); });
  });
  box.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', (e) => { e.stopPropagation(); closeTab(b.dataset.close); }));
  $('#cvTabAdd').onclick = () => newTab();
  box.querySelector('.cv-tab.active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

async function newTab() {
  const ws = cv.ws;
  const key = `${ws}~${Math.random().toString(36).slice(2, 10).replace(/[^a-z0-9]/g, 'x').padEnd(6, '0')}`;
  const n = (cv.tabs[ws] || []).length + 1;
  const d = { nodes: [], edges: [], pan: { x: 60, y: 40 }, zoom: 0.9, title: `Aba ${n}`, created: Date.now() };
  await api(`/api/canvas?ws=${key}`, d);
  cv.docs[key] = d;
  (cv.tabs[ws] = cv.tabs[ws] || []).push({ key, title: d.title, created: d.created });
  await switchWorkspace(ws, key);
}

function renameTab(key) {
  const tabs = cv.tabs[cv.ws] || [];
  const i = tabs.findIndex((t) => t.key === key);
  if (i < 0) return;
  const name = prompt('Nome da aba', tabTitle(tabs[i], i));
  if (name == null) return;
  tabs[i].title = name.trim().slice(0, 60);
  if (key === cv.doc) stashWorkspace();
  loadWorkspaceDoc(key).then((d) => { d.title = tabs[i].title; saveNow(); renderTabs(); });
}

async function closeTab(key) {
  const ws = cv.ws;
  const tabs = cv.tabs[ws] || [];
  const i = tabs.findIndex((t) => t.key === key);
  if (i <= 0) return;
  const d = key === cv.doc ? { nodes: cv.nodes } : await loadWorkspaceDoc(key);
  if (d.nodes.length && !confirm(`Fechar a aba "${tabTitle(tabs[i], i)}"? Os ${d.nodes.length} nó(s) dela somem (os arquivos continuam no Inventário).`)) return;
  if (d.nodes.some((n) => n.status === 'running')) return toast('Espere os renders desta aba terminarem');
  clearTimeout(cv.saveT);
  if (key !== cv.doc) stashWorkspace();
  await api(`/api/canvas?ws=${key}`, undefined, 'DELETE');
  tabs.splice(i, 1);
  delete cv.docs[key];
  if (key === cv.doc) {
    cv.loaded = false;
    await switchWorkspace(ws, tabs[Math.max(0, i - 1)].key);
    cv.loaded = true;
  } else renderTabs();
}

// Salva todas as áreas abertas (renders de outra área podem terminar enquanto você está nesta).
function saveNow() {
  clearTimeout(cv.saveT);
  stashWorkspace();
  for (const [key, d] of Object.entries(cv.docs)) {
    const body = { nodes: d.nodes, edges: d.edges, pan: d.pan, zoom: d.zoom, title: d.title || '', created: d.created || 0, migratedGen: d.migratedGen };
    api(`/api/canvas?ws=${key}`, body).catch(() => {});
  }
}

function save() {
  clearTimeout(cv.saveT);
  cv.saveT = setTimeout(saveNow, 600);
}

window.addEventListener('beforeunload', () => { if (cv.saveT) saveNow(); });



// Nós disponíveis na paleta: o que fazem e como usar.
const PALETTE = {
  brief: {
    icon: 'wand', title: 'Roteiro', tag: 'brief → planos',
    what: 'Escreve a decupagem do comercial a partir do brief e cria os planos já ligados no canvas.',
    how: 'Escreva o brief → Planejar → revise os planos → Criar planos no canvas. Ligue nós de Elenco/Produto na entrada para manter personagens e produto iguais.',
    make: (pos) => newBrief(pos),
  },
  cast: {
    icon: 'users', title: 'Elenco / produto', tag: 'consistência',
    what: 'Um personagem, produto ou logo com fotos e descrição fixa, para aparecer igual em todos os planos.',
    how: 'Dê um nome, descreva e envie fotos → ligue a saída na porta R dos geradores (ou no Roteiro). A IA cita o nome e usa as fotos como referência.',
    make: (pos) => newCast(pos),
  },
  chargen: {
    icon: 'user', title: 'Personagem', tag: 'ficha + 8 posições',
    what: 'A ficha do personagem (nome, descrição, atributos) e a geração de todas as posições, um nó por posição.',
    how: 'Preencha a ficha → solte fotos no canvas (camisa, logo, rosto), dê nome a cada uma e ligue na porta R → Gerar todas as posições. A frente vira o master e vai, com TODAS as referências, para cada posição. No Video Studio, o nó Elenco usa este personagem.',
    make: (pos) => newCharGen(pos, 'character'),
  },
  prodgen: {
    icon: 'logo', title: 'Produto', tag: 'ficha + 8 vistas',
    what: 'A ficha do produto e a geração de todas as vistas (frente, laterais, traseira, topo, detalhe), um nó por vista.',
    how: 'Preencha a ficha → solte fotos do produto, rótulo e logo no canvas e ligue na porta R → Gerar todas as posições. Cada vista recebe o master + todas as referências.',
    make: (pos) => newCharGen(pos, 'product'),
  },
  save: {
    icon: 'check-circle', title: 'Salvar na biblioteca', tag: 'publicar item',
    what: 'Escolhe quais posições/imagens valem e salva o personagem/produto na biblioteca. Só os salvos aparecem no painel e no Video Studio.',
    how: 'Ligue as posições (e a spritesheet) aqui → marque as imagens que ficam e a capa → Salvar. O Gerar todas as posições já cria e liga este nó.',
    make: (pos) => newSave(pos),
  },
  sheet: {
    icon: 'dashboard', title: 'Spritesheet', tag: 'juntar posições',
    what: 'Junta as posições numa única imagem em grade (PNG) + mapa dos quadros (JSON).',
    how: 'Ligue as posições aqui (o Gerar personagem já liga sozinho) → escolha colunas e fundo → Montar spritesheet.',
    make: (pos) => newSheet(pos),
  },
  video: {
    icon: 'video', title: 'Gerar vídeo', tag: 'texto ou imagem → vídeo',
    what: 'Cria um vídeo a partir de um texto em português. O prompt técnico é escrito pela IA por trás.',
    how: 'Escreva o que acontece → clique no quadro de câmera para desenhar o movimento → Gerar. '
      + 'Ligando uma imagem na entrada, o vídeo começa nela; ligando um vídeo, você escolhe de qual frame '
      + 'ele continua (padrão: o último).',
    make: (pos) => newShot(pos),
  },
  image: {
    icon: 'image', title: 'Gerar imagem', tag: 'texto + referências',
    what: 'Cria um still fotográfico a partir de um texto, usando logo/produto de referência quando o modelo aceita.',
    how: 'Escreva a cena → Gerar imagem. Ligue a saída num Gerar vídeo (vira o 1º frame) ou na entrada A/B de uma Transição.',
    make: (pos) => newImageGen(pos),
  },
  transition: {
    icon: 'transition', title: 'Transição', tag: 'A → B com IA',
    what: 'Gera um vídeo que sai do frame de A e chega no frame de B, com movimento de câmera pensado pela IA.',
    how: 'Ligue um vídeo/imagem na entrada A e outro na B → (opcional) descreva a ideia → Gerar transição. '
      + 'A saída continua num novo vídeo ou vai para a Montagem.',
    make: (pos) => newTransition(pos),
  },
  frame: {
    icon: 'frame', title: 'Extrair frame', tag: 'vídeo → imagem',
    what: 'Tira uma imagem de qualquer frame de um vídeo.',
    how: 'Ligue um vídeo na entrada → escolha o frame na régua (±1 frame) → Extrair. '
      + 'A saída é uma imagem: 1º frame de um novo vídeo, referência (R) ou entrada A/B de uma transição.',
    make: (pos) => newFrameNode(pos),
  },
  edit: {
    icon: 'sparkles', title: 'Expandir / editar região', tag: 'quadro maior ou região trocada',
    what: 'Expande a imagem para outro aspecto (a IA preenche as bordas) ou troca uma região pintada, mantendo o resto igual.',
    how: 'Ligue a imagem na entrada. Em Expandir, escolha o aspecto e a posição da imagem. Em Editar região, pinte a área com o pincel e descreva o que colocar.',
    make: (pos) => newEdit(pos),
  },
  relight: {
    icon: 'sparkles', title: 'Relight / Estilo', tag: 'luz ou estilo de outra imagem',
    what: 'Muda a luz de uma imagem (dourada, estúdio, neon, noite, contraluz) ou aplica o estilo de uma segunda imagem. A composição e as pessoas ficam como estão.',
    how: 'Ligue a imagem na entrada. No modo Relight, escolha o preset, desenhe a direção da luz e a intensidade. No modo Estilo, ligue a imagem de estilo na entrada S e ajuste a força.',
    make: (pos) => newRelight(pos),
  },
  upscale: {
    icon: 'zoom-in', title: 'Upscale criativo', tag: 'imagem → maior',
    what: 'Amplia uma imagem 2x ou 4x. Com um modelo de imagem que aceite referências, a IA acrescenta detalhe (criatividade) mantendo a cena (semelhança). Sem esse modelo, é um upscale simples.',
    how: 'Ligue uma imagem na entrada → escolha o fator, a criatividade e a semelhança → Ampliar. A saída pode seguir para um vídeo ou outro nó.',
    make: (pos) => newUpscale(pos),
  },
  review: {
    icon: 'eye', title: 'Aprovação', tag: 'pausa o fluxo',
    what: 'Mostra grande a saída de um nó e segura o fluxo até alguém aprovar.',
    how: 'Ligue a saída de qualquer nó aqui e a saída desta aprovação no próximo passo. Rodar fluxo para '
      + 'nela. Aprovar continua; Reprovar manda o seu comentário para a IA e gera de novo o nó anterior.',
    make: (pos) => newReviewNode(pos),
  },
  montage: {
    icon: 'film', title: 'Montagem', tag: 'corte final',
    what: 'Junta vídeos e transições na ordem das ligações, com cortes e trilha/locução, num MP4 final.',
    how: 'Ligue a saída dos vídeos/transições aqui → Montar. Mostra a sequência que vai sair.',
    make: (pos) => ({ id: uid('mont'), type: 'montage', name: t('Corte final'), ...pos }),
  },
};

// Permissão de node (grupos): tipo da paleta de cada nó do canvas.
const paletteKeyOf = (n) => (n.type === 'shot' ? (n.gen === 'image' ? 'image' : 'video')
  : n.type === 'pose' ? (cv.ws === 'product' ? 'prodgen' : 'chargen') : n.type === 'media' ? null : n.type);
const nodeAllowed = (key) => !key || state.me?.permissions?.[`node_${key}`] !== false;

function renderPalette() {
  $('#cvPalette').innerHTML = Object.entries(PALETTE).filter(([k]) => WS_PALETTE[cv.ws].includes(k) && nodeAllowed(k)).map(([k, p]) => `
    <div class="cv-pal" draggable="true" data-node="${k}">
      <div class="cv-pal-head"><span class="cv-pal-icon">${ic(p.icon)}</span><b>${esc(p.title)}</b><small>${esc(p.tag)}</small></div>
      <p>${esc(p.what)}</p>
      <details><summary>Como usar</summary><p>${esc(p.how)}</p></details>
    </div>`).join('') + `
    <label class="cv-pal" title="Enviar fotos ou vídeos do computador">
      <div class="cv-pal-head"><span class="cv-pal-icon">${ic('upload')}</span><b>Importar mídia</b><small>foto · vídeo</small></div>
      <p>Traz fotos do produto, vídeos prontos ou áudio (trilha/locução). Vídeo importado serve de entrada: o último frame continua no próximo nó.</p>
      <input type="file" accept="image/*,video/*,audio/*" multiple hidden data-role="pal-file">
    </label>`;
  document.querySelectorAll('.cv-pal[data-node]').forEach((el) => {
    el.addEventListener('click', (e) => { if (!e.target.closest('details')) revealNode(addNode(PALETTE[el.dataset.node].make(freeSpot(centerWorld())))); });
    el.addEventListener('dragstart', (e) => e.dataTransfer.setData(DRAG_NODE, el.dataset.node));
  });
  $('#cvPalette [data-role=pal-file]').onchange = (e) => { uploadFiles([...e.target.files], centerWorld()); e.target.value = ''; };
}

function newImageGen(pos) {
  return { ...newShot(pos), id: uid('img'), gen: 'image', name: t('Nova imagem') };
}

function newShot(pos) {
  return { id: uid('shot'), type: 'shot', name: t('Novo vídeo'), intent: '', model: 'auto', imageModel: 'auto', startMode: 'auto',
    duration: 5, aspect: '16:9', quality: 'draft', lens: '35', path: { easing: 'ease', points: [] }, auto: null, autoKey: null, status: 'idle', ...pos };
}

function addNode(n) {
  markSpawn(n.id);
  cv.nodes.push(n);
  renderAll();
  select(n.id);
  save();
  return n;
}

// ---------------- Grafo ----------------

const incoming = (id) => cv.edges.filter((e) => e.to === id && e.port !== 'ref' && e.port !== 'style').map((e) => byId(e.from)).filter(Boolean);

// Referências de um gerador: as enviadas no próprio card + o que estiver ligado na porta R.
function refsOf(n) {
  const linked = cv.edges.filter((e) => e.to === n.id && e.port === 'ref').map((e) => byId(e.from))
    .flatMap((s) => (s?.type === 'cast' ? castImages(s, true) : [s]))
    .map((s) => (typeof s === 'string' ? s : s && (s.type === 'review' ? frameOf(s) : s.type === 'frame' ? extractedFrame(s) : s.type === 'media' ? (s.media.kind === 'video' ? s.media.last_frame : s.media.url)
      : s.output ? (s.output.kind === 'image' ? s.output.url : s.output.last_frame) : null)))
    .filter(Boolean);
  return [...new Set([...(n.refs || []).map((r) => r.url), ...linked])];
}
const inputOn = (id, port) => byId(cv.edges.find((e) => e.to === id && e.port === port)?.from);

function frameOf(n) {
  if (!n) return null;
  if (n.type === 'transition') return n.output?.last_frame || null;
  if (n.type === 'frame') return extractedFrame(n);
  if (['upscale', 'relight', 'edit'].includes(n.type)) return n.output?.url || null;
  if (n.type === 'review') return frameOf(incoming(n.id)[0]);
  if (n.type === 'media') return n.media.kind === 'video' ? n.media.last_frame : n.media.url;
  if (n.type === 'shot') return n.output?.last_frame || (n.output?.kind === 'image' ? n.output.url : null);
  if (['pose', 'sheet'].includes(n.type)) return n.output?.url || null;
  if (GEN_KIND[n.type]) return masterOf(n)?.output?.url || null;
  return null;
}

function videoOf(n) {
  if (n?.type === 'review') return videoOf(incoming(n.id)[0]);
  if (n?.type === 'media' && n.media.kind === 'video') return n.media.url;
  if (n?.type === 'shot' || n?.type === 'transition') return n.output?.kind === 'video' ? n.output.url : null;
  return null;
}

function topoShots() {
  const order = [];
  const seen = new Set();
  const visit = (n) => {
    if (!n || seen.has(n.id)) return;
    seen.add(n.id);
    incoming(n.id).forEach(visit);
    if (n.type === 'shot' || (n.type === 'transition' && trMode(n) === 'ai')) order.push(n);
  };
  cv.nodes.filter((n) => n.type === 'shot' || n.type === 'transition').sort((a, b) => a.x - b.x).forEach(visit);
  return order;
}

// ---------------- Board: pan, zoom, drop ----------------

function applyView() {
  $('#cvWorld').style.transform = `translate(${cv.pan.x}px, ${cv.pan.y}px) scale(${cv.zoom})`;
}

function toWorld(clientX, clientY) {
  const r = $('#cvBoard').getBoundingClientRect();
  return { x: (clientX - r.left - cv.pan.x) / cv.zoom, y: (clientY - r.top - cv.pan.y) / cv.zoom };
}

// Posição livre perto de p: desce em cascata enquanto houver um nó ali (senão os nós novos se empilham).
function freeSpot(p) {
  const busy = (q) => cv.nodes.some((n) => Math.abs(n.x - q.x) < 380 && Math.abs(n.y - q.y) < 300);
  for (let row = 0; row < 12; row++) {
    for (let col = 0; col < 4; col++) {
      const q = { x: Math.round(p.x + col * 400), y: Math.round(p.y + row * 640) };
      if (!busy(q)) return q;
    }
  }
  return p;
}

// Nó criado fora da área visível: a visão vai até ele.
function revealNode(n) {
  const r = $('#cvBoard').getBoundingClientRect();
  const sx = n.x * cv.zoom + cv.pan.x, sy = n.y * cv.zoom + cv.pan.y;
  if (sx > 0 && sx + 300 * cv.zoom < r.width && sy > 0 && sy + 200 * cv.zoom < r.height) return;
  cv.pan = { x: r.width / 2 - (n.x + 180) * cv.zoom, y: Math.max(40, r.height / 3 - n.y * cv.zoom) };
  applyView();
  save();
}

function centerWorld() {
  const r = $('#cvBoard').getBoundingClientRect();
  const p = toWorld(r.left + r.width / 2 - 150, r.top + r.height / 2 - 150);
  return { x: Math.round(p.x + (Math.random() * 40 - 20)), y: Math.round(p.y) };
}

function fitView() {
  if (!cv.nodes.length) return;
  const r = $('#cvBoard').getBoundingClientRect();
  const xs = cv.nodes.map((n) => n.x), ys = cv.nodes.map((n) => n.y);
  const minX = Math.min(...xs) - 40, minY = Math.min(...ys) - 40;
  const w = Math.max(...xs) + 360 - minX, h = Math.max(...ys) + 520 - minY;
  cv.zoom = Math.max(0.3, Math.min(1.2, Math.min(r.width / w, r.height / h)));
  cv.pan = { x: -minX * cv.zoom + 20, y: -minY * cv.zoom + 20 };
  applyView();
  save();
}

function bindBoard() {
  const board = $('#cvBoard');
  board.addEventListener('wheel', (e) => {
    e.preventDefault();
    const r = board.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const k = Math.exp(-e.deltaY * 0.0012);
    const z = Math.max(0.25, Math.min(2, cv.zoom * k));
    cv.pan.x = mx - (mx - cv.pan.x) * (z / cv.zoom);
    cv.pan.y = my - (my - cv.pan.y) * (z / cv.zoom);
    cv.zoom = z;
    applyView();
    save();
  }, { passive: false });

  board.addEventListener('mousedown', (e) => {
    if (e.target !== board && e.target.id !== 'cvWorld' && e.target.id !== 'cvEdges') return;
    select(null);
    e.preventDefault(); // sem isso o navegador começa a selecionar texto e o próximo arrasto "pega" a seleção como item
    window.getSelection()?.removeAllRanges();
    const start = { x: e.clientX, y: e.clientY, px: cv.pan.x, py: cv.pan.y };
    board.classList.add('panning');
    const move = (ev) => { cv.pan.x = start.px + ev.clientX - start.x; cv.pan.y = start.py + ev.clientY - start.y; applyView(); };
    const up = () => { board.classList.remove('panning'); window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); save(); };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  });

  // Arrasto nativo só para o que é nosso (paleta, arquivos, itens salvos) ou arquivos do computador.
  // Imagem, vídeo ou texto selecionado dentro do canvas não viram "item arrastado".
  const ours = (e) => [...(e.dataTransfer?.types || [])].some((t) => [DRAG_MEDIA, DRAG_ASSET, DRAG_NODE, 'Files'].includes(t));
  board.addEventListener('dragstart', (e) => { if (!e.target.closest?.('[draggable="true"]')) e.preventDefault(); });
  board.addEventListener('dragover', (e) => { if (!ours(e)) return; e.preventDefault(); board.classList.add('dragover'); });
  board.addEventListener('dragend', () => board.classList.remove('dragover'));
  board.addEventListener('dragleave', (e) => { if (e.target === board) board.classList.remove('dragover'); });
  board.addEventListener('drop', async (e) => {
    board.classList.remove('dragover');
    if (!ours(e)) return;
    e.preventDefault();
    const at = toWorld(e.clientX, e.clientY);
    const lib = e.dataTransfer.getData(DRAG_MEDIA);
    if (lib) return addMediaNode(JSON.parse(lib), at);
    const assetId = e.dataTransfer.getData(DRAG_ASSET);
    if (assetId) return dropAsset(assetId, at);
    const kind = e.dataTransfer.getData(DRAG_NODE);
    if (kind && PALETTE[kind] && !nodeAllowed(kind)) return toast('Seu grupo não tem permissão para este node');
    if (kind && PALETTE[kind]) return addNode(PALETTE[kind].make({ x: Math.round(at.x), y: Math.round(at.y) }));
    if (e.dataTransfer.files.length) uploadFiles([...e.dataTransfer.files], at);
  });

  window.addEventListener('keydown', (e) => {
    if (!document.body.classList.contains('on-canvas') || !cv.sel) return;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;
    if (e.key === 'Delete' || e.key === 'Backspace') removeNode(cv.sel);
  });
  applyView();
}

function announceUpload(msg) {
  const el = $('#uploadStatus');
  if (el) el.textContent = msg;
}

async function uploadFiles(files, at) {
  const btn = document.querySelector('.cv-upload');
  btn?.setAttribute('aria-busy', 'true');
  try {
    await uploadEach(files, at);
  } finally {
    btn?.removeAttribute('aria-busy');
  }
}

async function uploadEach(files, at) {
  for (const [i, f] of files.entries()) {
    toast(`Enviando ${f.name}…`);
    announceUpload(`Enviando ${f.name}`);
    try {
      const m = await uploadFile(f);
      announceUpload(`${f.name} enviado`);
      renderLibrary._sig = null;
      renderLibrary();
      if (m.kind === 'audio') { toast(`${f.name} em Meus arquivos: arraste para uma Montagem`); continue; }
      addMediaNode({ ...m, name: f.name }, { x: at.x + i * 30, y: at.y + i * 30 });
    } catch (err) { toast(`Erro no upload: ${err.message}`); announceUpload(`Erro no upload de ${f.name}: ${err.message}`); }
  }
}

async function addMediaNode(m, at) {
  if (m.kind === 'audio') return toast('Áudio vai na Montagem: solte-o na caixa "Trilha / locução"');
  if (m.kind === 'video' && !m.last_frame) m = { ...m, ...(await api('/api/media/describe', { url: m.url })) };
  addNode({ id: uid('media'), type: 'media', x: Math.round(at.x), y: Math.round(at.y), name: m.name || (m.kind === 'video' ? 'Vídeo' : 'Imagem'), media: m });
}

// "Meus arquivos": uploads, renders e cortes do usuário (só ele vê).
async function renderLibrary() {
  let data;
  try { data = await api('/api/files'); } catch { return; }
  const items = data.files.filter((f) => ['image', 'video', 'audio'].includes(f.kind) && !f.asset).map((f) => ({
    ...f, thumb: f.kind === 'video' || /\.gif$/i.test(f.url) ? f.url.replace(/\.[^.]+$/, '_first.png') : f.kind === 'image' ? f.url : null,
    last_frame: f.kind === 'video' ? f.url.replace(/\.[^.]+$/, '_last.png') : undefined,
  }));
  state.files = items;
  $('#cvStorage').textContent = `${items.length} arquivo(s) · ${fmtBytes(data.used)}${data.limit_mb != null ? ` de ${data.limit_mb} MB` : ''}. Arraste para o canvas.`;
  const sig = items.map((i) => i.url).join('|');
  if (sig === renderLibrary._sig) return;
  renderLibrary._sig = sig;
  $('#cvLibrary').innerHTML = items.length ? items.map((m, i) =>
    `<div class="cv-lib-item ${m.kind}" draggable="true" data-i="${i}" title="${esc(m.name)} · ${fmtBytes(m.size)}" ${m.thumb ? `data-st="background-image:url('${m.thumb}')"` : ''}>${m.kind === 'audio' ? ic('music') : ''}<span>${m.kind === 'video' ? ic('play') : m.kind === 'audio' ? 'áudio' : ic('image')}</span><button class="del" data-del="${i}" title="Apagar arquivo" aria-label="Apagar arquivo">${ic('trash')}</button></div>`).join('')
    : '<p class="cv-side-note">Nada ainda. Envie arquivos ou gere imagens/vídeos.</p>';
  document.querySelectorAll('.cv-lib-item').forEach((el) => {
    el.addEventListener('dragstart', (e) => e.dataTransfer.setData(DRAG_MEDIA, JSON.stringify(items[+el.dataset.i])));
  });
  document.querySelectorAll('.cv-lib-item [data-del]').forEach((b) => b.addEventListener('click', async (e) => {
    e.stopPropagation();
    const f = items[+b.dataset.del];
    const inUse = cv.nodes.some((n) => JSON.stringify(n).includes(f.url));
    if (!confirm(`Apagar "${f.name}"?${inUse ? '\nEle está sendo usado no canvas.' : ''}`)) return;
    await api('/api/files/delete', { urls: [f.url] }).then(() => toast('Arquivo apagado')).catch((err) => toast(err.message));
    renderLibrary._sig = null;
    renderLibrary();
    loadMe().catch(() => {});
  }));
}

// ---------------- Render de nós ----------------

function renderAll() {
  const world = $('#cvWorld');
  world.querySelectorAll('.cv-node').forEach((el) => el.remove());
  cv.nodes.forEach((n) => world.appendChild(nodeEl(n)));
  drawEdges();
  applyView();
  $('#cvEmpty').hidden = cv.nodes.length > 0;
}

function renderNode(n) {
  if (!cv.nodes.includes(n)) return; // nó de outra área (render terminou lá): só o dado muda
  const old = document.querySelector(`.cv-node[data-id="${n.id}"]`);
  const el = nodeEl(n);
  if (old) old.replaceWith(el); else $('#cvWorld').appendChild(el);
  drawEdges();
}

function select(id) {
  cv.sel = id;
  document.querySelectorAll('.cv-node').forEach((el) => el.classList.toggle('selected', el.dataset.id === id));
}

function removeNode(id) {
  const n = byId(id);
  if (!n) return;
  if (GEN_KIND[n.type] && n.assetId) {
    const a = genAsset(n);
    if (!confirm(`Remover "${n.name}" do canvas?`)) return;
    if (a && confirm(`Excluir também "${a.name}" da biblioteca? (OK = excluir; Cancelar = manter para usar no Video Studio)`)) {
      api(`/api/assets/${a.id}`, undefined, 'DELETE').then(loadAssets).catch(() => {});
    }
  }
  animateRemove(n, () => removeNodeNow(id));
}

function removeNodeNow(id) {
  cv.nodes = cv.nodes.filter((n) => n.id !== id);
  cv.edges = cv.edges.filter((e) => e.from !== id && e.to !== id);
  cv.sel = null;
  renderAll();
  save();
}

const KIND = {
  image: { label: 'Imagem', icon: 'image' }, video: { label: 'Vídeo', icon: 'video' }, audio: { label: 'Áudio', icon: 'music' },
  shot: { label: 'Gerar vídeo', icon: 'video' }, imagegen: { label: 'Gerar imagem', icon: 'image' }, montage: { label: 'Montagem', icon: 'film' },
  transition: { label: 'Transição', icon: 'transition' }, frame: { label: 'Extrair frame', icon: 'frame' }, review: { label: 'Aprovação', icon: 'eye' },
  brief: { label: 'Roteiro', icon: 'wand' }, cast: { label: 'Elenco / produto', icon: 'users' },
  chargen: { label: 'Personagem', icon: 'user' }, prodgen: { label: 'Produto', icon: 'logo' },
  pose: { label: 'Posição', icon: 'frame' }, sheet: { label: 'Spritesheet', icon: 'dashboard' },
  save: { label: 'Salvar', icon: 'check-circle' },
  upscale: { label: 'Upscale', icon: 'zoom-in' },
  relight: { label: 'Relight / Estilo', icon: 'sparkles' },
  edit: { label: 'Expandir / editar', icon: 'sparkles' },
};

function nodeEl(n) {
  const el = document.createElement('div');
  el.className = `cv-node ${n.type}${cv.sel === n.id ? ' selected' : ''}`;
  el.dataset.id = n.id;
  el.style.left = `${n.x}px`;
  el.style.top = `${n.y}px`;
  const kind = n.type === 'media' ? n.media.kind : n.type === 'shot' ? (n.gen === 'image' ? 'imagegen' : 'shot') : n.type;
  el.dataset.kind = kind;
  if (n.type === 'shot') el.classList.add('gen');
  if (!nodeAllowed(paletteKeyOf(n))) { el.classList.add('perm-locked'); el.title = 'Seu grupo não tem permissão para este node: ele fica só para visualização'; }
  const k = KIND[kind];
  const canRegen = ['shot', 'transition', 'pose'].includes(n.type) && n.output && n.status !== 'running';
  el.innerHTML = `
    <div class="cv-head">
      <span class="cv-kicon">${ic(k.icon)}</span>
      <div class="cv-htext"><small>${k.label}</small><span class="title" spellcheck="false" title="Duplo clique para renomear">${esc(n.name)}</span></div>
      <span class="cv-dot ${n.status || 'idle'}" title="${esc(n.status || 'pronto para gerar')}"></span>
      ${canRegen ? `<button class="cv-hbtn" data-act="regen" title="Regerar (nova variação)" aria-label="Regerar (nova variação)">${ic('refresh')}</button>` : ''}
      ${n.type !== 'media' ? `<button class="cv-hbtn" data-act="dup" title="Duplicar nó" aria-label="Duplicar nó">${ic('copy')}</button>` : ''}
      <button class="cv-hbtn x" title="Remover nó" aria-label="Remover nó">${ic('x')}</button>
    </div>
    <div class="cv-content">${{ media: mediaBody, shot: (x) => (x.gen === 'image' ? imageGenBody(x) : shotBody(x)), montage: montageBody, transition: transitionBody, frame: frameNodeBody, upscale: upscaleBody, relight: relightBody, edit: editBody, review: reviewBody, brief: briefBody, cast: castBody, chargen: charGenBody, prodgen: charGenBody, pose: poseBody, sheet: sheetBody, save: saveBody }[n.type](n)}</div>
    ${n.type === 'transition'
      ? '<span class="cv-port in" data-port="a" title="Entrada A: vídeo ou imagem de onde a transição sai"><i>A</i></span><span class="cv-port in port-b" data-port="b" title="Entrada B: vídeo ou imagem onde a transição chega"><i>B</i></span>'
      : n.type === 'relight'
        ? '<span class="cv-port in" title="Entrada: imagem a tratar"></span>'
          + '<span class="cv-port in port-b" data-port="style" title="S: imagem de estilo (modo Estilo)"><i>S</i></span>'
      : n.type === 'shot'
        ? `${n.gen !== 'image' ? '<span class="cv-port in" title="Entrada: imagem ou vídeo (vira o 1º frame)"></span>' : ''}<span class="cv-port in port-ref" data-port="ref" title="R: imagens de referência (logo, produto, estilo)"><i>R</i></span>`
        : n.type === 'brief' ? '<span class="cv-port in" title="Entrada: nós de Elenco/Produto"></span>'
          : GEN_KIND[n.type] ? '<span class="cv-port in port-ref" data-port="ref" title="R: referências extras (fotos, Elenco)"><i>R</i></span>'
          : !['media', 'cast'].includes(n.type) ? '<span class="cv-port in" title="Entrada"></span>' : ''}
    ${!['montage', 'brief', 'save'].includes(n.type) ? `<span class="cv-port out" title="${n.type === 'cast' ? 'Saída: ligue na porta R dos geradores ou no Roteiro' : 'Saída: arraste até outro nó'}"></span>` : ''}`;

  el.addEventListener('mousedown', () => select(n.id));
  el.querySelector('.x').onclick = () => removeNode(n.id);
  el.querySelector('[data-act=dup]')?.addEventListener('click', () => duplicateNode(n));
  el.querySelector('.cv-head [data-act=regen]')?.addEventListener('click', () => regenerate(n));
  const title = el.querySelector('.title');
  title.addEventListener('dblclick', () => { title.contentEditable = 'true'; title.focus(); });
  title.addEventListener('blur', () => { title.contentEditable = 'false'; });
  title.addEventListener('input', () => { n.name = title.textContent.trim(); save(); });
  title.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); title.blur(); } });
  bindDrag(el, n);
  el.querySelector('.cv-port.out')?.addEventListener('mousedown', (e) => startLink(e, n));
  if (n.type === 'shot') bindShot(el, n);
  if (n.type === 'montage') bindMontage(el, n);
  if (n.type === 'transition') bindTransition(el, n);
  if (n.type === 'frame') bindFrameNode(el, n);
  if (n.type === 'upscale') bindUpscale(el, n);
  if (n.type === 'relight') bindRelight(el, n);
  if (n.type === 'edit') bindEdit(el, n);
  if (n.type === 'review') bindReview(el, n);
  if (n.type === 'brief') bindBrief(el, n);
  if (n.type === 'cast') bindCast(el, n);
  if (GEN_KIND[n.type]) bindCharGen(el, n);
  if (n.type === 'pose') bindPose(el, n);
  if (n.type === 'sheet') bindSheet(el, n);
  if (n.type === 'save') bindSave(el, n);
  bindPreview(el, n);
  applySpawn(el, n);
  return el;
}

// ---------------- Relight / Estilo ----------------

function newRelight(pos) {
  return { id: uid('rl'), type: 'relight', name: t('Relight'), op: 'relight', preset: 'studio', intensity: 60, strength: 60,
    prompt: '', lightDir: null, model: 'auto', status: 'idle', output: null, takes: [], jobId: null, opMode: null, ...pos };
}

// Entrada principal = imagem a tratar; entrada S = imagem de estilo (modo Estilo).
function connectRelight(from, to, port) {
  if (port === 'style') cv.edges = cv.edges.filter((e) => !(e.to === to.id && e.port === 'style'));
  else cv.edges = cv.edges.filter((e) => !(e.to === to.id && e.port !== 'style' && e.port !== 'ref'));
  cv.edges.push({ id: uid('e'), from: from.id, to: to.id, ...(port === 'style' ? { port: 'style' } : {}) });
  invalidate(to);
  renderNode(to);
  save();
  toast(port === 'style' ? `${from.name} é a imagem de estilo (entrada S)` : `${from.name} é a imagem a tratar`);
}

const styleSourceOf = (n) => frameOf(byId(cv.edges.find((e) => e.to === n.id && e.port === 'style')?.from));

const relightModeNote = (n) => (n.opMode === 'local'
  ? 'Sem modelo com referências: ajuste simples (Pillow). A IA não foi usada.'
  : n.opMode === 'ai' ? 'Relight com IA: a composição e as pessoas são preservadas.'
    : 'Com um modelo de imagem que aceite referências a IA faz o trabalho; sem ele, o ajuste é simples (Pillow).');

function relightBody(n) {
  const src = upscaleSource(n);
  const style = n.op === 'style';
  const styleSrc = style ? styleSourceOf(n) : null;
  const done = !!n.output;
  const presets = [['golden', 'Luz dourada'], ['studio', 'Estúdio'], ['neon', 'Neon'], ['night', 'Noite'], ['backlight', 'Contraluz']];
  const ops = `${opt('relight', n.op, 'Relight (luz)')}${opt('style', n.op, 'Estilo (de outra imagem)')}`;
  const body = style
    ? `<div class="cv-meta"><span>${styleSrc ? 'Imagem de estilo ligada na entrada S.' : '<span class="err">Ligue a imagem de estilo na entrada S</span>'}</span></div>
      ${sliderField('strength', 'Força', n.strength)}`
    : `${field('Preset de luz', `<select data-k="preset">${presets.map(([k, v]) => opt(k, n.preset, v)).join('')}</select>`)}
      ${sliderField('intensity', 'Intensidade', n.intensity)}
      <div class="cv-meta"><span>${n.lightDir ? 'Direção da luz desenhada.' : 'Sem direção desenhada: a luz segue o preset.'}</span></div>
      <button class="btn small ghost" data-act="light" ${src ? '' : 'disabled'}>${ic('route')} Desenhar direção da luz</button>`;
  return `
    ${previewBlock(n, { placeholder: { icon: 'sparkles', text: src ? '' : 'Ligue uma imagem na entrada' }, ghost: src })}
    ${src ? '' : `<div class="cv-meta"><span class="err">${ic('alert')} Ligue uma imagem na entrada</span></div>`}
    ${field('Modo', `<select data-k="op">${ops}</select>`)}
    ${body}
    ${field('Nota (opcional)', `<textarea data-k="prompt" rows="2" maxlength="1000" placeholder="Ex.: a luz bate na garrafa de lado">${esc(n.prompt)}</textarea>`)}
    <div class="cv-meta"><span>${esc(relightModeNote(n))}</span></div>
    ${n.status === 'error' ? `<div class="cv-error">${ic('x-circle')}<span>${esc(n.error || 'erro')}</span></div>` : ''}
    <div class="cv-foot">
      <button class="btn primary" data-act="relight" ${n.status === 'running' || !src ? 'disabled' : ''}>${ic('sparkles')} ${done ? 'Aplicar de novo' : (style ? 'Aplicar estilo' : 'Aplicar luz')}</button>
    </div>`;
}

function bindRelight(el, n) {
  el.querySelectorAll('[data-k]').forEach((inp) => {
    const range = inp.type === 'range';
    if (inp.dataset.k === 'op') {
      inp.addEventListener('change', () => { n.op = inp.value; save(); renderNode(n); });
      return;
    }
    const ev = range || inp.tagName === 'TEXTAREA' ? 'input' : 'change';
    inp.addEventListener(ev, () => {
      const k = inp.dataset.k;
      n[k] = range ? +inp.value : inp.value;
      if (range) {
        el.querySelector(`output[data-out="${k}"]`).textContent = inp.value;
        inp.setAttribute('aria-valuetext', inp.value);
      }
      save();
    });
  });
  el.querySelector('[data-act=light]')?.addEventListener('click', () => openLight(n));
  el.querySelector('[data-act=relight]')?.addEventListener('click', () => runRelight(n).catch((err) => toast(err.message)));
}

// Relight ou estilo: entra na fila de jobs. Sem modelo com referências, o ajuste local avisa na UI.
async function runRelight(n) {
  const src = upscaleSource(n);
  if (!src) return toast('Ligue uma imagem na entrada');
  if (n.status === 'running') return;
  let job;
  try {
    if (n.op === 'style') {
      const style = styleSourceOf(n);
      if (!style) return toast('Ligue a imagem de estilo na entrada S');
      job = await api('/api/image/style-transfer', { node_id: n.id, name: n.name, source_url: src, style_url: style,
        strength: +n.strength, prompt: n.prompt || '', model: n.model || 'auto' });
    } else {
      job = await api('/api/image/relight', { node_id: n.id, name: n.name, source_url: src, preset: n.preset,
        intensity: +n.intensity, light: n.lightDir || null, model: n.model || 'auto' });
    }
  } catch (err) {
    Object.assign(n, { status: 'error', error: err.message });
    renderNode(n);
    throw err;
  }
  n.opMode = job.mode;
  Object.assign(n, { jobId: job.id, status: 'running', progress: 0, error: null, stage: job.mode === 'local' ? 'ajuste simples' : 'enviado', cost: null });
  renderNode(n);
  save();
  toast(job.mode === 'local' ? `${n.name}: ajuste simples (sem modelo com referências)` : `${n.name}: aplicando com IA`);
  return watchJob(n);
}

// ---------------- Expandir / editar região ----------------

const EDIT_ASPECTS = ['1:1', '16:9', '9:16', '4:5', '21:9'];
const EDIT_ANCHORS = [['center', 'Centro'], ['top', 'Topo'], ['bottom', 'Base'], ['left', 'Esquerda'], ['right', 'Direita']];

function newEdit(pos) {
  return { id: uid('ed'), type: 'edit', name: t('Expandir / editar'), op: 'outpaint', aspect: '16:9', anchor: 'center',
    prompt: '', mask: null, model: 'auto', status: 'idle', output: null, takes: [], jobId: null, editMode: null, ...pos };
}

const editModeNote = (n) => (n.editMode === 'local'
  ? 'Sem modelo com referências: preenchimento simples (Pillow). A IA não foi usada.'
  : n.editMode === 'ai' ? 'Edição com IA: o original é mantido fora da área que muda.'
    : 'Com um modelo de imagem que aceite referências a IA faz o trabalho; sem ele, o preenchimento é simples (Pillow).');

function editBody(n) {
  const src = upscaleSource(n);
  const outpaint = n.op !== 'inpaint';
  const done = !!n.output;
  const aspects = EDIT_ASPECTS.map((a) => opt(a, n.aspect, a)).join('');
  const anchors = EDIT_ANCHORS.map(([k, v]) => opt(k, n.anchor, v)).join('');
  const body = outpaint
    ? `${field('Novo aspecto', `<select data-k="aspect">${aspects}</select>`)}
      ${field('Posição da imagem no quadro', `<select data-k="anchor">${anchors}</select>`)}`
    : `<button class="btn small ghost" data-act="mask">${ic('sparkles')} ${n.mask ? 'Repintar região' : 'Pintar região'}</button>
      <div class="cv-meta"><span>${n.mask ? 'Região pintada: a IA troca só ela.' : 'Pinte a região que deve mudar.'}</span></div>`;
  return `
    ${previewBlock(n, { placeholder: { icon: 'sparkles', text: src ? '' : 'Ligue uma imagem na entrada' }, ghost: src })}
    ${src ? '' : `<div class="cv-meta"><span class="err">${ic('alert')} Ligue uma imagem na entrada</span></div>`}
    ${field('Operação', `<select data-k="op">${opt('outpaint', n.op, 'Expandir (outpaint)')}${opt('inpaint', n.op, 'Editar região (inpaint)')}</select>`)}
    ${body}
    ${field('O que colocar (opcional)', `<textarea data-k="prompt" rows="2" maxlength="1000" placeholder="Ex.: céu noturno com estrelas">${esc(n.prompt)}</textarea>`)}
    <div class="cv-meta"><span>${esc(editModeNote(n))}</span></div>
    ${n.status === 'error' ? `<div class="cv-error">${ic('x-circle')}<span>${esc(n.error || 'erro')}</span></div>` : ''}
    <div class="cv-foot">
      <button class="btn primary" data-act="edit" ${n.status === 'running' || !src || (!outpaint && !n.mask) ? 'disabled' : ''}>${ic('sparkles')} ${done ? 'Aplicar de novo' : (outpaint ? 'Expandir' : 'Editar região')}</button>
    </div>`;
}

function bindEdit(el, n) {
  el.querySelectorAll('[data-k]').forEach((inp) => {
    const ev = inp.tagName === 'TEXTAREA' ? 'input' : 'change';
    inp.addEventListener(ev, () => {
      n[inp.dataset.k] = inp.value;
      save();
      if (inp.dataset.k === 'op') renderNode(n);
    });
  });
  el.querySelector('[data-act=mask]')?.addEventListener('click', () => openMask(n));
  el.querySelector('[data-act=edit]')?.addEventListener('click', () => runEdit(n).catch((err) => toast(err.message)));
}

// Expandir ou editar região: entra na fila de jobs. Sem modelo com referências, o preenchimento simples avisa.
async function runEdit(n) {
  const src = upscaleSource(n);
  if (!src) return toast('Ligue uma imagem na entrada');
  if (n.status === 'running') return;
  let job;
  try {
    if (n.op === 'inpaint') {
      if (!n.mask) return toast('Pinte a região que será editada');
      job = await api('/api/image/inpaint', { node_id: n.id, name: n.name, source_url: src, mask: n.mask, prompt: n.prompt || '', model: n.model || 'auto' });
    } else {
      job = await api('/api/image/outpaint', { node_id: n.id, name: n.name, source_url: src, aspect: n.aspect, anchor: n.anchor,
        prompt: n.prompt || '', model: n.model || 'auto' });
    }
  } catch (err) {
    Object.assign(n, { status: 'error', error: err.message });
    renderNode(n);
    throw err;
  }
  n.editMode = job.mode;
  Object.assign(n, { jobId: job.id, status: 'running', progress: 0, error: null, stage: job.mode === 'local' ? 'preenchimento simples' : 'enviado', cost: null });
  renderNode(n);
  save();
  toast(job.mode === 'local' ? `${n.name}: preenchimento simples (sem modelo com referências)` : `${n.name}: aplicando com IA`);
  return watchJob(n);
}

// ---------------- Upscale criativo ----------------

function newUpscale(pos) {
  return { id: uid('up'), type: 'upscale', name: t('Upscale'), factor: 2, creativity: 30, similarity: 70, prompt: '',
    model: 'auto', status: 'idle', output: null, takes: [], jobId: null, upscaleMode: null, ...pos };
}

const upscaleSource = (n) => frameOf(incoming(n.id)[0]);

const sliderField = (k, label, v) => `<label class="cv-slider"><span>${label} <output data-out="${k}">${v}</output></span>
  <input type="range" min="0" max="100" step="5" value="${v}" data-k="${k}" aria-label="${label}" aria-valuetext="${v}"></label>`;

function upscaleModeNote(n) {
  if (n.upscaleMode === 'local') return 'Upscale simples (Pillow): sem modelo criativo ativo.';
  if (n.upscaleMode === 'ai') return 'Upscale criativo com IA.';
  return 'Com um modelo de imagem que aceite referências: upscale criativo. Sem ele: upscale simples (Pillow).';
}

function upscaleBody(n) {
  const src = upscaleSource(n);
  const done = !!n.output;
  return `
    ${previewBlock(n, { placeholder: { icon: 'zoom-in', text: src ? '' : 'Ligue uma imagem na entrada' }, ghost: src })}
    ${src ? '' : `<div class="cv-meta"><span class="err">${ic('alert')} Ligue uma imagem na entrada</span></div>`}
    ${field('Fator', `<select data-k="factor">${[2, 4].map((f) => opt(f, n.factor, `${f}x`)).join('')}</select>`)}
    ${sliderField('creativity', 'Criatividade', n.creativity)}
    ${sliderField('similarity', 'Semelhança', n.similarity)}
    ${field('Nota (opcional)', `<textarea data-k="prompt" rows="2" maxlength="1000" placeholder="Ex.: mantenha o rótulo legível">${esc(n.prompt)}</textarea>`)}
    <div class="cv-meta"><span>${esc(upscaleModeNote(n))}</span></div>
    ${n.status === 'error' ? `<div class="cv-error">${ic('x-circle')}<span>${esc(n.error || 'erro')}</span></div>` : ''}
    <div class="cv-foot">
      <button class="btn primary" data-act="upscale" ${n.status === 'running' || !src ? 'disabled' : ''}>${ic('zoom-in')} ${done ? 'Ampliar de novo' : 'Ampliar'}</button>
    </div>`;
}

function bindUpscale(el, n) {
  el.querySelectorAll('[data-k]').forEach((inp) => {
    const range = inp.type === 'range';
    const ev = range || inp.tagName === 'TEXTAREA' ? 'input' : 'change';
    inp.addEventListener(ev, () => {
      const k = inp.dataset.k;
      n[k] = range || k === 'factor' ? +inp.value : inp.value;
      if (range) {
        el.querySelector(`output[data-out="${k}"]`).textContent = inp.value;
        inp.setAttribute('aria-valuetext', inp.value);
      }
      save();
    });
  });
  el.querySelector('[data-act=upscale]')?.addEventListener('click', () => runUpscale(n).catch((err) => toast(err.message)));
}

// Upscale: entra na fila de jobs (mesmas cotas e tokens dos demais renders). A versão anterior fica nas versões.
async function runUpscale(n) {
  const src = upscaleSource(n);
  if (!src) return toast('Ligue uma imagem na entrada');
  if (n.status === 'running') return;
  let job;
  try {
    job = await api('/api/image/upscale', { node_id: n.id, name: n.name, source_url: src, factor: +n.factor,
      creativity: +n.creativity, similarity: +n.similarity, prompt: n.prompt || '', model: n.model || 'auto' });
  } catch (err) {
    Object.assign(n, { status: 'error', error: err.message });
    renderNode(n);
    throw err;
  }
  n.upscaleMode = job.mode;
  Object.assign(n, { jobId: job.id, status: 'running', progress: 0, error: null, stage: job.mode === 'local' ? 'upscale simples' : 'enviado', cost: null });
  renderNode(n);
  save();
  toast(job.mode === 'local' ? `${n.name}: upscale simples (sem modelo criativo)` : `${n.name}: upscale criativo`);
  return watchJob(n);
}

// ---------------- Prévia, versões e regerar ----------------

// Área de mídia de qualquer gerador: resultado (com versões), andamento ou placeholder.
function previewBlock(n, { placeholder, ghost } = {}) {
  const out = n.output;
  const takes = n.takes || [];
  const idx = out ? takes.findIndex((t) => t.url === out.url) : -1;
  const media = out ? (out.kind === 'image' ? `<img src="${out.url}" alt="">`
    : `<video src="${out.url}" poster="${out.thumb || ''}" controls loop playsinline></video>`)
    : ghost ? `<img src="${ghost}" alt="" class="ghost">` : `<div class="cv-ph">${ic(placeholder?.icon || 'image')}<span>${esc(placeholder?.text || '')}</span></div>`;
  const running = n.status === 'running';
  return `<div class="cv-preview ${out ? 'has' : ''} ${running ? 'busy' : ''}">
    ${media}
    ${running ? `<div class="cv-busy"><div class="cv-busy-txt">${ic('loader', 'spin')}<span>${esc(n.stage || 'gerando')}</span><b>${n.progress || 0}%</b></div><div class="cv-bar"><i data-st="width:${n.progress || 0}%"></i></div></div>` : ''}
    ${out && !running ? `<div class="cv-pv-tools">
      ${takes.length > 1 ? `<div class="cv-takes"><button data-act="take" data-d="-1" title="Versão anterior" aria-label="Versão anterior">${ic('step-back')}</button><span>v${idx + 1}/${takes.length}</span><button data-act="take" data-d="1" title="Próxima versão" aria-label="Próxima versão">${ic('step-forward')}</button></div>` : '<span></span>'}
      <div class="cv-pv-btns">
        <button data-act="regen" title="Regerar: nova variação com o mesmo prompt" aria-label="Regerar: nova variação com o mesmo prompt">${ic('refresh')}</button>
        <a href="${out.url}" target="_blank" rel="noopener" title="Abrir em tamanho real">${ic('external')}</a>
        <a href="${out.url}" download title="Baixar">${ic('download')}</a>
      </div></div>` : ''}
  </div>`;
}

function bindPreview(el, n) {
  el.querySelectorAll('.cv-preview [data-act=take]').forEach((b) => b.addEventListener('click', () => {
    const takes = n.takes || [];
    const i = takes.findIndex((t) => t.url === n.output?.url);
    n.output = takes[(i + +b.dataset.d + takes.length) % takes.length];
    cv.edges.filter((e) => e.from === n.id).forEach((e) => { const t = byId(e.to); if (t) { invalidate(t); renderNode(t); } });
    renderNode(n);
    save();
  }));
  el.querySelector('.cv-preview [data-act=regen]')?.addEventListener('click', () => regenerate(n));
}

function regenerate(n) {
  if (n.status === 'running') return;
  const go = n.type === 'transition' ? genBridge(n)
    : n.type === 'pose' ? runPose(n)
      : GEN_KIND[n.type] ? generateAllPoses(n, { regenerate: true })
        : n.type === 'sheet' ? buildSheet(n)
          : runShot(n);
  go.catch((err) => toast(err.message));
}

function addTake(n, media) {
  if (!media?.url) return;
  n.takes = [...(n.takes || []).filter((t) => t.url !== media.url), media].slice(-12);
}

function duplicateNode(n) {
  const copy = JSON.parse(JSON.stringify(n));
  Object.assign(copy, { id: uid(n.type), x: n.x + 40, y: n.y + 40, name: `${n.name} (cópia)`, output: null, takes: [],
    jobId: null, status: 'idle', progress: 0, error: null, preview: null, approval: null });
  addNode(copy);
}

function field(label, html, extra = '') {
  return `<label class="cv-field ${extra}"><span class="cv-flabel">${label}</span>${html}</label>`;
}

const opt = (v, cur, text = v) => `<option value="${v}" ${String(v) === String(cur) ? 'selected' : ''}>${text}</option>`;

// vídeo de preview toca ao passar o mouse (sem onmouseenter inline: CSP)
document.addEventListener('mouseover', (e) => { const v = e.target.closest?.('video[data-hover-play]'); if (v) v.play().catch(() => {}); });
document.addEventListener('mouseout', (e) => {
  const v = e.target.closest?.('video[data-hover-play]');
  if (v && !v.contains(e.relatedTarget)) v.pause();
});

function mediaBody(n) {
  const m = n.media;
  return `<div class="cv-preview has">${m.kind === 'video'
    ? `<video src="${m.url}" poster="${m.thumb}" muted loop playsinline data-hover-play></video>`
    : `<img src="${m.url}" alt="">`}</div>
    <div class="cv-meta"><span>${m.kind === 'video' ? `${m.duration || '?'}s · ${m.width || ''}×${m.height || ''}` : 'imagem'}</span><span>${m.kind === 'video' ? 'saída = último frame' : 'saída = esta imagem'}</span></div>`;
}

function shotBody(n) {
  const frame = startFrameOf(n);
  const plan = planOf(n);
  const durs = durationsFor(n);
  if (!durs.includes(+n.duration)) n.duration = durs.reduce((a, b) => (Math.abs(b - n.duration) < Math.abs(a - n.duration) ? b : a));
  const needsStill = plan.steps.some((s) => s.kind === 'image');
  const done = !!n.output;
  return `
    ${previewBlock(n, { ghost: frame, placeholder: { icon: 'video', text: frame ? '' : 'O vídeo gerado aparece aqui' } })}
    ${frame ? `<div class="cv-chip-row"><span class="cv-chip">${ic('image')} começa do frame conectado</span>${hasComposable(n) && videoPick(n)?.caps?.refs ? `<span class="cv-chip ok" title="Este modelo recebe várias imagens: o frame vai como imagem 1 e as referências em seguida">${ic('users')} frame + referências vão juntos</span>` : hasComposable(n) ? (() => { const what = castNodesOf(n).length ? 'elenco' : 'referências'; return `<span class="cv-chip ${composeOn(n) ? 'ok' : 'warn'}" data-act="compose" title="Clique para ligar/desligar. Desligado, o vídeo usa só o frame conectado e o modelo pode ignorar as referências.">${ic('users')} ${composeOn(n) ? `${what} entra${what === 'referências' ? 'm' : ''} no 1º frame` : `${what} NÃO entra${what === 'referências' ? 'm' : ''} no 1º frame`}</span>`; })() : ''}</div>` : ''}
    ${field('O que acontece no plano', `<textarea data-k="intent" rows="3" placeholder="Ex.: a garrafa gira devagar sobre o gelo, gotas escorrendo, luz verde de fundo. Cite a marca pelo nome.">${esc(n.intent)}</textarea>`)}
    ${startPickerBlock(n)}
    ${directionCard(n)}
    ${refsBlock(n, frame ? (videoPick(n)?.caps?.refs ? `Vão direto para o vídeo junto com o frame (até ${videoPick(n).caps.max_refs || 4} imagens no total).` : composeOn(n) ? `Entram no 1º frame (gerado a partir do frame conectado)${videoTakesRefs(n) ? ' e também vão como referência para o vídeo' : ''}.` : videoTakesRefs(n) ? 'Vão como referência para o vídeo e orientam o prompt.' : 'Este modelo de vídeo não recebe referências: ligue "entram no 1º frame" para elas aparecerem.') : 'Logo, produto, estilo: entram no 1º frame gerado.')}
    <div class="cv-sec">
      ${field('Modelo de vídeo', modelSelect('model', 'video', n.model, frame ? { i2v: true } : n.startMode === 'text' ? { t2v: true } : {},
        frame || n.startMode === 'still' || !autoModel('video', { t2v: true }) ? { i2v: true } : { t2v: true }))}
      <div class="cv-plan">${plan.missing ? `<span class="err">${ic('alert')} ${esc(plan.missing)}</span>`
        : `<span>${plan.steps.map((s) => `<i class="cv-step ${s.kind}">${ic(s.kind === 'image' ? 'image' : 'video')}${esc(s.m.label)}</i>`).join(ic('step-forward', 'sep'))}</span><b class="cv-cost">~${fmtTok(planCost(plan, n.duration))}</b>`}</div>
      ${noRefsWarning(n)}
    </div>
    <div class="cv-row4">
      ${field('Duração', `<select data-k="duration">${durs.map((d) => opt(d, n.duration, `${d}s`)).join('')}</select>`)}
      ${field('Formato', `<select data-k="aspect">${['16:9', '9:16', '1:1'].map((a) => opt(a, n.aspect)).join('')}</select>`)}
      ${field('Qualidade', `<select data-k="quality">${opt('draft', n.quality === 'final' ? '' : 'draft', 'Rascunho')}${opt('final', n.quality, 'Final')}</select>`)}
      ${field('Versões', `<select data-k="variants" title="Quantas variações gerar de uma vez">${[1, 2, 3, 4].map((v) => opt(v, n.variants || 1, `×${v}`)).join('')}</select>`)}
    </div>
    ${frame ? '' : `<details class="cv-adv"><summary>${ic('settings')} Avançado</summary><div class="cv-row2">
      ${field('Começar por', `<select data-k="startMode">${Object.entries(START_MODES).map(([k, v]) => opt(k, n.startMode || 'auto', v)).join('')}</select>`)}
      ${field('Modelo do 1º frame', needsStill ? modelSelect('imageModel', 'image', n.imageModel, refsOf(n).length ? { refs: true } : { t2i: true }) : '<select disabled><option>— não precisa —</option></select>')}
      ${needsStill ? imageRefsWarning(plan.steps.find((s) => s.kind === 'image')?.m, refsOf(n).length + (composeOn(n) ? 1 : 0), composeOn(n)) : ''}
    </div></details>`}
    ${n.auto ? `<details class="cv-auto"><summary>${ic('sparkles')} Prompt técnico <span>${esc(n.auto.source)}</span></summary><p>${esc(n.auto.video_prompt)}</p>${n.auto.notes_pt ? `<p class="note">${esc(n.auto.notes_pt)}</p>` : ''}</details>` : ''}
    ${n.status === 'error' ? `<div class="cv-error">${ic('x-circle')}<span>${esc(n.error || 'erro')}</span></div>` : ''}
    <div class="cv-foot">
      <button class="btn ghost" data-act="prompt" title="Escrever/reescrever o prompt técnico">${ic('sparkles')} Prompt</button>
      <button class="btn primary" data-act="run" ${n.status === 'running' || plan.missing ? 'disabled' : ''}>${ic(done ? 'refresh' : 'play')} ${done ? 'Regerar' : 'Gerar vídeo'}</button>
    </div>`;
}

function imageGenBody(n) {
  const plan = planOf(n);
  const refs = refsOf(n).length;
  const m = plan.steps[0]?.m;
  const done = !!n.output;
  return `
    ${previewBlock(n, { placeholder: { icon: 'image', text: 'A imagem gerada aparece aqui' } })}
    ${field('Descreva a imagem', `<textarea data-k="intent" rows="3" placeholder="Ex.: a garrafa da marca gelada sobre o balcão do bar, logo da imagem 1 no rótulo.">${esc(n.intent)}</textarea>`)}
    ${refsBlock(n, 'Logo, foto do produto, estilo. A IA chama de "imagem 1, 2…" no prompt.')}
    <div class="cv-sec">
      ${field('Modelo de imagem', modelSelect('model', 'image', n.model, refs ? {} : { t2i: true }))}
      <div class="cv-plan">${plan.missing ? `<span class="err">${ic('alert')} ${esc(plan.missing)}</span>`
        : `<span><i class="cv-step image">${ic('image')}${esc(m.label)}</i>${refs && !m.caps.refs ? ' <span class="cv-warn">não usa referências</span>' : ''}</span><b class="cv-cost">${costLabel(m)}</b>`}</div>
      ${imageRefsWarning(m, refs)}
    </div>
    <div class="cv-row3">
      ${field('Formato', `<select data-k="aspect">${['16:9', '9:16', '1:1'].map((a) => opt(a, n.aspect)).join('')}</select>`)}
      ${field('Lente', `<select data-k="lens">${Object.keys(state.catalog.camera.lenses).map((k) => opt(k, n.lens, lensLabel(k))).join('')}</select>`)}
      ${field('Versões', `<select data-k="variants" title="Quantas variações gerar de uma vez">${[1, 2, 3, 4].map((v) => opt(v, n.variants || 1, `×${v}`)).join('')}</select>`)}
    </div>
    ${n.auto ? `<details class="cv-auto"><summary>${ic('sparkles')} Prompt técnico <span>${esc(n.auto.source)}</span></summary><p>${esc(n.auto.keyframe_prompt)}</p></details>` : ''}
    ${n.status === 'error' ? `<div class="cv-error">${ic('x-circle')}<span>${esc(n.error || 'erro')}</span></div>` : ''}
    <div class="cv-foot">
      <button class="btn ghost" data-act="prompt" title="Escrever/reescrever o prompt técnico">${ic('sparkles')} Prompt</button>
      ${done ? `<button class="btn ghost" data-act="sketch" title="Marque regiões e escreva o que muda: a imagem é regerada sem os traços">${ic('route')} Desenhar para regerar</button>` : ''}
      <button class="btn primary" data-act="run" ${n.status === 'running' || plan.missing ? 'disabled' : ''}>${ic(done ? 'refresh' : 'play')} ${done ? 'Regerar' : 'Gerar imagem'}</button>
    </div>`;
}

function statusLine(n) {
  if (n.status === 'running') return `<span>${ic('loader', 'spin')} ${esc(n.stage || 'gerando')}</span><span class="mono">${n.progress || 0}%</span>`;
  if (n.status === 'error') return `<span class="err">${ic('x-circle')} ${esc(n.error || 'erro')}</span>`;
  if (n.status === 'done') return `<span class="ok">${ic('check-circle')} pronto</span><span class="mono">${n.cost ? fmtTok(n.cost) : ''}</span>`;
  return '<span>pronto para gerar</span>';
}

function montageBody(n) {
  const seq = sequenceOf(n);
  return `${n.output ? `<div class="cv-preview has"><video src="${n.output}" controls playsinline></video><div class="cv-pv-tools"><span></span><div class="cv-pv-btns"><a href="${n.output}" download title="Baixar corte">${ic('download')}</a></div></div></div>`
    : `<div class="cv-preview"><div class="cv-ph">${ic('film')}<span>O corte final aparece aqui</span></div></div>`}
    <span class="cv-flabel">Sequência</span>
    <div class="cv-montage-list">${seq.length ? seq.map((s) => s.type === 'transition'
      ? `<div class="tr">${ic('corner-right')} ${esc(transitionLabel(s))}${s.cutAt != null ? ` · corte em ${(+s.cutAt).toFixed(2)}s` : ''}</div>`
      : `<div>${esc(s.name)} ${videoOf(s) ? ic('check', 'ok') : '— sem vídeo'}</div>`).join('') : '<div>Ligue planos ou vídeos aqui</div>'}</div>
    <div class="cv-refs" data-role="audio" title="Solte aqui um áudio de Meus arquivos">
      <div class="cv-refs-head"><b>Trilha / locução</b><span>${n.audio ? esc(n.audio.name || 'áudio') : 'opcional: solte um áudio aqui'}</span></div>
      ${n.audio ? `<audio src="${n.audio.url}" controls data-st="width:100%"></audio><button class="btn small ghost" data-act="noaudio">Remover áudio</button>`
        : `<label class="btn small ghost">${ic('music')} Enviar áudio<input type="file" accept="audio/*" hidden data-role="audio-file"></label>`}
    </div>
    <div class="cv-foot"><button class="btn primary" data-act="montage">${ic('film')} ${n.output ? 'Montar de novo' : 'Montar corte'}</button></div>`;
}

function bindDrag(el, n) {
  el.querySelector('.cv-head').addEventListener('mousedown', (e) => {
    if (e.target.closest('button') || e.target.isContentEditable) return;
    e.preventDefault();
    const s = { x: e.clientX, y: e.clientY, nx: n.x, ny: n.y };
    const move = (ev) => {
      n.x = Math.round(s.nx + (ev.clientX - s.x) / cv.zoom);
      n.y = Math.round(s.ny + (ev.clientY - s.y) / cv.zoom);
      el.style.left = `${n.x}px`;
      el.style.top = `${n.y}px`;
      drawEdges();
    };
    const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); save(); };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  });
}

function bindShot(el, n) {
  el.querySelectorAll('[data-k]').forEach((inp) => {
    const ev = inp.tagName === 'TEXTAREA' ? 'input' : 'change';
    inp.addEventListener(ev, () => {
      n[inp.dataset.k] = ['duration', 'variants'].includes(inp.dataset.k) ? +inp.value : inp.value;
      if (['aspect', 'duration'].includes(inp.dataset.k)) n.autoKey = null;
      save();
      if (inp.tagName === 'SELECT') renderNode(n);
    });
  });
  bindDirectionCard(el, n);
  el.querySelector('[data-act=compose]')?.addEventListener('click', () => { n.compose = composeOn(n) ? 'off' : 'on'; n.autoKey = null; save(); renderNode(n); });
  el.querySelector('.cv-adv')?.addEventListener('toggle', (e) => { n.advOpen = e.target.open; });
  if (n.advOpen) el.querySelector('.cv-adv')?.setAttribute('open', '');
  el.querySelector('[data-act=prompt]').onclick = async () => { await ensureAuto(n, true); };
  el.querySelector('[data-act=sketch]')?.addEventListener('click', () => openSketch(n));
  el.querySelector('[data-act=run]').onclick = () => runShot(n).catch((err) => toast(err.message));
  bindRefs(el, n);
  bindStartPicker(el, n);
}

// ---------------- Referências (logo, produto, estilo) ----------------

function refsBlock(n, hint) {
  const linked = refsOf(n).filter((u) => !(n.refs || []).some((r) => r.url === u));
  return `<div class="cv-refs" data-role="refs" title="Solte aqui imagens da biblioteca ou do computador">
    <div class="cv-refs-head"><b>Referências</b><span>${esc(hint)}</span></div>
    <div class="cv-refs-row">
      ${(n.refs || []).map((r, i) => `<div class="cv-ref" data-st="background-image:url('${r.url}')" title="${esc(r.name || '')}"><span>${i + 1}</span><button data-del="${i}" title="Remover" aria-label="Remover">${ic('x')}</button></div>`).join('')}
      ${linked.map((u, j) => `<div class="cv-ref linked" data-st="background-image:url('${u}')" title="Ligada na porta R"><span>${(n.refs || []).length + j + 1}</span></div>`).join('')}
      <label class="cv-ref add" title="Enviar imagem de referência">${ic('plus')}<input type="file" accept="image/*" multiple hidden data-role="ref-file"></label>
    </div>
  </div>`;
}

function bindRefs(el, n) {
  const box = el.querySelector('[data-role=refs]');
  if (!box) return;
  const add = (m) => {
    n.refs = [...(n.refs || []), { url: m.kind === 'video' ? (m.last_frame || m.thumb) : m.url, name: m.name || '' }];
    n.autoKey = null;
    save();
    renderNode(n);
  };
  box.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => {
    n.refs.splice(+b.dataset.del, 1);
    n.autoKey = null;
    save();
    renderNode(n);
  }));
  box.querySelector('[data-role=ref-file]').onchange = async (e) => {
    for (const f of e.target.files) {
      try { add({ ...(await uploadFile(f)), name: f.name }); } catch (err) { toast(err.message); }
    }
    renderLibrary._sig = null;
  };
  box.addEventListener('dragover', (e) => { e.preventDefault(); e.stopPropagation(); box.classList.add('over'); });
  box.addEventListener('dragleave', () => box.classList.remove('over'));
  box.addEventListener('drop', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    box.classList.remove('over');
    const lib = e.dataTransfer.getData(DRAG_MEDIA);
    if (lib) return add(JSON.parse(lib));
    for (const f of e.dataTransfer.files) {
      try { add({ ...(await uploadFile(f)), name: f.name }); } catch (err) { toast(err.message); }
    }
    renderLibrary._sig = null;
  });
}

function bindMontage(el, n) {
  el.querySelector('[data-act=montage]').onclick = () => runMontage(n);
  const setAudio = (m) => { n.audio = m ? { url: m.url, name: m.name } : null; n.output = null; save(); renderNode(n); };
  el.querySelector('[data-act=noaudio]')?.addEventListener('click', () => setAudio(null));
  const inp = el.querySelector('[data-role=audio-file]');
  if (inp) inp.onchange = async (e) => { try { setAudio(await uploadFile(e.target.files[0])); renderLibrary._sig = null; } catch (err) { toast(err.message); } };
  const box = el.querySelector('[data-role=audio]');
  box.addEventListener('dragover', (e) => { e.preventDefault(); e.stopPropagation(); box.classList.add('over'); });
  box.addEventListener('dragleave', () => box.classList.remove('over'));
  box.addEventListener('drop', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    box.classList.remove('over');
    const lib = e.dataTransfer.getData(DRAG_MEDIA);
    const m = lib ? JSON.parse(lib) : e.dataTransfer.files[0] ? await uploadFile(e.dataTransfer.files[0]).catch((err) => toast(err.message)) : null;
    if (m?.kind !== 'audio') return toast('Solte um arquivo de áudio');
    setAudio(m);
  });
}

// ---------------- Ligações ----------------

const PORT_B_Y = 86;

function portPos(n, side, port) {
  const el = document.querySelector(`.cv-node[data-id="${n.id}"]`);
  const w = el ? el.offsetWidth : 300;
  return { x: n.x + (side === 'out' ? w : 0), y: n.y + (port === 'b' || port === 'ref' ? PORT_B_Y : PORT_Y) };
}

function curve(a, b) {
  const dx = Math.max(60, Math.abs(b.x - a.x) * 0.5);
  return `M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}`;
}

function drawEdges() {
  ropes.sync(); // cordas com gravidade (effects.js)
}

function startLink(e, from) {
  e.stopPropagation();
  e.preventDefault();
  cv.link = { a: portPos(from, 'out'), b: toWorld(e.clientX, e.clientY) };
  const move = (ev) => { cv.link.b = toWorld(ev.clientX, ev.clientY); drawEdges(); };
  const up = (ev) => {
    window.removeEventListener('mousemove', move);
    window.removeEventListener('mouseup', up);
    cv.link = null;
    const hit = document.elementFromPoint(ev.clientX, ev.clientY);
    const target = hit?.closest('.cv-node');
    const to = target && byId(target.dataset.id);
    if (to && to.id !== from.id && !['media', 'cast'].includes(to.type)) {
      let port = hit.closest('.cv-port')?.dataset.port;
      if (to.type === 'transition' && !port) {
        // Solto no corpo do card: metade de cima = A, metade de baixo = B (ou a porta livre).
        const r = target.getBoundingClientRect();
        port = !inputOn(to.id, 'a') ? 'a' : !inputOn(to.id, 'b') ? 'b' : (ev.clientY - r.top < r.height / 2 ? 'a' : 'b');
      }
      connect(from, to, port);
    }
    drawEdges();
  };
  window.addEventListener('mousemove', move);
  window.addEventListener('mouseup', up);
}

function connect(from, to, port) {
  if (GEN_KIND[to.type]) port = 'ref';
  if (to.type === 'relight') return connectRelight(from, to, port);
  if (from.type === 'cast') {
    if (to.type === 'shot' || GEN_KIND[to.type]) port = 'ref';
    else if (to.type === 'brief') {
      if (cv.edges.some((e) => e.from === from.id && e.to === to.id)) return;
      cv.edges.push({ id: uid('e'), from: from.id, to: to.id });
      renderNode(to);
      save();
      return toast(`${from.name} entra na decupagem do roteiro`);
    } else return toast('Elenco/Produto liga na porta R de Gerar imagem/vídeo ou no Roteiro');
  }
  if (to.type === 'brief') return toast('O Roteiro só recebe nós de Elenco/Produto');
  if ((to.type === 'shot' || GEN_KIND[to.type]) && port === 'ref') {
    if (cv.edges.some((e) => e.from === from.id && e.to === to.id && e.port === 'ref')) return;
    cv.edges.push({ id: uid('e'), from: from.id, to: to.id, port: 'ref' });
    if (GEN_KIND[to.type]) { queueAssetSync(to); renderNode(to); save(); return toast(`Referência "${from.name}" ligada: vai em todas as posições`); }
    to.autoKey = null;
    renderNode(to);
    save();
    return toast('Referência ligada: entra no prompt como "imagem N"');
  }
  if (to.type === 'shot' && to.gen === 'image') return toast('Para usar como referência, solte na porta R do gerador de imagem');
  if (to.type === 'shot') cv.edges = cv.edges.filter((e) => e.to !== to.id || e.port === 'ref'); // um só 1º frame
  if (to.type === 'transition') {
    port = port || 'a';
    cv.edges = cv.edges.filter((e) => !(e.to === to.id && (e.port === port || e.from === from.id)));
    to.output = null; to.plan = null; to.preview = null; to.status = 'idle';
  }
  if (cv.edges.some((e) => e.from === from.id && e.to === to.id)) return;
  cv.edges.push({ id: uid('e'), from: from.id, to: to.id, ...(to.type === 'transition' ? { port } : {}) });
  invalidate(to);
  renderNode(to);
  save();
  if (to.type === 'transition') toast(port === 'a' ? 'Entrada A: a transição sai deste vídeo/imagem' : 'Entrada B: a transição chega neste vídeo/imagem');
  if (to.type === 'shot') toast(from.type === 'transition' ? 'Continuidade: o último frame da transição vira o 1º frame do plano'
    : from.type === 'media' && from.media.kind === 'video' ? 'Continuação: o último frame do vídeo vira o 1º frame do plano' : 'Imagem conectada como 1º frame');
}

function invalidate(n) {
  if (n.type === 'shot') n.autoKey = null;
  if (n.type === 'transition') {
    n.plan = null;
    cv.edges.filter((e) => e.from === n.id).forEach((e) => { const t = byId(e.to); if (t?.type === 'shot') t.autoKey = null; });
  }
}

// ---------------- Prompt automático & render ----------------

// Sem Brand Kit na tela: marca/produto vêm do Roteiro e do Elenco de cada plano.
function kit() {
  return { brand: 'Marca', product: '', palette: '', mood: '', category: 'geral' };
}

// Direção salva sem interpretação da IA (ex.: offline): manda os textos dos cartões.
function directionFallback(n) {
  const L = n.direction?.layers || [];
  const cam = L.find((l) => l.color === 'camera');
  return { camera_en: cam?.prompt || '', elements: L.filter((l) => l.color !== 'camera' && l.prompt.trim())
    .map((l) => ({ color: l.color, target_en: `the element marked in ${l.color}`, action_en: l.prompt })) };
}

function autoRequest(n) {
  const up = incoming(n.id)[0];
  const frame = n.gen === 'image' ? null : startFrameOf(n);
  return {
    intent: n.intent || n.name,
    mode: n.gen === 'image' ? 'keyframe' : frame ? 'i2v' : 't2v',
    start_image_url: frame,
    camera_path: null,
    direction: n.gen !== 'image' && frame && n.direction?.frame === frame ? n.direction.result || directionFallback(n) : null,
    camera_hint: n.gen !== 'image' && !frame ? n.cameraHint || null : null,
    ref_image_urls: refsOf(n),
    lens: n.lens,
    duration: n.duration,
    previous_prompt: through(up)?.type === 'shot' ? through(up).auto?.video_prompt
      : through(up)?.type === 'transition' ? through(up).plan?.bridge_prompt_en || null : null,
    feedback: n.feedback || [],
    cast: castOf(n),
    transition_hint: null,
    kit: kit(),
  };
}

async function ensureAuto(n, force = false) {
  if (!n.intent.trim()) { toast('Escreva a intenção do plano primeiro'); return null; }
  const req = autoRequest(n);
  const key = JSON.stringify(req);
  if (!force && n.auto && n.autoKey === key) return n.auto;
  n.stage = req.mode === 'i2v' ? 'lendo a imagem + escrevendo prompt' : 'escrevendo prompt';
  const btn = document.querySelector(`.cv-node[data-id="${n.id}"] [data-act=prompt]`);
  if (btn) { btn.disabled = true; setBtn(btn, 'loader', 'Prompt'); }
  try {
    n.auto = await api('/api/autoprompt', req);
    n.autoKey = key;
    if (n.auto.camera) n.camAnalysis = n.auto.camera;
    if (n.auto.title && ['Novo plano', 'Novo vídeo', 'Nova imagem'].some((d) => d === n.name || t(d) === n.name)) n.name = n.auto.title;
    (n.auto.warnings || []).forEach((w) => toast(`Brand Guard: ${w}`));
    save();
    return n.auto;
  } finally {
    renderNode(n);
  }
}

function shotPayload(n, ap) {
  return {
    node_id: n.id, name: n.name, gen: n.gen === 'image' ? 'image' : 'video', model: n.model || 'auto',
    image_model: n.imageModel || 'auto', start_mode: n.startMode || 'auto',
    duration: n.duration, aspect: n.aspect || '16:9', quality: n.quality === 'final' ? 'final' : 'draft',
    prompt: (n.gen === 'image' ? ap.keyframe_prompt : ap.video_prompt) || n.intent, keyframe_prompt: ap.keyframe_prompt,
    negative: ap.negative_extra || '',
    start_image_url: n.gen === 'image' ? null : startFrameOf(n),
    ref_image_urls: refsOf(n),
    compose: composeOn(n),
    direction_image_url: n.gen !== 'image' && videoTakesRefs(n) ? diagramOf(n) : null,
  };
}

async function runShot(n) {
  const gate = blockingReview(n);
  if (gate) { pauseAt(gate); throw new Error(`Aguardando aprovação em "${gate.name}"`); }
  const up = through(incoming(n.id)[0]);
  if (up?.type === 'shot' && !up.output) throw new Error(`Gere "${up.name}" antes (ele fornece o 1º frame)`);
  if (up?.type === 'transition' && !up.output) throw new Error(`Gere a transição "${up.name}" antes (ela fornece o 1º frame)`);
  const ap = await ensureAuto(n);
  if (!ap) return;
  const load = refsLoad(n);
  if (load && load.total > load.max) toast(`${load.m.label} aceita até ${load.max} imagens: ${load.total - load.max} referência(s) de "${n.name}" ficaram de fora`);
  let job;
  try { job = await api('/api/render', shotPayload(n, ap)); } catch (err) {
    Object.assign(n, { status: 'error', error: err.message });
    renderNode(n);
    throw err;
  }
  Object.assign(n, { jobId: job.id, status: 'running', progress: 0, error: null, output: null, cost: null, stage: 'enviado' });
  renderNode(n);
  save();
  const extra = Math.max(0, (n.variants || 1) - 1);
  toast(`${n.name}: ${job.steps.map((s) => s.model_label).join(' → ')} (~${fmtTok(job.est_cost * (extra + 1))}${extra ? `, ${extra + 1} versões` : ''})`);
  for (let i = 0; i < extra; i++) {
    api('/api/render', shotPayload(n, ap)).then((j) => watchExtra(n, j.id)).catch((err) => toast(`Variação não enviada: ${err.message}`));
  }
  return watchJob(n);
}

function watchJob(n) {
  return new Promise((resolve) => {
    const tick = async () => {
      let j;
      try { j = await api(`/api/jobs/${n.jobId}`); } catch { return setTimeout(tick, 3000); }
      n.progress = j.progress;
      const cur = j.steps.find((s) => s.status === 'running');
      n.stage = [cur ? `${cur.label} · ${cur.model_label}` : j.status, j.detail].filter(Boolean).join(' · ');
      if (j.status === 'done') {
        Object.assign(n, { status: 'done', output: j.media, progress: 100, preview: null, cost: j.steps.reduce((t, s) => t + (s.cost || 0), 0) });
        addTake(n, j.media);
        if (j.spec?.prompt && n.auto && n.gen !== 'image') n.auto.video_prompt = j.spec.prompt; // prompt já alinhado ao 1º frame real
        renderLibrary._sig = null;
        renderLibrary();
        loadMe().catch(() => {});
        renderNode(n);
        cv.edges.filter((e) => e.from === n.id).forEach((e) => { const t = byId(e.to); if (t) { invalidate(t); renderNode(t); } });
        save();
        toast(`${n.name} pronto`);
        return resolve(n);
      }
      if (['error', 'interrupted'].includes(j.status)) {
        Object.assign(n, { status: 'error', error: j.error || j.status });
        renderNode(n);
        save();
        return resolve(n);
      }
      const el = document.querySelector(`.cv-node[data-id="${n.id}"]`);
      const st = el?.querySelector('.cv-status');
      if (st) st.innerHTML = statusLine(n);
      const busy = el?.querySelector('.cv-busy');
      if (busy) {
        busy.querySelector('.cv-busy-txt span').textContent = n.stage || 'gerando';
        busy.querySelector('.cv-busy-txt b').textContent = `${n.progress || 0}%`;
        busy.querySelector('.cv-bar i').style.width = `${n.progress || 0}%`;
      } else if (el && n.type !== 'transition') renderNode(n);
      setTimeout(tick, 2000);
    };
    tick();
  });
}

async function runAll() {
  const shots = topoShots();
  if (!shots.length) return toast('Nenhum plano no canvas');
  $('#cvRunAll').disabled = true;
  delete $('#cvRunAll').dataset.paused;
  setBtn($('#cvRunAll'), 'loader', 'Rodando fluxo');
  try {
    for (const n of shots) {
      if (n.status === 'done' && n.output) continue;
      const gate = blockingReview(n);
      if (gate) return pauseAt(gate); // espera aprovação; Aprovar chama runAll de novo
      if (n.type === 'transition') await genBridge(n); else await runShot(n);
      if (n.status === 'error') throw new Error(`${n.name}: ${n.error}`);
    }
    for (const m of cv.nodes.filter((x) => x.type === 'montage')) {
      const gate = blockingReview(m);
      if (gate) return pauseAt(gate);
      await runMontage(m);
    }
    toast('Fluxo completo');
  } catch (err) {
    toast(`Fluxo parado: ${err.message}`);
  } finally { $('#cvRunAll').disabled = false; if (!$('#cvRunAll').dataset.paused) setBtn($('#cvRunAll'), 'play', 'Rodar fluxo'); }
}

async function runMontage(n) {
  const items = montageItems(sequenceOf(n));
  if (!items.some((i) => i.type === 'clip')) return toast('Montagem sem vídeos prontos');
  try {
    const first = sequenceOf(n).find((s) => s.type !== 'transition');
    const r = await api('/api/montage', { items, aspect: first?.aspect || '16:9', audio_url: n.audio?.url || null });
    n.output = r.url;
    renderNode(n);
    save();
    toast(`Corte montado (${r.shots} planos)`);
  } catch (err) { toast(`Erro na montagem: ${err.message}`); }
}
