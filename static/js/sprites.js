// Gerar personagem / Gerar produto -> um nó por posição (8 direções) -> Spritesheet.
//
//   GERAR PERSONAGEM ──▶ [Frente] (master) ──▶ [3/4 dir] [Perfil] [Costas] ... ──▶ SPRITESHEET
// Toda posição recebe, montado no servidor: master (image 1) + TODAS as referências do item + extras do nó.
// Cada posição tem o próprio prompt (editável), regerar e sugestões de prompt da IA.

const GEN_KIND = { chargen: 'character', prodgen: 'product' };
const POSE_W = 300;

function newCharGen(pos, kind = 'character') {
  return { id: uid(kind === 'character' ? 'chargen' : 'prodgen'), type: kind === 'character' ? 'chargen' : 'prodgen',
    name: t(kind === 'character' ? 'Novo personagem' : 'Novo produto'), assetId: '', model: 'auto', aspect: '1:1',
    desc: '', refs: [], ...pos };
}

function newSheet(pos) {
  return { id: uid('sheet'), type: 'sheet', name: 'Spritesheet', cols: 0, bg: 'transparent', labels: true, cell: 512, ...pos };
}

const genAsset = (n) => (state.assets || []).find((a) => a.id === n.assetId) || null;
const posesOf = (g) => cv.nodes.filter((x) => x.type === 'pose' && x.parentId === g.id).sort((a, b) => a.order - b.order);
const masterOf = (g) => posesOf(g).find((p) => p.key === 'front');

// ---------------- Nó Personagem / Produto (é a ficha do item) ----------------

// Referências com nota: fotos do próprio nó + nós de imagem ligados na porta R (o título do nó é a nota).
function genRefs(n) {
  const out = (n.refs || []).map((r) => ({ url: r.url, note: r.note || r.name || '' }));
  cv.edges.filter((e) => e.to === n.id && e.port === 'ref').forEach((e) => {
    const s = byId(e.from);
    if (!s) return;
    if (s.type === 'cast') castImages(s).forEach((u) => out.push({ url: u, note: castInfo(s).name }));
    else { const u = frameOf(s); if (u) out.push({ url: u, note: s.name || '' }); }
  });
  const seen = new Set();
  return out.filter((r) => r.url && !seen.has(r.url) && seen.add(r.url));
}

function charGenBody(n) {
  const kind = GEN_KIND[n.type];
  const a = genAsset(n);
  const poses = posesOf(n);
  const done = poses.filter((p) => p.output).length;
  const master = masterOf(n);
  const refs = genRefs(n);
  const models = (state.models.image || []).filter((m) => m.caps.refs);
  const attrs = n.attributes || a?.attributes || {};
  return `
    ${previewBlock({ ...n, output: master?.output || null, takes: [] }, { placeholder: { icon: kind === 'character' ? 'user' : 'logo', text: 'A frente aparece aqui' } })}
    ${field('Nome', `<input data-k="name" value="${esc(n.name)}" placeholder="${kind === 'character' ? 'Ex.: Ana' : 'Ex.: Garrafa Verde'}">`)}
    ${field('Descrição visual (o que nunca muda)', `<textarea data-k="desc" rows="3" placeholder="${kind === 'character' ? 'Ex.: mulher de 30 anos, cabelo cacheado curto, camisa branca com o logo no peito' : 'Ex.: garrafa de vidro verde long neck, rótulo prata com estrela vermelha'}">${esc(n.desc || '')}</textarea>`)}
    <details class="cv-adv" ${n.attrsOpen ? 'open' : ''}><summary>${ic('list')} Ficha completa (atributos)</summary>
      <div class="cv-attrs">${AS_FIELDS[kind].map(([k, label, ph]) => `<input data-attr="${k}" value="${esc(attrs[k] || '')}" placeholder="${esc(label)}: ${esc(ph.replace('ex.: ', ''))}" title="${esc(label)}">`).join('')}</div>
    </details>
    <div class="cv-refs-sum ${refs.length ? '' : 'warn'}">${ic(refs.length ? 'check' : 'alert')}
      <span>${refs.length ? `${refs.length} referência(s) · <b>todas vão em todas as posições</b>, junto com a frente` : 'Sem fotos: a frente é criada só pela descrição e vira a referência das outras 7. Para roupa/logo exatos, solte fotos no canvas, dê nome e ligue na porta <b>R</b>.'}</span></div>
    ${refs.length ? `<div class="cv-reflist">${refs.slice(0, 10).map((r, i) => `<div><i data-st="background-image:url('${r.url}')"></i><span>image ${i + 2}: <b>${esc(r.note || 'referência')}</b></span></div>`).join('')}</div>` : ''}
    ${refsBlock(n, 'Ou envie fotos direto aqui (o nome do arquivo vira a nota).')}
    <div class="cv-row2">
      ${field('Modelo (aceita referências)', `<select data-k="model"><option value="auto">Automático</option>${models.map((m) => opt(m.id, n.model, esc(m.label))).join('')}</select>`)}
      ${field('Formato', `<select data-k="aspect">${['1:1', '9:16', '16:9'].map((x) => opt(x, n.aspect)).join('')}</select>`)}
    </div>
    ${models.length ? '' : `<div class="cv-error">${ic('alert')}<span>Nenhum modelo de imagem que aceite referências está liberado.</span></div>`}
    <div class="cv-meta"><span>${poses.length ? `${done}/${poses.length} posições prontas` : `${sprites_dirCount(kind)} posições serão criadas`}${a ? ' · na biblioteca' : ''}</span><span class="cv-cost">${models[0]?.cost ? `~${fmtTok(models[0].cost * sprites_dirCount(kind))} tudo` : ''}</span></div>
    <div class="cv-foot">
      <button class="btn primary" data-act="genall" ${n.busy || !models.length ? 'disabled' : ''}>${ic(n.busy ? 'loader' : poses.length && done === poses.length ? 'refresh' : 'sparkles', n.busy ? 'spin' : '')}
        ${n.busy ? `Gerando ${done}/${poses.length || sprites_dirCount(kind)}…` : !poses.length ? `Gerar ${kind === 'character' ? 'personagem' : 'produto'} (${sprites_dirCount(kind)} posições)`
          : done < poses.length ? `Gerar posições que faltam (${poses.length - done})` : 'Regerar todas as posições'}</button>
    </div>
    ${n.lastError ? `<div class="cv-error">${ic('x-circle')}<span>${esc(n.lastError)}</span></div>` : ''}`;
}

