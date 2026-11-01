// Criação: nó Roteiro (brief -> planos no canvas) e nó Elenco/Produto (consistência entre planos).
//
//   ELENCO ●──R──▶ GERAR VÍDEO / GERAR IMAGEM   (fotos + descrição entram em todo plano ligado)
//   ELENCO ●────▶ ROTEIRO ── Criar planos ──▶ cria os planos já ligados ao elenco e à montagem

const CAMERA_HINTS = {
  static: 'Travada', push: 'Push in sutil', dolly_in: 'Dolly in', reveal: 'Reveal (abre revelando)', truck: 'Travelling lateral',
  pan: 'Pan', tilt: 'Tilt up', crane: 'Grua', orbit: 'Orbit', push_pan: 'Push + pan', hero: 'Hero contra-plongée',
  crash: 'Crash zoom', vertigo: 'Dolly zoom', whip: 'Whip pan', roll: 'Roll / dutch', handheld: 'Câmera na mão',
};

const CAST_KINDS = { personagem: 'Personagem', produto: 'Produto', logo: 'Logo / marca', estilo: 'Estilo / ambiente' };

function newBrief(pos) {
  return { id: uid('brief'), type: 'brief', name: t('Roteiro'), brief: '', total: 15, aspect: '16:9', chain: false, plan: null, ...pos };
}

function newCast(pos) {
  return { id: uid('cast'), type: 'cast', name: t('Elenco'), castKind: 'produto', desc: '', refs: [], ...pos };
}

// Item do estúdio (Personagens/Produtos) escolhido no nó.
const assetOf = (c) => (c.assetId ? (state.assets || []).find((a) => a.id === c.assetId) : null);

// Imagens que o elenco manda como referência: as marcadas "usar" do item (ou as referências dele) + extras do nó.
// compact = para vídeo/imagem do Video Studio: UMA imagem (a spritesheet salva, senão a frente) em vez de todas.
function castImages(c, compact = false) {
  const a = assetOf(c);
  if (compact && a) {
    const used = a.images.filter((i) => i.use);
    const one = used.find((i) => i.view === 'spritesheet') || used.find((i) => i.view === 'front') || used[0] || null;
    const extra = (c.refs || []).map((r) => r.url);
    if (one) return [...new Set([one.url, ...extra])];
  }
  const fromAsset = a ? (a.images.filter((i) => i.use).map((i) => i.url).concat(a.images.some((i) => i.use) ? [] : a.refs.map((r) => r.url))) : [];
  return [...new Set([...fromAsset, ...(c.refs || []).map((r) => r.url)])];
}

function castInfo(c) {
  const a = assetOf(c);
  if (!a) return { name: c.name, kind: CAST_KINDS[c.castKind] || c.castKind, desc: c.desc };
  const attrs = Object.entries(a.attributes || {}).filter(([, v]) => String(v).trim()).map(([k, v]) => `${k}: ${v}`).join('; ');
  return { name: a.name, kind: a.kind === 'character' ? 'Personagem' : 'Produto', desc: [a.description, attrs, c.desc].filter(Boolean).join('. ') };
}

// Elenco ligado a um nó (porta R dos geradores ou entrada do Roteiro).
function castNodesOf(n) {
  return cv.edges.filter((e) => e.to === n.id).map((e) => byId(e.from)).filter((s) => s?.type === 'cast');
}

// Para o prompt: nome, tipo, descrição e quais "imagem N" mostram cada elemento.
function castOf(n) {
  const all = refsOf(n);
  return castNodesOf(n).map((c) => ({
    ...castInfo(c),
    images: castImages(c, true).map((u) => all.indexOf(u) + 1).filter((i) => i > 0),
  }));
}

function maxShotSeconds() {
  const d = (state.models.video || []).flatMap((m) => m.caps?.durations || []);
  return d.length ? Math.max(...d) : 5;
}

// ---------------- Elenco / produto ----------------

