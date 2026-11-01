// Nó de Transição
//
//   A (vídeo ou imagem) ●─┐
//                         ├─ TRANSIÇÃO ─● saída: vídeo gerado (o último frame continua num novo plano)
//   B (vídeo ou imagem) ●─┘
//
// Modo "ai" (padrão): um modelo de vídeo com frame inicial + final gera o vídeo que sai do frame de A
// e chega no frame de B. A IA olha os dois frames e escreve o movimento de câmera.
// Modo "edit": transição editorial (dissolve, whip pan, flash...) feita na montagem; precisa de dois vídeos.

const outgoing = (id) => cv.edges.filter((e) => e.from === id).map((e) => byId(e.to)).filter(Boolean);
const durationOf = (n) => n?.output?.duration || (n?.type === 'media' ? n.media.duration : null) || null;
const isClip = (n) => !!n && (n.type === 'review' ? isClip(incoming(n.id)[0])
  : n.type === 'shot' || n.type === 'transition' || (n.type === 'media' && n.media.kind === 'video'));
const TR_MODES = {
  ai: { icon: 'sparkles', label: 'IA gera a transição', hint: 'Vídeo novo: do frame de A até o frame de B' },
  edit: { icon: 'scissors', label: 'Edição', hint: 'Dissolve, whip pan, flash... entre dois vídeos, na montagem' },
};

function trMode(t) {
  if (!['ai', 'edit'].includes(t.mode)) t.mode = t.kind && !['bridge', 'auto'].includes(t.kind) ? 'edit' : 'ai';
  return t.mode;
}

function trKind(t) {
  return trMode(t) === 'ai' ? 'bridge' : (t.editKind || 'dissolve');
}

function transitionLabel(t) {
  return trMode(t) === 'edit' ? (state.catalog.transitions[trKind(t)]?.label || trKind(t)) : TR_MODES.ai.label;
}

function newTransition(pos) {
  return { id: uid('trans'), type: 'transition', name: t('Transição'), mode: 'ai', editKind: 'dissolve', model: 'auto',
    bridgeDur: 5, idea: '', cutAt: null, frame: null, bIn: null, bFrame: null, plan: null, ...pos };
}

const trA = (t) => inputOn(t.id, 'a');
const trB = (t) => inputOn(t.id, 'b');

// Primeiro frame "real" de qualquer nó (entrada de B).
function firstFrameOf(n) {
  if (!n) return null;
  if (n.type === 'frame') return extractedFrame(n);
  if (n.type === 'review') return firstFrameOf(incoming(n.id)[0]);
  if (n.type === 'media') return n.media.kind === 'video' ? n.media.thumb : n.media.url;
  if (n.output) return n.output.kind === 'video' ? n.output.thumb : n.output.url;
  return null;
}

const exitFrame = (t) => t.frame?.url || frameOf(trA(t));
const entryFrame = (t) => t.bFrame?.url || firstFrameOf(trB(t));
const kindOf = (n) => (!n ? '—' : n.type === 'media' ? (n.media.kind === 'video' ? 'vídeo' : 'imagem')
  : n.output?.kind === 'image' ? 'imagem' : 'vídeo');

// Migra cards antigos (uma entrada + "saída para B") para o modelo de portas A/B.
function migrateTransitions() {
  cv.nodes.filter((n) => n.type === 'transition').forEach((t) => {
    trMode(t);
    cv.edges.filter((e) => e.to === t.id && !e.port).forEach((e) => { e.port = 'a'; });
    if (!inputOn(t.id, 'b')) {
      const old = cv.edges.find((e) => e.from === t.id && byId(e.to)?.type !== 'montage');
      if (old) { old.from = old.to; old.to = t.id; old.port = 'b'; }
    }
    // Montagem que já recebia A ou B passa a receber a transição (A → transição → B).
    const ends = [trA(t)?.id, trB(t)?.id];
    cv.nodes.filter((m) => m.type === 'montage' && cv.edges.some((e) => e.to === m.id && ends.includes(e.from)))
      .forEach((m) => { if (!cv.edges.some((e) => e.from === t.id && e.to === m.id)) cv.edges.push({ id: uid('e'), from: t.id, to: m.id }); });
  });
}