const sprites_dirs = { character: 8, product: 8 };
const sprites_dirCount = (kind) => sprites_dirs[kind] || 8;

function bindCharGen(el, n) {
  el.querySelectorAll('[data-k]').forEach((inp) => {
    inp.addEventListener(inp.tagName === 'SELECT' ? 'change' : 'input', () => {
      n[inp.dataset.k] = inp.value;
      if (inp.dataset.k === 'name') el.querySelector('.cv-htext .title').textContent = inp.value;
      if (['name', 'desc'].includes(inp.dataset.k)) queueAssetSync(n);
      save();
    });
  });
  el.querySelectorAll('[data-attr]').forEach((inp) => inp.addEventListener('input', () => {
    n.attributes = { ...(n.attributes || {}), [inp.dataset.attr]: inp.value };
    queueAssetSync(n);
    save();
  }));
  el.querySelector('.cv-adv')?.addEventListener('toggle', (e) => { n.attrsOpen = e.target.open; });
  bindRefs(el, n);
  el.querySelector('[data-act=genall]').onclick = () => {
    const poses = posesOf(n);
    const all = poses.length && poses.every((p) => p.output);
    if (all && !confirm('Regerar as 8 posições? As versões atuais continuam guardadas em cada nó (v1, v2…).')) return;
    n.lastError = null;
    generateAllPoses(n, { regenerate: all }).catch((err) => { n.busy = false; n.lastError = err.message; renderNode(n); toast(err.message); });
  };
}

// A ficha do nó é espelhada no item da biblioteca (que o Elenco do Video Studio usa).
function queueAssetSync(n) {
  clearTimeout(n._syncT);
  n._syncT = setTimeout(() => syncGenAsset(n).catch(() => {}), 700);
}

async function syncGenAsset(n) {
  const kind = GEN_KIND[n.type];
  const body = { kind, name: n.name || 'Sem nome', description: n.desc || '', attributes: n.attributes || {}, refs: genRefs(n) };
  let a = genAsset(n);
  if (a) {
    body.images = a.images.map((i) => ({ url: i.url, use: !!i.use }));
    a = await api(`/api/assets/${a.id}`, body, 'PUT');
  } else {
    a = await api('/api/assets', body);
    n.assetId = a.id;
    posesOf(n).forEach((p) => { p.assetId = a.id; });
    save();
  }
  const list = state.assets || (state.assets = []);
  const i = list.findIndex((x) => x.id === a.id);
  if (i >= 0) list[i] = a; else list.unshift(a);
  return a;
}

