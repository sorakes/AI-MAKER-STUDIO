// Storyboard: briefing curto -> plano (IA de texto) editável -> nós ligados no canvas:
//   ELENCO ──R──▶ IMAGEM ──▶ VÍDEO ──┐
//   ... (um par por plano) ... ──────┴──▶ CORTE
// Por padrão só cria os nós (custo zero). "Gerar tudo" usa o Rodar fluxo, depois da estimativa e da confirmação.

const SB_TIMES = [6, 10, 15, 20, 30, 45, 60];
const SB = { dlg: null, plan: null };

document.getElementById('cvStoryboard')?.addEventListener('click', openStoryboard);

const sbMoves = () => state.catalog?.camera?.moves || {};
const sbMode = (dlg) => dlg.querySelector('[name=sbMode]:checked').value;

function sbEstimate(shots, plan) {
  const images = shots.length;
  const seconds = Math.round(shots.reduce((t, s) => t + (+s.duration || 0), 0) * 10) / 10;
  const usd = images * plan.estimate.image_usd + seconds * plan.estimate.video_usd_per_s;
  return { images, seconds, tokens: Math.round(usd * plan.estimate.tokens_per_usd) };
}

function openStoryboard() {
  SB.dlg?.remove();
  SB.plan = null;
  const assets = state.assets || [];
  const dlg = document.createElement('dialog');
  dlg.className = 'sb-dialog';
  dlg.setAttribute('aria-labelledby', 'sbTitle');
  dlg.innerHTML = `
    <div class="sb-form">
      <div class="sb-head"><h2 id="sbTitle">Storyboard</h2><button type="button" class="cv-hbtn" data-sb="close" title="Fechar" aria-label="Fechar">${ic('x')}</button></div>
      <p class="cv-side-note">Descreva a cena em 1 ou 2 frases. Você revisa o plano antes de criar qualquer nó.</p>
      <label class="cv-field"><span class="cv-flabel">Briefing</span><textarea data-sb="brief" rows="3" maxlength="400" placeholder="Ex.: uma garrafa de água gelada numa praia ao pôr do sol, dois amigos brindando"></textarea></label>
      <div class="cv-row3">
        <label class="cv-field"><span class="cv-flabel">Planos</span><select data-sb="shots">${[3, 4, 5, 6].map((v) => opt(v, 4, `${v}`)).join('')}</select></label>
        <label class="cv-field"><span class="cv-flabel">Duração total</span><select data-sb="total">${SB_TIMES.map((v) => opt(v, 15, `${v}s`)).join('')}</select></label>
        <label class="cv-field"><span class="cv-flabel">Formato</span><select data-sb="aspect">${opt('16:9', '16:9')}${opt('9:16', '16:9')}</select></label>
      </div>
      <label class="cv-field"><span class="cv-flabel">Estilo (opcional)</span><input data-sb="style" maxlength="80" placeholder="Ex.: cinematográfico, cores quentes"></label>
      ${assets.length
        ? `<div class="cv-chip-row" aria-label="Inventário usado">${assets.map((a) => `<span class="cv-chip">${ic('users')} ${esc(a.name)}</span>`).join('')}</div><p class="cv-side-note">Personagens e produtos do inventário são reaproveitados pelo nome.</p>`
        : '<p class="cv-side-note">Sem personagens ou produtos salvos: a IA cria os nomes e você preenche depois.</p>'}
      <div class="sb-actions">
        <button type="button" class="btn ghost" data-sb="plan">${ic('sparkles')} Planejar</button>
        <span class="cv-side-note" data-sb="status" aria-live="polite"></span>
      </div>
      <div data-sb="plan-box" aria-live="polite"></div>
      <fieldset class="sb-mode">
        <legend>Depois de criar os nós</legend>
        <label><input type="radio" name="sbMode" value="nodes" checked> Só criar os nós (custo zero)</label>
        <label><input type="radio" name="sbMode" value="all"> Gerar tudo agora</label>
        <p class="cv-side-note" data-sb="estimate" aria-live="polite"></p>
        <label class="sb-confirm" hidden><input type="checkbox" data-sb="confirm"> Confirmo o custo estimado</label>
      </fieldset>
      <div class="sb-actions">
        <button type="button" class="btn ghost" data-sb="close">Fechar</button>
        <button type="button" class="btn primary" data-sb="create" disabled>${ic('plus')} Criar storyboard no canvas</button>
      </div>
    </div>`;
  document.body.appendChild(dlg);
  SB.dlg = dlg;
  dlg.querySelectorAll('[data-sb=close]').forEach((b) => b.addEventListener('click', () => dlg.close()));
  dlg.addEventListener('close', () => { dlg.remove(); if (SB.dlg === dlg) SB.dlg = null; });
  dlg.querySelector('[data-sb=plan]').addEventListener('click', () => planStoryboard(dlg));
  dlg.querySelector('[data-sb=create]').addEventListener('click', () => createStoryboard(dlg));
  dlg.querySelectorAll('[name=sbMode]').forEach((r) => r.addEventListener('change', () => refreshEstimate(dlg)));
  dlg.querySelector('[data-sb=confirm]').addEventListener('change', () => refreshCreate(dlg));
  dlg.showModal();
  dlg.querySelector('[data-sb=brief]').focus();
}