// ---------------- Sequência para a montagem ----------------
// A transição na montagem vira: [A cortado em cutAt] → [vídeo da transição] → [B a partir de bIn].

function seqBefore(n, guard = new Set()) {
  if (n?.type === 'review') return seqBefore(incoming(n.id)[0], guard); // aprovação é transparente na montagem
  if (!n || guard.has(n.id)) return [];
  guard.add(n.id);
  if (n.type === 'transition') {
    const a = trA(n), b = trB(n);
    return [...(isClip(a) ? seqBefore(a, guard) : []), n, ...(isClip(b) && !guard.has(b.id) ? [b] : [])];
  }
  const up = incoming(n.id)[0];
  return [...(isClip(up) ? seqBefore(up, guard) : []), n];
}

function sequenceOf(m) {
  // Cada entrada da montagem vira uma cadeia; cadeias contidas em outra maior somem
  // (ex.: A e B soltos + A→transição→B = uma cadeia só), e as restantes seguem a posição no canvas.
  const chains = incoming(m.id).map((n) => seqBefore(n));
  const kept = chains.filter((c, i) => !chains.some((o, j) => j !== i && o.length > c.length && c.every((x) => o.includes(x))));
  kept.sort((x, y) => x[0].x - y[0].x);
  const seen = new Set();
  const seq = [];
  kept.forEach((c) => c.forEach((x) => { if (!seen.has(x.id)) { seen.add(x.id); seq.push(x); } }));
  return seq;
}

function montageItems(seq) {
  const items = [];
  seq.forEach((n, i) => {
    const prev = seq[i - 1], next = seq[i + 1];
    if (n.type === 'transition') {
      if (trMode(n) === 'ai') {
        if (!n.output?.url) return;
        items.push({ type: 'clip', url: n.output.url });
        // Costura curta para "pousar" exatamente no B.
        if (next && next.id === trB(n)?.id) items.push({ type: 'transition', kind: 'dissolve', duration: 0.25 });
      } else {
        items.push({ type: 'transition', kind: trKind(n), duration: n.duration || null });
      }
      return;
    }
    const url = videoOf(n);
    if (!url) return;
    const clip = { type: 'clip', url };
    if (next?.type === 'transition' && trA(next)?.id === n.id && next.cutAt != null) clip.end = next.cutAt;
    if (next?.type === 'shot' && next.inFrame?.video === url) clip.end = next.inFrame.t;
    if (prev?.type === 'transition' && trB(prev)?.id === n.id && prev.bIn != null) clip.start = prev.bIn;
    items.push(clip);
  });
  return items;
}

// ---------------- Card ----------------

function framePicker(role, video, dur, t, label) {
  return `<div class="tr-pick" data-pick="${role}">
    <div class="tr-pick-head">${label}</div>
    <video class="cv-media" data-role="scrub" src="${video}" muted playsinline preload="auto"></video>
    <div class="tr-row">
      <input type="range" data-role="pos" min="0" max="${dur || 5}" step="0.0625" value="${t}">
      <span class="mono" data-role="poslabel">${(+t).toFixed(2)}s</span>
    </div>
    <div class="tr-row">
      <button class="btn small ghost" data-act="first">${ic('skip-back')}</button>
      <button class="btn small ghost" data-act="back">−1f</button>
      <button class="btn small ghost" data-act="fwd">+1f</button>
      <button class="btn small ghost" data-act="last">${ic('skip-forward')}</button>
      <button class="btn small primary" data-act="use">${ic('target')} ${role === 'a' ? 'A termina aqui' : 'B começa aqui'}</button>
    </div>
  </div>`;
}