// Itens da biblioteca sem nó nesta área (ex.: criados antes) ganham um nó de ficha.
function syncAssetNodes(ws, otherTabs = []) {
  const type = ws === 'character' ? 'chargen' : 'prodgen';
  const have = new Set([...cv.nodes, ...otherTabs].filter((x) => x.type === type).map((x) => x.assetId));
  let y = Math.max(40, ...cv.nodes.map((x) => x.y + 900)) - 900;
  for (const a of (state.assets || []).filter((x) => x.kind === ws && !have.has(x.id))) {
    y += 900;
    const n = newCharGen({ x: 40, y }, ws);
    Object.assign(n, { assetId: a.id, name: a.name, desc: a.description, attributes: a.attributes,
      refs: a.refs.map((r) => ({ url: r.url, name: r.note, note: r.note })) });
    cv.nodes.push(n);
  }
}

async function ensureGenAsset(n) {
  if (!genRefs(n).length && !(n.desc || '').trim()) throw new Error('Descreva o item ou ligue referências primeiro');
  return syncGenAsset(n); // sempre envia a ficha e as referências atuais antes de gerar
}

async function generateAllPoses(g, { onlyMaster = false, regenerate = false } = {}) {
  g.busy = true;
  renderNode(g);
  try {
    const a = await ensureGenAsset(g);
    const { directions } = await api(`/api/assets/${a.id}/directions`);
    let poses = posesOf(g);
    if (!poses.length) poses = spawnPoses(g, directions);
    let sheet = cv.nodes.find((x) => x.type === 'sheet' && cv.edges.some((e) => e.to === x.id && poses.some((p) => p.id === e.from)));
    if (!sheet) sheet = spawnSheet(g, poses);
    if (!cv.nodes.some((x) => x.type === 'save' && saveOwner(x)?.id === g.id)) spawnSave(g, poses, sheet);
    const master = masterOf(g);
    if (onlyMaster || regenerate || !master.output) {
      await runPose(master);
      if (master.status !== 'done') throw new Error(`A frente não foi gerada: ${master.error || 'erro no provedor'}`);
    }
    if (onlyMaster) return;
    // Em cadeia, girando: cada posição sai da anterior (passo de rotação) + frente (âncora).
    renderNode(g);
    for (const p of posesOf(g).filter((x) => x.key !== 'front')) {
      if (!regenerate && p.output) continue;
      await runPose(p).catch(() => {});
      renderNode(g);
      if (p.status !== 'done') throw new Error(`Parou em "${p.label}": ${p.error || 'erro no provedor'}. Corrija e clique de novo para continuar.`);
    }
    if (posesOf(g).every((p) => p.output)) await buildSheet(sheet).catch(() => {});
  } finally {
    g.busy = false;
    renderNode(g);
    save();
  }
}

function spawnPoses(g, directions) {
  const cols = 4;
  const made = [];
  directions.forEach((d, i) => {
    const p = { id: uid('pose'), type: 'pose', parentId: g.id, assetId: g.assetId, key: d.key, label: d.label, view: d.view,
      order: i, name: d.label, prompt: d.prompt, custom: false, feedback: '', suggestions: null,
      x: g.x + 460 + (i % cols) * (POSE_W + 40), y: g.y + Math.floor(i / cols) * 520 };
    markSpawn(p.id, 80 + i * 90);
    cv.nodes.push(p);
    cv.edges.push({ id: uid('e'), from: i === 0 ? g.id : made[i - 1].id, to: p.id }); // corrente: cada uma sai da anterior
    made.push(p);
  });
  renderAll();
  save();
  return made;
}

function spawnSheet(g, poses) {
  const s = newSheet({ x: g.x + 460 + 4 * (POSE_W + 40) + 40, y: g.y + 200 });
  markSpawn(s.id, 900);
  cv.nodes.push(s);
  poses.forEach((p) => cv.edges.push({ id: uid('e'), from: p.id, to: s.id }));
  renderAll();
  save();
  return s;
}

// ---------------- Nó de posição ----------------