async function planStoryboard(dlg) {
  const brief = dlg.querySelector('[data-sb=brief]').value.trim();
  const status = dlg.querySelector('[data-sb=status]');
  if (brief.length < 10) { status.textContent = 'Escreva um briefing com pelo menos 10 caracteres'; return; }
  const btn = dlg.querySelector('[data-sb=plan]');
  btn.disabled = true;
  status.textContent = 'Montando o plano…';
  try {
    const aspect = dlg.querySelector('[data-sb=aspect]').value;
    const assets = (state.assets || []).slice(0, 20).map((a) => ({ name: a.name, kind: a.kind === 'product' ? 'produto' : 'personagem' }));
    SB.plan = { ...(await api('/api/storyboard/plan', { brief, shots: +dlg.querySelector('[data-sb=shots]').value,
      total: +dlg.querySelector('[data-sb=total]').value, aspect, style: dlg.querySelector('[data-sb=style]').value.trim(), assets })), aspect };
    status.textContent = '';
    renderPlan(dlg);
    refreshEstimate(dlg);
    refreshCreate(dlg);
  } catch (err) {
    status.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

function renderPlan(dlg) {
  const p = SB.plan;
  const moves = sbMoves();
  dlg.querySelector('[data-sb=plan-box]').innerHTML = `
    <div class="cv-plan-list">
      <div class="cv-plan-head"><b>${esc(p.title)}</b><span>${p.shots.length} planos</span></div>
      ${p.shots.map((s, i) => `
        <div class="cv-plan-shot sb-shot" data-i="${i}">
          <span class="n">${i + 1}</span>
          <div class="sb-fields">
            <label class="cv-field"><span class="cv-flabel">Título</span><input data-f="title" maxlength="80" value="${esc(s.title)}"></label>
            <label class="cv-field"><span class="cv-flabel">O que acontece</span><textarea data-f="description" rows="2" maxlength="400">${esc(s.description)}</textarea></label>
            <div class="sb-row">
              <label class="cv-field"><span class="cv-flabel">Duração (s)</span><input type="number" data-f="duration" min="2" max="8" step="0.5" value="${s.duration}"></label>
              <label class="cv-field"><span class="cv-flabel">Câmera</span><select data-f="camera">${Object.entries(moves).map(([k, m]) => opt(k, s.camera, m.label)).join('')}</select></label>
            </div>
            <details class="sb-prompts"><summary>Prompts (em inglês)</summary>
              <label class="cv-field"><span class="cv-flabel">Imagem (prompt)</span><textarea data-f="image_prompt" rows="2" maxlength="600">${esc(s.image_prompt)}</textarea></label>
              <label class="cv-field"><span class="cv-flabel">Vídeo (prompt)</span><textarea data-f="video_prompt" rows="2" maxlength="600">${esc(s.video_prompt)}</textarea></label>
            </details>
            ${s.refs.length ? `<div class="cv-chip-row">${s.refs.map((r) => `<span class="cv-chip">${ic('users')} ${esc(r)}</span>`).join('')}</div>` : ''}
          </div>
          <button type="button" class="cv-hbtn" data-remove="${i}" title="Tirar este plano" aria-label="Tirar este plano">${ic('x')}</button>
        </div>`).join('')}
    </div>`;
  dlg.querySelectorAll('[data-f]').forEach((el) => el.addEventListener('input', () => {
    const s = SB.plan.shots[+el.closest('.sb-shot').dataset.i];
    const f = el.dataset.f;
    if (f === 'duration') s.duration = Math.min(8, Math.max(2, +el.value || 2));
    else if (f === 'camera') { s.camera = el.value; s.camera_hint = moves[el.value]?.prompt || s.camera_hint; }
    else s[f] = el.value;
    refreshEstimate(dlg);
    refreshCreate(dlg);
  }));
  dlg.querySelectorAll('[data-remove]').forEach((b) => b.addEventListener('click', () => {
    SB.plan.shots.splice(+b.dataset.remove, 1);
    renderPlan(dlg);
    refreshEstimate(dlg);
    refreshCreate(dlg);
  }));
}

function refreshEstimate(dlg) {
  const el = dlg.querySelector('[data-sb=estimate]');
  const confirm = dlg.querySelector('.sb-confirm');
  const all = sbMode(dlg) === 'all';
  confirm.hidden = !all;
  if (!SB.plan) { el.textContent = ''; return; }
  if (!all) { el.textContent = 'Criar os nós não gasta nada.'; return; }
  const e = sbEstimate(SB.plan.shots, SB.plan);
  el.textContent = `Estimativa: ${e.images} imagens e ${e.seconds} s de vídeo, cerca de ${fmtTok(e.tokens)}.`;
}

function refreshCreate(dlg) {
  const p = SB.plan;
  const ok = !!p && p.shots.length > 0
    && p.shots.every((s) => s.title.trim() && s.description.trim() && s.image_prompt.trim() && s.video_prompt.trim());
  const blocked = sbMode(dlg) === 'all' && !dlg.querySelector('[data-sb=confirm]').checked;
  dlg.querySelector('[data-sb=create]').disabled = !ok || blocked;
}

function createStoryboard(dlg) {
  const p = SB.plan;
  if (!p?.shots.length) return;
  const generate = sbMode(dlg) === 'all';
  if (generate && !dlg.querySelector('[data-sb=confirm]').checked) return toast('Confirme o custo estimado para gerar tudo');
  const aspect = p.aspect;
  const x0 = cv.nodes.length ? Math.max(...cv.nodes.map((n) => n.x)) + 520 : 120;
  const y0 = 120;
  const cast = new Map();  // personagem/produto: um nó por nome, ligado nas imagens e nos vídeos onde aparece
  const castFor = (name, desc) => {
    const key = name.toLowerCase();
    if (!cast.has(key)) {
      const asset = (state.assets || []).find((a) => a.name.toLowerCase() === key);  // inventário: reaproveita pelo nome
      const node = newCast({ x: x0 - 380, y: y0 + cast.size * 230, name: asset ? asset.name : name,
        castKind: asset ? (asset.kind === 'product' ? 'produto' : 'personagem') : 'personagem',
        assetId: asset?.id || '', desc: asset ? '' : desc });
      cv.nodes.push(node);
      cast.set(key, node);
    }
    return cast.get(key);
  };
  const pairs = p.shots.map((s, i) => {
    const col = x0 + i * 460;
    const img = newShot({ x: col, y: y0 + 240, gen: 'image', name: `${s.title} · Imagem`, intent: s.image_prompt, aspect, imageModel: 'auto' });
    const vid = newShot({ x: col, y: y0 + 540, name: `${s.title} · Vídeo`, intent: s.video_prompt, duration: s.duration, aspect, cameraHint: s.camera_hint });
    cv.nodes.push(img, vid);
    cv.edges.push({ id: uid('e'), from: img.id, to: vid.id });
    s.refs.forEach((name) => {
      const c = castFor(name, s.description);
      cv.edges.push({ id: uid('e'), from: c.id, to: img.id, port: 'ref' });
      cv.edges.push({ id: uid('e'), from: c.id, to: vid.id, port: 'ref' });
    });
    return { vid };
  });
  const mont = { id: uid('mont'), type: 'montage', name: `Corte · ${p.title}`, x: x0 + p.shots.length * 460, y: y0 + 540 };
  cv.nodes.push(mont);
  pairs.forEach(({ vid }) => cv.edges.push({ id: uid('e'), from: vid.id, to: mont.id }));
  renderAll();
  save();
  fitView();
  dlg.close();
  toast(generate ? `${pairs.length} planos criados. Gerando as imagens e os vídeos…`
    : `${pairs.length} planos criados. Ajuste e clique em Rodar fluxo para gerar.`);
  if (generate) runAll().catch((err) => toast(err.message));
}