function transitionBody(n) {
  const mode = trMode(n);
  const a = trA(n), b = trB(n);
  const exit = exitFrame(n), entry = entryFrame(n);
  const aVid = videoOf(a), bVid = videoOf(b);
  const aDur = durationOf(a), bDur = durationOf(b);
  const p = n.plan;
  const missing = !a ? 'Ligue um vídeo ou imagem na entrada A'
    : !exit ? `Gere "${a.name}" primeiro`
    : !b ? 'Ligue um vídeo ou imagem na entrada B'
    : !entry ? `Gere "${b.name}" primeiro`
    : mode === 'edit' && (!aVid || !bVid) ? 'Edição precisa de vídeo em A e em B (com imagem use o modo IA)' : '';
  return `
    <div class="tr-strip">
      <div class="tr-frame ${exit ? '' : 'empty'}" style="background-image:url('${exit || ''}')"><span>A · ${esc(kindOf(a))}${n.cutAt != null ? ` · ${(+n.cutAt).toFixed(2)}s` : ''}</span></div>
      <div class="tr-mid"><b>${ic(TR_MODES[mode].icon)}</b><small>${mode === 'ai' ? `${n.bridgeDur}s` : ''}</small></div>
      <div class="tr-frame ${entry ? '' : 'empty'}" style="background-image:url('${entry || ''}')"><span>B · ${esc(kindOf(b))}${n.bIn != null ? ` · ${(+n.bIn).toFixed(2)}s` : ''}</span></div>
    </div>
    <div class="cv-meta"><span>A: ${a ? esc(a.name) : '—'}</span><span>B: ${b ? esc(b.name) : '—'}</span></div>
    <div class="tr-modes">${Object.entries(TR_MODES).map(([k, v]) =>
      `<button class="chip ${k === mode ? 'active' : ''}" data-mode="${k}" title="${esc(v.hint)}">${ic(v.icon)} ${esc(v.label)}</button>`).join('')}</div>

    ${mode === 'ai' ? `
      <textarea data-k="idea" rows="2" placeholder="Como a câmera vai de A para B? (opcional) Ex.: entra no vidro da garrafa e sai no bar">${esc(n.idea || '')}</textarea>
      <div class="cv-grid4"><label style="grid-column: span 4">Modelo${modelSelect('model', 'video', n.model, { i2v: true, end_frame: true })}</label>
      <label style="grid-column: span 2">Duração<select data-k="bridgeDur">${bridgeDurations(n).map((d) => `<option value="${d}" ${d == n.bridgeDur ? 'selected' : ''}>${d}s</option>`).join('')}</select></label>
      <label style="grid-column: span 2">Qualidade<select data-k="quality"><option value="draft" ${n.quality !== 'final' ? 'selected' : ''}>Rascunho</option><option value="final" ${n.quality === 'final' ? 'selected' : ''}>Final</option></select></label></div>
      ${bridgeModel(n) ? `<div class="cv-meta"><span>${esc(bridgeModel(n).label)}</span><span class="cv-cost">${costLabel(bridgeModel(n), n.bridgeDur)}</span></div>` : '<div class="cv-meta"><span class="err">Nenhum modelo com frame inicial + final liberado</span></div>'}`
    : `<div class="cv-grid4"><label style="grid-column: span 3">Tipo<select data-k="editKind">${Object.entries(state.catalog.transitions)
        .filter(([k]) => !['bridge', 'match_cut'].includes(k))
        .map(([k, v]) => `<option value="${k}" ${k === trKind(n) ? 'selected' : ''}>${esc(v.label)}</option>`).join('')}</select></label>
      <label>Duração<select data-k="duration"><option value="">auto</option>${[0.25, 0.4, 0.6, 0.8, 1.2].map((d) => `<option value="${d}" ${n.duration == d ? 'selected' : ''}>${d}s</option>`).join('')}</select></label></div>`}

    ${p?.camera_plan_pt ? `<div class="tr-explain"><b>${ic('route')} Como a câmera vai fazer</b><p>${esc(p.camera_plan_pt)}</p>
      ${(p.checks || []).filter((c) => !c.ok).map((c) => `<div class="warn">${ic('alert')} ${esc(c.text)}</div>`).join('')}
      ${p.bridge_prompt_en && mode === 'ai' ? `<details><summary>prompt técnico (${esc(p.source)})</summary>${esc(p.bridge_prompt_en)}</details>` : ''}</div>` : ''}

    ${n.output?.url ? `<div class="cv-meta"><span>${ic('check-circle', 'ok')} Transição gerada · ${(n.output.duration || 0).toFixed(1)}s</span><span>saída ● → continuar</span></div>
      <video class="cv-media" src="${n.output.url}" controls loop playsinline></video>` : ''}
    ${n.preview ? `<div class="cv-meta"><span>${ic('eye')} Prévia A → transição → B</span></div><video class="cv-media" src="${n.preview}" controls loop playsinline></video>` : ''}
    ${n.status && n.status !== 'idle' ? `<div class="cv-status">${statusLine(n)}</div>` : ''}
    ${missing ? `<div class="cv-meta"><span class="err">${esc(missing)}</span></div>` : ''}

    <div class="cv-actions">
      <button class="btn ghost" data-act="plan" ${exit && entry ? '' : 'disabled'}>${ic('bulb')} Explicar câmera</button>
      ${mode === 'ai' ? `<button class="btn primary" data-act="bridge" ${!missing && n.status !== 'running' ? '' : 'disabled'}>${ic('sparkles')} Gerar transição</button>` : ''}
      <button class="btn ghost" data-act="preview" title="Prévia A → transição → B" ${aVid || bVid ? '' : 'disabled'}>${ic('eye')}</button>
    </div>

    ${aVid || bVid ? `<details class="tr-fine"><summary>Ajuste fino: onde A termina / onde B começa</summary>
      ${aVid ? framePicker('a', aVid, aDur, n.cutAt ?? aDur ?? 5, 'A termina em') : ''}
      ${bVid ? framePicker('b', bVid, bDur, n.bIn ?? 0, 'B começa em') : ''}
    </details>` : ''}`;
}