function poseBody(n) {
  const g = byId(n.parentId);
  const master = g && masterOf(g);
  const isMaster = n.key === 'front';
  return `
    ${previewBlock(n, { ghost: !isMaster ? master?.output?.url : null, placeholder: { icon: 'frame', text: isMaster ? 'Frente' : 'Aguardando a frente' } })}
    <div class="cv-chip-row"><span class="cv-chip">${ic(isMaster ? 'star' : 'frame')} ${isMaster ? 'FRENTE · referência das outras posições' : esc(n.label)}</span>${n.custom ? '<span class="cv-chip warn">prompt editado</span>' : ''}</div>
    <details class="cv-adv" ${n.promptOpen ? 'open' : ''}><summary>${ic('wand')} Prompt desta posição</summary>
      <textarea data-k="prompt" rows="5">${esc(n.prompt || '')}</textarea>
      <div class="cv-row2"><button class="btn small ghost" data-act="resetprompt" ${n.custom ? '' : 'disabled'}>${ic('undo')} Prompt padrão</button><span class="cv-side-note">As referências entram sempre, mesmo editando.</span></div>
    </details>
    ${n.suggestions ? `<div class="cv-suggest"><span class="cv-flabel">Sugestões da IA (${esc(n.suggestSource || '')})</span>
      ${n.suggestions.map((s, i) => `<button class="cv-sug" data-sug="${i}" title="${esc(s.prompt_en)}"><b>${esc(s.label_pt || `Opção ${i + 1}`)}</b><small>${esc(s.why_pt || '')}</small></button>`).join('')}</div>` : ''}
    ${n.status === 'error' ? `<div class="cv-error">${ic('x-circle')}<span>${esc(n.error || 'erro')}</span></div>` : ''}
    <div class="cv-foot">
      <button class="btn ghost" data-act="suggest" title="A IA propõe prompts alternativos para esta posição">${ic(n.suggesting ? 'loader' : 'sparkles', n.suggesting ? 'spin' : '')} Sugerir</button>
      <button class="btn primary" data-act="run" ${n.status === 'running' ? 'disabled' : ''}>${ic(n.output ? 'refresh' : 'play')} ${n.output ? 'Regerar' : 'Gerar'}</button>
    </div>`;
}

function bindPose(el, n) {
  const ta = el.querySelector('[data-k=prompt]');
  ta.addEventListener('input', () => { n.prompt = ta.value; n.custom = true; save(); });
  el.querySelector('.cv-adv').addEventListener('toggle', (e) => { n.promptOpen = e.target.open; });
  el.querySelector('[data-act=resetprompt]').onclick = async () => {
    const r = await api(`/api/assets/${n.assetId}/pose-prompt`, { key: n.key });
    Object.assign(n, { prompt: r.prompt, custom: false });
    renderNode(n);
    save();
  };
  el.querySelector('[data-act=run]').onclick = () => runPose(n).catch((err) => toast(err.message));
  el.querySelector('[data-act=suggest]').onclick = () => suggestPose(n).catch((err) => { n.suggesting = false; renderNode(n); toast(err.message); });
  el.querySelectorAll('[data-sug]').forEach((b) => b.addEventListener('click', () => {
    const s = n.suggestions[+b.dataset.sug];
    Object.assign(n, { prompt: s.prompt_en, custom: true, promptOpen: true, suggestions: null });
    renderNode(n);
    save();
    runPose(n).catch((err) => toast(err.message));
  }));
}

async function suggestPose(n) {
  const fb = prompt('O que não ficou bom nesta posição? (opcional, ajuda a IA a sugerir)', n.feedback || '');
  if (fb === null) return;
  n.feedback = fb;
  n.suggesting = true;
  renderNode(n);
  const a = (state.assets || []).find((x) => x.id === n.assetId);
  const r = await api('/api/prompts/suggest', { prompt: n.prompt, view: n.view || n.label, kind: a?.kind || 'character', name: a?.name || '', feedback: fb });
  Object.assign(n, { suggestions: r.suggestions, suggestSource: r.source, suggesting: false });
  renderNode(n);
  save();
}

async function runPose(n, opts = {}) {
  const g = byId(n.parentId);
  if (!g) throw new Error('O nó gerador desta posição foi removido');
  const master = masterOf(g);
  if (n.key !== 'front' && !master?.output?.url) throw new Error('Gere a frente antes desta posição');
  const chain = posesOf(g);
  const prev = chain.slice(0, chain.indexOf(n)).reverse().find((x) => x.output?.url); // posição anterior pronta
  let r;
  try {
    r = await api(`/api/assets/${n.assetId || g.assetId}/pose-render`, {
    key: n.key, prompt: n.custom ? n.prompt : null, master_url: n.key === 'front' ? null : master.output.url,
    prev_url: n.key === 'front' ? null : prev?.output?.url || null, prev_label: prev?.label || '',
    extra_refs: refsOf(g), model: g.model || 'auto', aspect: g.aspect || '1:1', name: `${g.name} · ${n.label}`,
    extra: opts.fix || '',
    });
  } catch (err) {
    Object.assign(n, { status: 'error', error: err.message });
    renderNode(n);
    save();
    throw err;
  }
  if (!n.custom) n.prompt = r.prompt;
  Object.assign(n, { jobId: r.job.id, status: 'running', progress: 0, error: null, stage: `${r.refs.length} referências enviadas` });
  renderNode(n);
  save();
  await watchJob(n);
  if (n.key === 'front') renderNode(g);
  return n;
}