function castBody(n) {
  const a = assetOf(n);
  const imgs = castImages(n);
  const items = (state.assets || []).filter((x) => x.saved || x.id === n.assetId);
  const group = (kind, label) => {
    const list = items.filter((x) => x.kind === kind);
    return list.length ? `<optgroup label="${label}">${list.map((x) => opt(x.id, n.assetId, esc(x.name))).join('')}</optgroup>` : '';
  };
  return `
    <div class="cv-cast-strip">${imgs.length ? imgs.slice(0, 4).map((u) => `<div style="background-image:url('${u}')"></div>`).join('')
      : `<div class="empty">${ic('users')}<span>${a ? 'Salve o item na aba dele (nó Salvar)' : 'Escolha um personagem/produto salvo ou envie fotos'}</span></div>`}</div>
    ${field('Do estúdio', `<select data-k="assetId"><option value="">— manual (preencher aqui) —</option>${group('character', 'Personagens')}${group('product', 'Produtos')}</select>`)}
    ${a ? `<div class="cv-asset-info"><b>${esc(a.name)}</b><p>${esc(a.description || 'sem descrição')}</p>
        <a href="#${a.kind}" class="btn small ghost" data-act="openasset">${ic('external')} Editar em ${a.kind === 'character' ? 'Personagens' : 'Produtos'}</a></div>
      ${field('Nota extra para este vídeo (opcional)', `<textarea data-k="desc" rows="2" placeholder="Ex.: aqui ela está de óculos escuros">${esc(n.desc)}</textarea>`)}`
    : `<div class="cv-row2">
      ${field('Nome (como chamar no roteiro)', `<input data-k="name" value="${esc(n.name)}" placeholder="Ex.: Ana, Garrafa Verde">`)}
      ${field('Tipo', `<select data-k="castKind">${Object.entries(CAST_KINDS).map(([k, v]) => opt(k, n.castKind, v)).join('')}</select>`)}
    </div>
    ${field('Descrição visual fixa', `<textarea data-k="desc" rows="3" placeholder="Ex.: mulher de 30 anos, cabelo cacheado curto, jaqueta jeans; ou: garrafa de vidro verde, rótulo prata com estrela vermelha">${esc(n.desc)}</textarea>`)}`}
    ${refsBlock(n, a ? 'Fotos extras só para este vídeo (opcional).' : 'Fotos de frente/perfil, do produto em vários ângulos. Quanto mais, mais consistente.')}
    <div class="cv-hint-box">${ic('route')}<span>Ligue a saída ● na porta <b>R</b> dos geradores (ou no Roteiro): o mesmo ${n.castKind === 'personagem' ? 'personagem' : 'elemento'} aparece igual em todos os planos.</span></div>`;
}

function bindCast(el, n) {
  el.querySelectorAll('[data-k]').forEach((inp) => {
    inp.addEventListener(inp.tagName === 'SELECT' ? 'change' : 'input', () => {
      n[inp.dataset.k] = inp.value;
      if (inp.dataset.k === 'assetId') { const a = assetOf(n); if (a) n.name = a.name; }
      if (inp.dataset.k === 'name') el.querySelector('.cv-htext .title').textContent = inp.value;
      cv.edges.filter((e) => e.from === n.id).forEach((e) => { const t = byId(e.to); if (t) t.autoKey = null; });
      save();
      if (inp.tagName === 'SELECT') renderNode(n);
    });
  });
  bindRefs(el, n);
  el.querySelector('[data-act=openasset]')?.addEventListener('click', (e) => { e.preventDefault(); focusAsset(n.assetId); });
}

// ---------------- Roteiro ----------------

function briefBody(n) {
  const cast = castNodesOf(n);
  const p = n.plan;
  const camName = (id) => CAMERA_HINTS[id] || id;
  const castName = (c) => castInfo(c).name;
  return `
    ${field('Brief do comercial', `<textarea data-k="brief" rows="5" placeholder="Ex.: comercial de verão de 15s para redes sociais. A garrafa gelada como herói, amigos brindando ao pôr do sol, fechar com packshot e assinatura da marca.">${esc(n.brief)}</textarea>`)}
    <div class="cv-row3">
      ${field('Duração total', `<select data-k="total">${[6, 10, 15, 20, 30, 45, 60].map((d) => opt(d, n.total, `${d}s`)).join('')}</select>`)}
      ${field('Formato', `<select data-k="aspect">${['16:9', '9:16', '1:1'].map((a) => opt(a, n.aspect)).join('')}</select>`)}
      ${field('Planos', `<select data-k="chain">${opt('0', n.chain ? '1' : '0', 'Cortes')}${opt('1', n.chain ? '1' : '0', 'Contínuos')}</select>`)}
    </div>
    <div class="cv-chip-row">${cast.length ? cast.map((c) => `<span class="cv-chip">${ic('users')} ${esc(castName(c))}</span>`).join('')
      : '<span class="cv-side-note">Dica: ligue nós de Elenco/Produto aqui para manter personagem e produto iguais.</span>'}</div>
    ${p ? `<div class="cv-plan-list">
        <div class="cv-plan-head"><b>${esc(p.title || 'Decupagem')}</b><span>${p.shots.length} planos · ${p.total}s · ${esc(p.source)}</span></div>
        ${p.shots.map((s, i) => `<div class="cv-plan-shot">
          <span class="n">${i + 1}</span>
          <div><b>${esc(s.name)}</b><small>${s.duration}s · ${esc(camName(s.camera))}</small><p>${esc(s.intent)}</p>${s.why ? `<p class="why">${esc(s.why)}</p>` : ''}</div>
          <button class="cv-hbtn" data-del="${i}" title="Tirar este plano">${ic('x')}</button>
        </div>`).join('')}
        ${p.notes ? `<p class="cv-side-note">${esc(p.notes)}</p>` : ''}
        ${p.compliance_note ? `<p class="cv-warn">${ic('alert')} ${esc(p.compliance_note)}</p>` : ''}
      </div>` : ''}
    <div class="cv-foot">
      <button class="btn ghost" data-act="plan" ${n.busy ? 'disabled' : ''}>${ic(n.busy ? 'loader' : 'sparkles', n.busy ? 'spin' : '')} ${p ? 'Replanejar' : 'Planejar'}</button>
      <button class="btn primary" data-act="create" ${p?.shots.length ? '' : 'disabled'}>${ic('plus')} Criar ${p?.shots.length || ''} planos no canvas</button>
    </div>`;
}