function bindPicker(box, n) {
  const role = box.dataset.pick;
  const vid = box.querySelector('[data-role=scrub]');
  const range = box.querySelector('[data-role=pos]');
  const label = box.querySelector('[data-role=poslabel]');
  const seek = (t) => {
    const max = +range.max;
    t = Math.max(0, Math.min(max, t));
    range.value = t;
    vid.currentTime = Math.min(t, (vid.duration || max) - 0.01);
    label.textContent = `${t.toFixed(2)}s`;
  };
  vid.addEventListener('loadedmetadata', () => {
    if (!durationOf(role === 'a' ? trA(n) : trB(n))) range.max = vid.duration;
    seek(+range.value);
  });
  range.addEventListener('input', () => seek(+range.value));
  const f = 1 / 16;
  box.querySelector('[data-act=first]').onclick = () => seek(0);
  box.querySelector('[data-act=last]').onclick = () => seek(+range.max);
  box.querySelector('[data-act=back]').onclick = () => seek(+range.value - f);
  box.querySelector('[data-act=fwd]').onclick = () => seek(+range.value + f);
  box.querySelector('[data-act=use]').onclick = async () => {
    const t = +range.value;
    const src = role === 'a' ? trA(n) : trB(n);
    const r = await api('/api/media/frame', { url: videoOf(src), t });
    if (role === 'a') Object.assign(n, { frame: r, cutAt: t });
    else Object.assign(n, { bFrame: r, bIn: t });
    Object.assign(n, { preview: null, plan: null, output: null, status: 'idle' });
    outgoing(n.id).forEach(invalidate);
    renderNode(n);
    save();
    toast(role === 'a' ? `A termina em ${t.toFixed(2)}s: a transição sai deste frame` : `B começa em ${t.toFixed(2)}s: a transição chega neste frame`);
  };
}