// ---------------- Spritesheet ----------------

function sheetInputs(n) {
  return incoming(n.id).map((s) => ({ node: s, url: frameOf(s) })).filter((x) => x.url)
    .sort((a, b) => (a.node.order ?? 99) - (b.node.order ?? 99) || a.node.x - b.node.x);
}

function sheetBody(n) {
  const ins = sheetInputs(n);
  const total = incoming(n.id).length;
  return `
    ${previewBlock(n, { placeholder: { icon: 'dashboard', text: 'A spritesheet aparece aqui' } })}
    ${turnViewer(n)}
    <div class="cv-sheet-strip">${incoming(n.id).map((s) => `<i data-st="${frameOf(s) ? `background-image:url('${frameOf(s)}')` : ''}" title="${esc(s.name)}"></i>`).join('') || '<span class="cv-side-note">Ligue as posições aqui</span>'}</div>
    <div class="cv-row4">
      ${field('Colunas', `<select data-k="cols">${[0, 2, 3, 4, 8].map((c) => opt(c, n.cols, c ? c : 'auto')).join('')}</select>`)}
      ${field('Célula', `<select data-k="cell">${[256, 384, 512, 768].map((c) => opt(c, n.cell, `${c}px`)).join('')}</select>`)}
      ${field('Fundo', `<select data-k="bg">${opt('transparent', n.bg, 'Transparente')}${opt('white', n.bg, 'Branco')}${opt('grey', n.bg, 'Cinza')}${opt('black', n.bg, 'Preto')}</select>`)}
      ${field('Rótulos', `<select data-k="labels">${opt('1', n.labels ? '1' : '0', 'Sim')}${opt('0', n.labels ? '1' : '0', 'Não')}</select>`)}
    </div>
    ${n.output?.map_url ? `<div class="cv-meta"><span>${n.output.cols}×${n.output.rows} quadros</span><a href="${n.output.map_url}" download>${ic('download')} mapa .json</a></div>` : ''}
    <div class="cv-foot"><button class="btn primary" data-act="sheet" ${ins.length ? '' : 'disabled'}>${ic('dashboard')} Montar spritesheet (${ins.length}/${total})</button></div>`;
}

function bindSheet(el, n) {
  el.querySelectorAll('[data-k]').forEach((inp) => inp.addEventListener('change', () => {
    n[inp.dataset.k] = inp.dataset.k === 'labels' ? inp.value === '1' : inp.dataset.k === 'bg' ? inp.value : +inp.value;
    save();
  }));
  el.querySelector('[data-act=sheet]').onclick = () => buildSheet(n).catch((err) => toast(err.message));
  bindTurn(el, n);
}

// Visualizador de giro: arraste para os lados (ou play) e o personagem roda pelas posições.
function turnFrames(n) {
  return sheetInputs(n).filter((x) => x.node.type === 'pose');
}

function turnViewer(n) {
  const fr = turnFrames(n);
  if (fr.length < 2) return '';
  const i = Math.min(n.turnIdx || 0, fr.length - 1);
  return `<div class="sh-turn" data-role="turn" title="Arraste para os lados para girar">
    <img src="${fr[i].url}" alt="" draggable="false">
    <span class="sh-turn-label">${esc(fr[i].node.label)} · ${i + 1}/${fr.length}</span>
    <div class="sh-turn-bar">
      <button data-turn="-1" title="Girar para a esquerda" aria-label="Girar para a esquerda">${ic('step-back')}</button>
      <button data-turn="play" title="Girar sozinho" aria-label="Girar sozinho">${ic(n.turning ? 'pause' : 'play')}</button>
      <button data-turn="1" title="Girar para a direita" aria-label="Girar para a direita">${ic('step-forward')}</button>
      <input type="range" min="0" max="${fr.length - 1}" value="${i}" data-turn="range">
    </div>
  </div>`;
}

// Um único par de listeners globais para arrastar o giro (antes cada redesenho do nó somava mais dois e travava a página).
let turnDrag = null;
window.addEventListener('mousemove', (e) => { if (turnDrag) turnDrag.show(turnDrag.i + Math.round((e.clientX - turnDrag.x) / 40)); });
window.addEventListener('mouseup', () => { turnDrag = null; });

