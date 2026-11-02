// Performance: o personagem da imagem (entrada I) fala um texto com expressão. O modelo gera a fala e a boca
// (voz própria). Um áudio ligado da node Áudio só define a duração: não é sincronizado.

const PERF_ASPECTS = [['16:9', '16:9'], ['9:16', '9:16'], ['1:1', '1:1']];
const PERF_CONFIRM_USD = 0.25;

function newPerformance(pos) {
  return { id: uid('perf'), type: 'performance', name: 'Performance', text: '', motion: '', aspect: '16:9',
    quote: null, status: 'idle', output: null, takes: [], jobId: null, error: null, ...pos };
}

const perfKey = (n) => JSON.stringify([n.text, n.motion, n.aspect, upscaleSource(n) || '', incomingAudioUrl(n) || '']);
const perfFresh = (n) => !!n.quote && n.quote.key === perfKey(n);

function perfRequest(n) {
  return { node_id: n.id, name: n.name, image_url: upscaleSource(n), text: n.text.trim(), motion: n.motion || '',
    aspect: n.aspect, audio_url: incomingAudioUrl(n) };
}

function performanceBody(n) {
  const src = upscaleSource(n);
  const audio = incomingAudioUrl(n);
  const busy = n.status === 'running';
  const q = perfFresh(n) ? n.quote : null;
  return `
    ${previewBlock(n, { placeholder: { icon: 'user', text: src ? '' : 'Ligue a imagem do personagem na entrada' } })}
    ${src ? '' : `<div class="cv-meta"><span class="err">${ic('alert')} Ligue a imagem do personagem na entrada</span></div>`}
    <div class="cv-warn perf-limit">${ic('alert')} <span><b>Limitação:</b> a fala e a boca são geradas pelo modelo, com voz própria. Não sincronizam com um áudio enviado, e o áudio ligado não é usado na fala: só define a duração. A imagem é ajustada ao formato escolhido (fundo desfocado).</span></div>
    ${field('Texto da fala', `<textarea data-k="text" rows="2" maxlength="300" placeholder="Ex.: Bem-vindo à nova coleção de verão.">${esc(n.text)}</textarea>`)}
    ${field('Expressão e movimento (opcional)', `<input data-k="motion" maxlength="200" value="${esc(n.motion)}" placeholder="Ex.: sorri com entusiasmo">`)}
    <div class="cv-row3">
      ${field('Formato', `<select data-k="aspect">${PERF_ASPECTS.map(([v, l]) => opt(v, n.aspect, l)).join('')}</select>`)}
    </div>
    <div class="cv-meta"><span>${audio ? 'Áudio ligado: usado só para a duração.' : 'Sem áudio ligado: a duração vem do texto.'}</span></div>
    ${q ? `<div class="cv-meta"><span>Estimativa: ${q.seconds} s, cerca de ${fmtTok(Math.round(q.usd * q.tokens_per_usd))}.</span></div>` : ''}
    ${q?.confirm ? `<label class="vedit-check"><input type="checkbox" data-role="perf-confirm"> Confirmo o custo estimado (acima de US$ ${PERF_CONFIRM_USD.toFixed(2)})</label>` : ''}
    ${n.status === 'error' ? `<div class="cv-error">${ic('x-circle')}<span>${esc(n.error || 'erro')}</span></div>` : ''}
    <div class="cv-foot">
      <button class="btn ghost" data-act="perf-quote" ${busy || !src || !n.text.trim() ? 'disabled' : ''}>${ic('sparkles')} Ver custo</button>
      <button class="btn primary" data-act="perf-run" ${busy || !src || !n.text.trim() ? 'disabled' : ''}>${ic('user')} ${n.output ? 'Gerar de novo' : 'Gerar performance'}</button>
    </div>`;
}

// Botões do node Performance sem recriar o DOM (mantém o foco no campo de texto).
function syncPerformanceButtons(el, n) {
  const off = n.status === 'running' || !upscaleSource(n) || !n.text.trim();
  el.querySelector('[data-act=perf-quote]')?.toggleAttribute('disabled', off);
  el.querySelector('[data-act=perf-run]')?.toggleAttribute('disabled', off);
}

function bindPerformance(el, n) {
  el.querySelectorAll('[data-k]').forEach((inp) => {
    const ev = inp.tagName === 'TEXTAREA' || inp.type === 'text' ? 'input' : 'change';
    inp.addEventListener(ev, () => {
      n[inp.dataset.k] = inp.value;
      save();
      if (inp.tagName === 'SELECT') renderNode(n);  // seletor reconstrói; campos de texto só atualizam os botões
      else syncPerformanceButtons(el, n);
    });
  });
  el.querySelector('[data-act=perf-quote]')?.addEventListener('click', () => quotePerformance(n).catch((err) => toast(err.message)));
  el.querySelector('[data-act=perf-run]')?.addEventListener('click', () => runPerformance(n).catch((err) => toast(err.message)));
}

async function quotePerformance(n) {
  const q = await api('/api/video/performance/quote', perfRequest(n));
  n.quote = { ...q, key: perfKey(n), tokens_per_usd: q.tokens_per_usd };
  renderNode(n);
  return n.quote;
}

async function runPerformance(n) {
  if (n.status === 'running') return;
  if (!upscaleSource(n)) return toast('Ligue a imagem do personagem na entrada');
  const q = perfFresh(n) ? n.quote : await quotePerformance(n);
  if (q.confirm && !document.querySelector(`.cv-node[data-id="${n.id}"] [data-role=perf-confirm]`)?.checked) {
    return toast('Confirme o custo estimado para gerar');
  }
  let job;
  try {
    job = await api('/api/video/performance', perfRequest(n));
  } catch (err) {
    Object.assign(n, { status: 'error', error: err.message });
    renderNode(n);
    throw err;
  }
  Object.assign(n, { jobId: job.id, status: 'running', progress: 0, error: null, cost: null, stage: 'enviado' });
  renderNode(n);
  save();
  toast(`${n.name}: gerando a performance`);
  return watchJob(n);
}