function bindTransition(el, n) {
  el.querySelectorAll('[data-mode]').forEach((b) => b.addEventListener('click', () => {
    n.mode = b.dataset.mode;
    n.preview = null;
    save();
    renderNode(n);
  }));
  el.querySelectorAll('[data-k]').forEach((inp) => {
    const ev = inp.tagName === 'TEXTAREA' ? 'input' : 'change';
    inp.addEventListener(ev, () => {
      const k = inp.dataset.k;
      n[k] = k === 'bridgeDur' ? +inp.value : k === 'duration' ? (inp.value ? +inp.value : null) : inp.value;
      if (k === 'idea' || k === 'bridgeDur') n.plan = null; // ideia nova → explicação nova
      n.preview = null;
      save();
      if (inp.tagName === 'SELECT') renderNode(n);
    });
  });
  el.querySelectorAll('[data-pick]').forEach((box) => bindPicker(box, n));
  el.querySelector('[data-act=plan]').onclick = () => planTransition(n).catch((e) => toast(e.message));
  el.querySelector('[data-act=preview]').onclick = () => previewTransition(n).catch((e) => toast(e.message));
  el.querySelector('[data-act=bridge]')?.addEventListener('click', () => genBridge(n).catch((e) => toast(e.message)));
}

// ---------------- Ações ----------------

async function planTransition(n) {
  const a = trA(n), b = trB(n);
  if (!exitFrame(n) || !entryFrame(n)) throw new Error('Ligue A e B (com imagem ou vídeo pronto) primeiro');
  const btn = document.querySelector(`.cv-node[data-id="${n.id}"] [data-act=plan]`);
  if (btn) { btn.disabled = true; setBtn(btn, 'loader', 'olhando os frames…'); }
  try {
    n.plan = await api('/api/transition/plan', {
      a: { name: a.name, intent: a.intent || a.name, prompt: a.auto?.video_prompt, move: a.camAnalysis?.move,
        frame_url: exitFrame(n), duration: durationOf(a), cut_at: n.cutAt },
      b: { name: b.name, intent: b.intent || b.name, move: b.camAnalysis?.move, frame_url: entryFrame(n) },
      kind: trKind(n), duration: n.bridgeDur || 3, kit: kit(),
      idea: [n.idea, ...(n.feedback || []).map((f) => `Ajuste pedido na aprovação: ${f}`)].filter(Boolean).join('. '),
    });
    save();
    return n.plan;
  } finally {
    renderNode(n);
  }
}

async function genBridge(n) {
  const a = trA(n), b = trB(n);
  const start = exitFrame(n), end = entryFrame(n);
  if (!start) throw new Error(a ? `Gere "${a.name}" antes` : 'Ligue algo na entrada A');
  if (!end) throw new Error(b ? `Gere "${b.name}" antes` : 'Ligue algo na entrada B');
  if (!n.plan?.bridge_prompt_en) await planTransition(n);
  const job = await api('/api/render', {
    node_id: n.id, name: `Transição ${a.name} → ${b.name}`, gen: 'bridge', model: n.model || 'auto', duration: n.bridgeDur || 5,
    aspect: a.aspect || b.aspect || '16:9', quality: n.quality === 'final' ? 'final' : 'draft', prompt: n.plan.bridge_prompt_en,
    start_image_url: start, end_image_url: end, adapt: false,
  });
  Object.assign(n, { jobId: job.id, status: 'running', progress: 0, error: null, output: null, preview: null, stage: 'enviado' });
  renderNode(n);
  save();
  toast(`Gerando transição de ${n.bridgeDur}s: A → B`);
  await watchJob(n);
  if (n.status === 'done' && (videoOf(a) || videoOf(b))) await previewTransition(n).catch(() => {});
  return n;
}