function bindTurn(el, n) {
  const box = el.querySelector('[data-role=turn]');
  if (!box) return;
  const fr = turnFrames(n);
  const img = box.querySelector('img');
  const label = box.querySelector('.sh-turn-label');
  const range = box.querySelector('[data-turn=range]');
  n.turnIdx = Math.min(n.turnIdx || 0, fr.length - 1);
  const show = (i) => {
    i = Number.isFinite(i) ? i : 0;
    n.turnIdx = ((i % fr.length) + fr.length) % fr.length;
    img.src = fr[n.turnIdx].url;
    label.textContent = `${fr[n.turnIdx].node.label} · ${n.turnIdx + 1}/${fr.length}`;
    range.value = n.turnIdx;
  };
  // pré-carrega para o giro ficar suave
  fr.forEach((f) => { const im = new Image(); im.src = f.url; });
  box.querySelectorAll('button[data-turn]').forEach((b) => b.addEventListener('click', (e) => {
    e.stopPropagation();
    if (b.dataset.turn === 'play') {
      n.turning = !n.turning;
      clearInterval(n._turnT);
      if (n.turning) n._turnT = setInterval(() => (document.body.contains(img) ? show(n.turnIdx + 1) : clearInterval(n._turnT)), 220);
      b.innerHTML = ic(n.turning ? 'pause' : 'play');
      return;
    }
    show(n.turnIdx + +b.dataset.turn);
  }));
  range.addEventListener('input', () => show(+range.value));
  img.addEventListener('mousedown', (e) => { e.stopPropagation(); e.preventDefault(); turnDrag = { x: e.clientX, i: n.turnIdx || 0, show }; });
  if (n.turning) { clearInterval(n._turnT); n._turnT = setInterval(() => (document.body.contains(img) ? show(n.turnIdx + 1) : clearInterval(n._turnT)), 220); }
}

async function buildSheet(n) {
  const ins = sheetInputs(n);
  if (!ins.length) throw new Error('Nenhuma posição pronta ligada');
  n.status = 'running'; n.stage = 'montando'; n.progress = 50;
  renderNode(n);
  try {
    const out = await api('/api/spritesheet', { urls: ins.map((x) => x.url), labels: ins.map((x) => x.node.label || x.node.name),
      cols: n.cols || 0, cell: n.cell || 512, bg: n.bg || 'transparent', show_labels: n.labels !== false });
    Object.assign(n, { output: out, status: 'done', progress: 100 });
    addTake(n, out);
    renderLibrary._sig = null;
    renderLibrary();
    toast(`Spritesheet pronta: ${out.cols}×${out.rows}`);
  } catch (err) {
    Object.assign(n, { status: 'error', error: err.message });
    throw err;
  } finally {
    renderNode(n);
    save();
  }
}

// ---------------- Nó Salvar na biblioteca ----------------

const DRAG_ASSET = 'application/x-studio-asset';

function newSave(pos) {
  return { id: uid('save'), type: 'save', name: t('Salvar na biblioteca'), excluded: [], cover: null, ...pos };
}

function spawnSave(g, poses, sheet) {
  const s = newSave({ x: sheet.x + 430, y: sheet.y });
  markSpawn(s.id, 1000);
  cv.nodes.push(s);
  poses.forEach((p) => cv.edges.push({ id: uid('e'), from: p.id, to: s.id }));
  cv.edges.push({ id: uid('e'), from: sheet.id, to: s.id });
  renderAll();
  save();
  return s;
}

// Dono do Salvar: o nó Personagem/Produto acima das posições ligadas.
function saveOwner(n) {
  for (const s of incoming(n.id)) {
    if (GEN_KIND[s.type]) return s;
    if (s.type === 'pose') return byId(s.parentId);
    if (s.type === 'sheet') { const p = incoming(s.id).find((x) => x.type === 'pose'); if (p) return byId(p.parentId); }
  }
  return null;
}

function saveItems(n) {
  return incoming(n.id).map((s) => ({ node: s, url: frameOf(s), view: s.key || (s.type === 'sheet' ? 'spritesheet' : null), label: s.label || s.name }))
    .filter((x) => x.url).sort((a, b) => (a.node.order ?? 50) - (b.node.order ?? 50));
}

