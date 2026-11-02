// Editar vídeo: um trecho (até 10 s) de um vídeo ligado na entrada é editado por quadros e remontado com o resto.
// Aproximação honesta: cada quadro vai ao modelo de imagem. Estimativa antes; confirmação acima de US$ 0,25.

const VEDIT_PRESETS = [['none', 'Sem preset'], ['anime', 'Estilo anime'], ['night', 'Dia → noite'], ['sunset', 'Céu por pôr do sol'],
  ['background', 'Trocar o fundo'], ['color', 'Mudar a cor de um elemento']];
const VEDIT_STRENGTH = [['low', 'Leve'], ['medium', 'Média'], ['high', 'Forte']];
const VEDIT_CONFIRM_USD = 0.25;

function newVideoEdit(pos) {
  return { id: uid('vedit'), type: 'videoedit', name: t('Editar vídeo'), prompt: '', preset: 'none', strength: 'medium',
    start: 0, end: 3, preserveAudio: true, quote: null, status: 'idle', output: null, takes: [], jobId: null, error: null, ...pos };
}

// Chave da estimativa: se mudar algo que custa, a estimativa antiga não vale.
const veditKey = (n) => JSON.stringify([n.start, n.end, n.preset, n.prompt, n.strength, videoOf(incoming(n.id)[0]) || '']);
const veditFresh = (n) => !!n.quote && n.quote.key === veditKey(n);

function videoEditBody(n) {
  const src = videoOf(incoming(n.id)[0]);
  const busy = n.status === 'running';
  const q = veditFresh(n) ? n.quote : null;
  const span = Math.round((n.end - n.start) * 10) / 10;
  const spanBad = !(n.end > n.start) || span > 10;
  return `
    ${previewBlock(n, { placeholder: { icon: 'video', text: src ? '' : 'Ligue um vídeo na entrada' } })}
    ${src ? '' : `<div class="cv-meta"><span class="err">${ic('alert')} Ligue um vídeo na entrada</span></div>`}
    <div class="cv-warn">${ic('alert')} <span>Edição por quadros (aproximação): cada quadro é editado por IA e remontado. Pode haver tremidos.</span></div>
    ${field('O que mudar', `<textarea data-k="prompt" rows="2" maxlength="300" placeholder="Ex.: troque o céu por pôr do sol">${esc(n.prompt)}</textarea>`)}
    <div class="cv-row3">
      ${field('Preset', `<select data-k="preset">${VEDIT_PRESETS.map(([v, l]) => opt(v, n.preset, l)).join('')}</select>`)}
      ${field('Força', `<select data-k="strength">${VEDIT_STRENGTH.map(([v, l]) => opt(v, n.strength, l)).join('')}</select>`)}
      <div class="vedit-trim" role="group" aria-label="Trecho a editar">
        <label class="cv-field"><span class="cv-flabel">Início (s)</span><input type="number" min="0" step="0.5" value="${n.start}" data-k="start" aria-label="Início do trecho (segundos)"></label>
        <label class="cv-field"><span class="cv-flabel">Fim (s)</span><input type="number" min="0" step="0.5" value="${n.end}" data-k="end" aria-label="Fim do trecho (segundos)"></label>
      </div>
    </div>
    <label class="vedit-check"><input type="checkbox" data-k="preserveAudio" ${n.preserveAudio ? 'checked' : ''}> Manter o áudio original</label>
    <div class="cv-meta"><span>${spanBad ? 'O trecho precisa ter de 0 a 10 s.' : `Trecho de ${span} s.`}</span></div>
    ${q ? `<div class="cv-meta"><span>Estimativa: ${q.frames} quadros, cerca de ${fmtTok(q.tokens)}.</span></div>` : ''}
    ${q?.confirm ? `<label class="vedit-check"><input type="checkbox" data-role="vedit-confirm"> Confirmo o custo estimado (acima de US$ ${VEDIT_CONFIRM_USD.toFixed(2)})</label>` : ''}
    ${n.status === 'error' ? `<div class="cv-error">${ic('x-circle')}<span>${esc(n.error || 'erro')}</span></div>` : ''}
    <div class="cv-foot">
      <button class="btn ghost" data-act="vedit-quote" ${busy || !src || spanBad ? 'disabled' : ''}>${ic('sparkles')} Ver custo</button>
      <button class="btn primary" data-act="vedit-run" ${busy || !src || spanBad || !(n.prompt.trim() || n.preset !== 'none') ? 'disabled' : ''}>${ic('video')} ${n.output ? 'Editar de novo' : 'Editar trecho'}</button>
    </div>`;
}

function bindVideoEdit(el, n) {
  el.querySelectorAll('[data-k]').forEach((inp) => {
    const k = inp.dataset.k;
    const ev = inp.tagName === 'TEXTAREA' || inp.type === 'number' ? 'input' : 'change';
    inp.addEventListener(ev, () => {
      if (inp.type === 'checkbox') n[k] = inp.checked;
      else n[k] = ['start', 'end'].includes(k) ? +inp.value : inp.value;
      save();
      if (['preset', 'start', 'end'].includes(k)) renderNode(n);
    });
  });
  el.querySelector('[data-act=vedit-quote]')?.addEventListener('click', () => quoteVideoEdit(n).catch((err) => toast(err.message)));
  el.querySelector('[data-act=vedit-run]')?.addEventListener('click', () => runVideoEdit(n).catch((err) => toast(err.message)));
}

function veditRequest(n) {
  return { node_id: n.id, name: n.name, source_url: videoOf(incoming(n.id)[0]), prompt: n.prompt || '', preset: n.preset,
    strength: n.strength, start: +n.start, end: +n.end, preserve_audio: !!n.preserveAudio };
}

async function quoteVideoEdit(n) {
  const q = await api('/api/video/edit/quote', veditRequest(n));
  n.quote = { ...q, key: veditKey(n), tokens: Math.round(q.usd * q.tokens_per_usd) };
  renderNode(n);
  return n.quote;
}

async function runVideoEdit(n) {
  if (n.status === 'running') return;
  if (!videoOf(incoming(n.id)[0])) return toast('Ligue um vídeo na entrada');
  const q = veditFresh(n) ? n.quote : await quoteVideoEdit(n);
  if (q.confirm && !document.querySelector(`.cv-node[data-id="${n.id}"] [data-role=vedit-confirm]`)?.checked) {
    return toast('Confirme o custo estimado para editar');
  }
  let job;
  try {
    job = await api('/api/video/edit', veditRequest(n));
  } catch (err) {
    Object.assign(n, { status: 'error', error: err.message });
    renderNode(n);
    throw err;
  }
  Object.assign(n, { jobId: job.id, status: 'running', progress: 0, error: null, cost: null, stage: 'enviado' });
  renderNode(n);
  save();
  toast(`${n.name}: editando os quadros`);
  return watchJob(n);
}