function bindBrief(el, n) {
  el.querySelectorAll('[data-k]').forEach((inp) => {
    inp.addEventListener(inp.tagName === 'SELECT' ? 'change' : 'input', () => {
      n[inp.dataset.k] = inp.dataset.k === 'total' ? +inp.value : inp.dataset.k === 'chain' ? inp.value === '1' : inp.value;
      save();
    });
  });
  el.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => {
    n.plan.shots.splice(+b.dataset.del, 1);
    n.plan.total = +n.plan.shots.reduce((t, s) => t + s.duration, 0).toFixed(1);
    save();
    renderNode(n);
  }));
  el.querySelector('[data-act=plan]').onclick = () => planBrief(n);
  el.querySelector('[data-act=create]').onclick = () => createFromPlan(n);
}

async function planBrief(n) {
  if (!n.brief.trim()) return toast('Escreva o brief primeiro');
  n.busy = true;
  renderNode(n);
  try {
    n.plan = await api('/api/director/plan', {
      brief: n.brief, duration: n.total, aspect: n.aspect, max_shot: maxShotSeconds(), kit: kit(),
      cast: castNodesOf(n).map((c) => castInfo(c)),
    });
    if (n.plan.title && n.name === 'Roteiro') n.name = n.plan.title;
    toast(`Decupagem pronta: ${n.plan.shots.length} planos`);
  } catch (err) { toast(err.message); } finally {
    n.busy = false;
    save();
    renderNode(n);
  }
}

function createFromPlan(n) {
  const shots = n.plan?.shots || [];
  if (!shots.length) return;
  const cast = castNodesOf(n);
  const x0 = n.x + 460, y0 = n.y;
  const made = shots.map((s, i) => {
    const node = newShot({ x: x0 + i * 420, y: y0 });
    markSpawn(node.id, 120 + i * 140);
    Object.assign(node, { name: s.name, intent: s.intent, duration: s.duration, aspect: n.aspect, cameraHint: CAMERA_HINTS[s.camera] || null });
    cv.nodes.push(node);
    cast.forEach((c) => cv.edges.push({ id: uid('e'), from: c.id, to: node.id, port: 'ref' }));
    return node;
  });
  if (n.chain) made.slice(1).forEach((node, i) => cv.edges.push({ id: uid('e'), from: made[i].id, to: node.id }));
  const mont = { id: uid('mont'), type: 'montage', name: n.plan.title ? `Corte · ${n.plan.title}` : 'Corte final', x: x0 + shots.length * 420 + 20, y: y0 };
  markSpawn(mont.id, 120 + shots.length * 140);
  cv.nodes.push(mont);
  (n.chain ? [made[made.length - 1]] : made).forEach((node) => cv.edges.push({ id: uid('e'), from: node.id, to: mont.id }));
  renderAll();
  save();
  fitView();
  toast(`${made.length} planos criados${cast.length ? ` com ${cast.map((c) => castInfo(c).name).join(', ')}` : ''}. Ajuste e clique em Rodar fluxo.`);
}

// ---------------- Variações ----------------

// Versões extras do mesmo nó rodando em paralelo: cada uma vira uma versão (v2, v3...) no nó.
function watchExtra(n, jobId) {
  const tick = async () => {
    let j;
    try { j = await api(`/api/jobs/${jobId}`); } catch { return setTimeout(tick, 4000); }
    if (j.status === 'done') {
      addTake(n, j.media);
      if (!n.output) Object.assign(n, { output: j.media, status: 'done' });
      renderNode(n);
      save();
      return toast(`${n.name}: nova versão pronta (${(n.takes || []).length})`);
    }
    if (['error', 'interrupted'].includes(j.status)) return toast(`${n.name}: uma variação falhou (${j.error || j.status})`);
    setTimeout(tick, 2500);
  };
  tick();
}