function saveBody(n) {
  const g = saveOwner(n);
  const a = g && genAsset(g);
  const items = saveItems(n);
  const chosen = items.filter((x) => !(n.excluded || []).includes(x.node.id));
  const cover = n.cover && chosen.some((x) => x.url === n.cover) ? n.cover : chosen[0]?.url;
  const savedUrls = new Set((a?.images || []).filter((i) => i.use).map((i) => i.url));
  const dirty = !a?.saved || chosen.length !== savedUrls.size || chosen.some((x) => !savedUrls.has(x.url)) || (a.cover && cover !== a.cover);
  return `
    <div class="sv-head">${a?.saved ? `<span class="cv-chip">${ic('check')} salvo ${fmtDate(a.saved_at)}</span>` : '<span class="cv-chip warn">ainda não salvo</span>'}${a?.saved && dirty ? '<span class="cv-chip warn">alterações não salvas</span>' : ''}</div>
    ${g ? `<div class="cv-asset-info"><b>${esc(g.name)}</b><p>${GEN_KIND[g.type] === 'character' ? 'Personagem' : 'Produto'} · ${chosen.length} de ${items.length} imagem(ns) selecionada(s)</p></div>`
      : '<div class="cv-error">' + ic('alert') + '<span>Ligue aqui as posições de um Personagem/Produto.</span></div>'}
    <div class="sv-grid">${items.map((x) => `<button class="sv-item ${(n.excluded || []).includes(x.node.id) ? 'off' : ''} ${x.url === cover ? 'cover' : ''}" data-node="${x.node.id}" data-st="background-image:url('${x.url}')" title="Clique: incluir/tirar · duplo clique: capa">
      <span>${esc(x.label || '')}</span>${x.url === cover ? `<i>${ic('star')}</i>` : ''}</button>`).join('') || '<p class="cv-side-note">Nenhuma imagem pronta ligada.</p>'}</div>
    <p class="cv-side-note">Clique para incluir/tirar · <b>duplo clique</b> escolhe a capa (${ic('star')}).</p>
    <div class="cv-foot"><button class="btn primary" data-act="save" ${g && chosen.length ? '' : 'disabled'}>${ic('check-circle')} ${a?.saved ? 'Salvar alterações' : `Salvar ${g && GEN_KIND[g.type] === 'product' ? 'produto' : 'personagem'}`}</button></div>`;
}

function bindSave(el, n) {
  el.querySelectorAll('.sv-item').forEach((b) => {
    b.addEventListener('click', (e) => {
      if (e.detail > 1) return;
      const id = b.dataset.node;
      n.excluded = (n.excluded || []).includes(id) ? n.excluded.filter((x) => x !== id) : [...(n.excluded || []), id];
      renderNode(n);
      save();
    });
    b.addEventListener('dblclick', () => {
      const it = saveItems(n).find((x) => x.node.id === b.dataset.node);
      n.cover = it?.url || null;
      n.excluded = (n.excluded || []).filter((x) => x !== b.dataset.node);
      renderNode(n);
      save();
    });
  });
  el.querySelector('[data-act=save]').onclick = () => saveToLibrary(n).catch((err) => toast(err.message));
}

async function saveToLibrary(n) {
  const g = saveOwner(n);
  if (!g) throw new Error('Ligue as posições de um Personagem/Produto');
  const a = await syncGenAsset(g);
  const chosen = saveItems(n).filter((x) => !(n.excluded || []).includes(x.node.id));
  const cover = n.cover && chosen.some((x) => x.url === n.cover) ? n.cover : chosen[0]?.url;
  // versões antigas (não escolhidas e que não são a imagem atual do nó) saem: o item guarda só o que vale
  const keep = new Set(chosen.map((x) => x.url));
  const family = cv.nodes.filter((x) => x.id === g.id || (x.type === 'pose' && x.parentId === g.id)
    || (x.type === 'sheet' && incoming(x.id).some((p) => p.type === 'pose' && p.parentId === g.id)));
  family.forEach((x) => { if (x.output?.url) keep.add(x.output.url); });
  const discard = [...new Set(family.flatMap((x) => (x.takes || []).map((t) => t.url)))].filter((u) => u && !keep.has(u));
  const saved = await api(`/api/assets/${a.id}/save`, { name: g.name, cover, discard, tab: cv.doc,
    images: chosen.map((x) => ({ url: x.url, view: x.view, label: x.label })) });
  const gone = new Set(saved.deleted || []);
  if (gone.size) {
    family.forEach((x) => { if (x.takes) x.takes = x.takes.filter((t) => !gone.has(t.url)); renderNode(x); });
    save();
    renderLibrary._sig = null;
    renderLibrary();
  }
  const i = state.assets.findIndex((x) => x.id === saved.id);
  if (i >= 0) state.assets[i] = saved; else state.assets.unshift(saved);
  sparkleBurst(n.x + 180, n.y + 120, { count: 16, spread: 180, colors: ['#10b981', '#facc15', '#ffffff'] });
  renderNode(n);
  renderAssetPanel();
  toast(`${saved.name} salvo na biblioteca${gone.size ? ` · ${gone.size} versão(ões) antiga(s) apagada(s)` : ''}: já pode usar no Video Studio`);
}