async function previewTransition(n) {
  const a = trA(n), b = trB(n);
  const va = videoOf(a), vb = videoOf(b);
  const mode = trMode(n);
  if (mode === 'ai' && !n.output?.url) throw new Error('Gere a transição primeiro');
  if (mode === 'edit' && (!va || !vb)) throw new Error('Edição precisa de vídeo em A e B');
  const items = [];
  if (va) {
    const cut = n.cutAt ?? durationOf(a) ?? 5;
    items.push({ type: 'clip', url: va, start: Math.max(0, cut - 1.5), end: cut });
  }
  if (mode === 'ai') items.push({ type: 'clip', url: n.output.url }, ...(vb ? [{ type: 'transition', kind: 'dissolve', duration: 0.25 }] : []));
  else items.push({ type: 'transition', kind: trKind(n), duration: n.duration });
  if (vb) items.push({ type: 'clip', url: vb, start: n.bIn || 0, end: (n.bIn || 0) + 2 });
  toast('Montando prévia A → transição → B…');
  const r = await api('/api/montage', { items });
  n.preview = r.url;
  renderNode(n);
  save();
}

// ---------------- Inserção ----------------

function edgeMenu(edge, ev) {
  document.querySelector('.cv-edge-menu')?.remove();
  const a = byId(edge.from), b = byId(edge.to);
  const menu = document.createElement('div');
  menu.className = 'cv-edge-menu';
  menu.style.left = `${ev.clientX}px`;
  menu.style.top = `${ev.clientY}px`;
  const canInsert = a && b && a.type !== 'transition' && b.type !== 'transition' && b.type !== 'montage';
  menu.innerHTML = `${canInsert ? `<button data-m="ins">${ic('transition')} Inserir transição entre os dois</button>` : ''}<button data-m="del">${ic('x')} Remover ligação</button>`;
  document.body.appendChild(menu);
  const close = () => { menu.remove(); window.removeEventListener('mousedown', outside); };
  const outside = (e) => { if (!menu.contains(e.target)) close(); };
  setTimeout(() => window.addEventListener('mousedown', outside));
  menu.querySelector('[data-m=del]').onclick = () => {
    cv.edges = cv.edges.filter((x) => x.id !== edge.id);
    if (b) invalidate(b);
    close();
    renderAll();
    save();
  };
  menu.querySelector('[data-m=ins]')?.addEventListener('click', () => { close(); insertTransition(a, b); });
}

// A → T(entrada A) e B → T(entrada B). B deixa de depender de A.
function insertTransition(a, b) {
  const t = newTransition({ x: Math.round(Math.max(a.x, b.x) + 380), y: Math.round((a.y + b.y) / 2) + 40 });
  cv.edges = cv.edges.filter((e) => !(e.from === a.id && e.to === b.id));
  cv.nodes.push(t);
  cv.edges.push({ id: uid('e'), from: a.id, to: t.id, port: 'a' }, { id: uid('e'), from: b.id, to: t.id, port: 'b' });
  invalidate(b);
  renderAll();
  select(t.id);
  save();
  toast('Transição criada com A e B ligados: descreva a ideia (opcional) e clique em Gerar transição');
}

function addTransitionFromToolbar() {
  addNode(newTransition(centerWorld()));
  toast('Ligue um vídeo/imagem em A e outro em B (bolinhas à esquerda do card)');
}

const bridgeModel = (n) => chosenModel('video', n.model, { i2v: true, end_frame: true });

function bridgeDurations(n) {
  const d = bridgeModel(n)?.caps?.durations;
  const list = d?.length ? d : DEFAULT_DURATIONS;
  if (!list.includes(+n.bridgeDur)) n.bridgeDur = list[0];
  return list;
}
