// nó Aprovação
//
// Visualiza grande a saída do nó anterior e, com "Pausar aqui" ligado, segura o Rodar fluxo
// até alguém aprovar. Reprovar com comentário: o comentário entra no prompt automático do nó
// anterior (DeepSeek precisa atender) e ele é gerado de novo.
// O nó é transparente: quem vem depois recebe exatamente a mídia que chegou nele.

const through = (n) => {
  const seen = new Set();
  while (n?.type === 'review' && !seen.has(n.id)) { seen.add(n.id); n = incoming(n.id)[0]; }
  return n;
};

function newReviewNode(pos) {
  return { id: uid('review'), type: 'review', name: 'Aprovação', gate: true, approval: null, rejections: [], comment: '', ...pos };
}

// Chave do que está sendo aprovado: se a mídia de cima mudar, a aprovação deixa de valer.
function reviewKey(r) {
  const up = incoming(r.id)[0];
  return videoOf(up) || frameOf(up) || null;
}

const isApproved = (r) => !!r.approval && r.approval.key === reviewKey(r);

function reviewState(r) {
  if (!reviewKey(r)) return 'empty';
  if (isApproved(r)) return 'approved';
  if (r.waiting) return 'waiting';
  return 'pending';
}

// Primeira aprovação pendente (com pausa ligada) em qualquer caminho até o nó n.
function blockingReview(n, seen = new Set()) {
  if (!n || seen.has(n.id)) return null;
  seen.add(n.id);
  for (const e of cv.edges.filter((x) => x.to === n.id)) {
    const src = byId(e.from);
    if (!src) continue;
    if (src.type === 'review' && src.gate !== false && reviewKey(src) && !isApproved(src)) return src;
    const deeper = blockingReview(src, seen);
    if (deeper) return deeper;
  }
  return null;
}

function pauseAt(r) {
  r.waiting = true;
  renderNode(r);
  cv.zoom = Math.max(cv.zoom, 0.7);
  const b = $('#cvBoard').getBoundingClientRect();
  cv.pan = { x: b.width / 2 - (r.x + 210) * cv.zoom, y: 80 - r.y * cv.zoom };
  applyView();
  select(r.id);
  save();
  $('#cvRunAll').dataset.paused = '1';
  setBtn($('#cvRunAll'), 'play', 'Continuar fluxo');
  toast(`Fluxo pausado em "${r.name}": aprove para continuar`);
}

function reviewBody(r) {
  const up = through(incoming(r.id)[0]);
  const video = videoOf(incoming(r.id)[0]);
  const image = !video && frameOf(incoming(r.id)[0]);
  const st = reviewState(r);
  const prompt = up?.auto?.video_prompt || up?.auto?.keyframe_prompt || up?.plan?.bridge_prompt_en;
  const badge = {
    empty: '<span class="rv-badge">sem mídia ainda</span>',
    pending: '<span class="rv-badge pending">aguardando revisão</span>',
    waiting: `<span class="rv-badge waiting">${ic('pause')} fluxo pausado aqui</span>`,
    approved: `<span class="rv-badge ok">${ic('check')} aprovado ${r.approval ? new Date(r.approval.at).toLocaleTimeString(LOC, { hour: '2-digit', minute: '2-digit' }) : ''}</span>`,
  }[st];
  return `
    <div class="rv-top">${badge}<span class="rv-ver">${r.rejections.length ? `versão ${r.rejections.length + 1}` : ''}</span></div>
    ${video ? `<video class="cv-media rv-media" src="${video}" controls loop playsinline></video>`
      : image ? `<img class="cv-media rv-media" src="${image}" alt="">`
      : `<div class="rv-empty">${up ? `Esperando "${esc(up.name)}" gerar` : 'Ligue a saída de um nó aqui'}</div>`}
    ${up ? `<div class="cv-meta"><span>De: ${esc(up.name)}</span><span>${video ? 'vídeo' : image ? 'imagem' : ''}</span></div>` : ''}
    ${prompt ? `<details class="cv-auto"><summary>Prompt técnico usado</summary>${esc(prompt)}</details>` : ''}
    ${r.rejections.length ? `<details class="cv-auto"><summary>Reprovações anteriores (${r.rejections.length})</summary>${r.rejections.map((x, i) =>
      `<div class="rv-hist"><b>v${i + 1}</b> ${esc(x.comment || 'sem comentário')}</div>`).join('')}</details>` : ''}
    <label class="inline rv-gate"><input type="checkbox" data-k="gate" ${r.gate !== false ? 'checked' : ''}> Pausar o fluxo aqui até aprovar</label>
    <textarea data-k="comment" rows="2" placeholder="Comentário / ajuste pedido (vai para a IA ao reprovar). Ex.: logo maior, luz mais quente, câmera mais lenta">${esc(r.comment || '')}</textarea>
    <div class="cv-actions">
      <button class="btn rv-reject" data-act="reject" ${st === 'empty' || !up || up.type === 'media' ? 'disabled' : ''} title="Gera de novo o nó anterior levando o comentário">${ic('x')} Reprovar e refazer</button>
      <button class="btn primary" data-act="approve" ${st === 'empty' || st === 'approved' ? 'disabled' : ''}>${ic('check')} Aprovar${r.waiting ? ' e continuar' : ''}</button>
    </div>`;
}

function bindReview(el, r) {
  el.querySelector('[data-k=gate]').onchange = (e) => { r.gate = e.target.checked; save(); };
  el.querySelector('[data-k=comment]').oninput = (e) => { r.comment = e.target.value; save(); };
  el.querySelector('[data-act=approve]').onclick = () => {
    const resume = r.waiting;
    r.approval = { key: reviewKey(r), at: Date.now(), comment: r.comment || '' };
    r.waiting = false;
    r.comment = '';
    renderNode(r);
    save();
    toast(`"${r.name}" aprovado`);
    if (resume) runAll();
  };
  el.querySelector('[data-act=reject]').onclick = () => rejectReview(r).catch((e) => toast(e.message));
}

async function rejectReview(r) {
  const src = through(incoming(r.id)[0]);
  if (!src || src.type === 'media') throw new Error('Só dá para refazer nós que geram (vídeo, imagem, transição)');
  const comment = (r.comment || '').trim();
  r.rejections.push({ key: reviewKey(r), comment, at: Date.now() });
  r.approval = null;
  r.comment = '';
  // O comentário vira instrução obrigatória no próximo prompt automático.
  src.feedback = [...(src.feedback || []), comment].filter(Boolean);
  if (src.type === 'transition') { src.plan = null; } else { src.autoKey = null; }
  renderNode(r);
  save();
  toast(`Reprovado: refazendo "${src.name}"${comment ? ' com o seu comentário' : ''}`);
  if (src.type === 'transition') await genBridge(src); else await runShot(src);
  r.waiting = r.gate !== false;
  renderNode(r);
  save();
  if (src.status === 'done') toast(`Nova versão de "${src.name}" pronta para revisão`);
}