// ---------------- Painel lateral: itens salvos ----------------

function renderAssetPanel() {
  const box = $('#cvAssetsPanel');
  if (!box) return;
  const ws = cv.ws;
  const kinds = ws === 'video' ? ['character', 'product'] : [ws];
  const list = (state.assets || []).filter((a) => a.saved && kinds.includes(a.kind));
  const title = ws === 'character' ? 'Meus personagens' : ws === 'product' ? 'Meus produtos' : 'Personagens e produtos';
  box.innerHTML = `<div class="section-label"><span>${title}</span><span class="hint">${list.length} salvo(s)</span></div>
    ${list.length ? `<div class="as-pgrid">${list.map((a) => {
      const img = a.cover || a.images.find((i) => i.use)?.url || a.images[0]?.url;
      return `<div class="as-pcard" draggable="true" data-asset="${a.id}" title="${ws === 'video' ? 'Arraste para o canvas: cria um nó Elenco' : 'Clique para abrir no canvas'}">
        <span class="as-pthumb" ${img ? `data-st="background-image:url('${img}')"` : ''}>${img ? '' : ic(a.kind === 'character' ? 'user' : 'logo')}</span>
        <b>${esc(a.name)}</b><small>${a.kind === 'character' ? 'personagem' : 'produto'} · ${a.images.filter((i) => i.use).length} img</small></div>`;
    }).join('')}</div>` : `<p class="cv-side-note">${ws === 'video' ? 'Nada salvo ainda. Crie nas abas Personagens/Produtos e use o nó Salvar.' : 'Nada salvo ainda. Gere as posições e clique em Salvar no nó Salvar na biblioteca.'}</p>`}`;
  box.querySelectorAll('[data-asset]').forEach((c) => {
    c.addEventListener('dragstart', (e) => e.dataTransfer.setData(DRAG_ASSET, c.dataset.asset));
    c.addEventListener('click', () => {
      if (cv.ws === 'video') return toast('Arraste para o canvas para usar num vídeo (cria um nó Elenco)');
      focusAsset(c.dataset.asset);
    });
  });
}

function dropAsset(assetId, at) {
  const a = (state.assets || []).find((x) => x.id === assetId);
  if (!a) return;
  if (cv.ws === 'video') {
    const n = newCast({ x: Math.round(at.x), y: Math.round(at.y) });
    Object.assign(n, { assetId: a.id, name: a.name, castKind: a.kind === 'character' ? 'personagem' : 'produto' });
    addNode(n);
    return toast(`${a.name} no vídeo: ligue a saída na porta R dos planos`);
  }
  if (cv.ws !== a.kind) return toast(`Arraste ${a.kind === 'character' ? 'personagens' : 'produtos'} na aba ${a.kind === 'character' ? 'Personagens' : 'Produtos'}`);
  if (cv.nodes.some((x) => GEN_KIND[x.type] && x.assetId === a.id)) return focusAsset(assetId);
  openAssetOnCanvas(a, at).catch((err) => toast(err.message));
}

// Recria no canvas a ficha + as 8 posições (com as imagens já geradas) + spritesheet + salvar.
async function openAssetOnCanvas(a, at) {
  const g = newCharGen({ x: Math.round(at.x), y: Math.round(at.y) }, a.kind);
  Object.assign(g, { assetId: a.id, name: a.name, desc: a.description, attributes: a.attributes,
    refs: a.refs.map((r) => ({ url: r.url, name: r.note, note: r.note })) });
  markSpawn(g.id);
  cv.nodes.push(g);
  const { directions } = await api(`/api/assets/${a.id}/directions`);
  const poses = spawnPoses(g, directions);
  const byView = (v) => {
    const imgs = a.images.filter((i) => i.view === v);
    return imgs.find((i) => i.use) || imgs[imgs.length - 1] || null;
  };
  for (const p of poses) {
    const im = byView(p.key);
    if (!im) continue;
    const m = { kind: 'image', url: im.url, thumb: im.url, name: p.label };
    Object.assign(p, { output: m, takes: a.images.filter((i) => i.view === p.key).map((i) => ({ kind: 'image', url: i.url, thumb: i.url })), status: 'done' });
  }
  const sheet = spawnSheet(g, poses);
  const sh = a.images.filter((i) => i.view === 'spritesheet').pop();
  if (sh) Object.assign(sheet, { output: { kind: 'image', url: sh.url, thumb: sh.url }, status: 'done' });
  spawnSave(g, poses, sheet);
  renderAll();
  save();
  toast(`${a.name} aberto: ${poses.filter((p) => p.output).length}/${poses.length} posições já geradas`);
}
