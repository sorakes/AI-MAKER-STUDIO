// Editar vídeo: o vídeo ligado na entrada vai ao modelo que recebe vídeo (FLUX, Runway Aleph, Wan, Seedance), por link
// público temporário; vídeos longos em pedaços em sequência. O node mostra só quantos tokens vai usar (sem confirmação).

const VEDIT_PRESETS = [['none', 'Sem preset'], ['anime', 'Estilo anime'], ['night', 'Dia → noite'], ['sunset', 'Céu por pôr do sol'],
  ['background', 'Trocar o fundo'], ['color', 'Mudar a cor de um elemento']];
const VEDIT_STRENGTH = [['low', 'Leve'], ['medium', 'Média'], ['high', 'Forte']];
const VEDIT_MODES = [['swap', 'Trocar personagem, roupa ou objeto (mantém o resto)'],
  ['motion', 'Transferir movimento (refaz o resto com as referências)']];

function newVideoEdit(pos) {
  return { id: uid('vedit'), type: 'videoedit', name: 'Editar vídeo', prompt: '', preset: 'none', strength: 'medium',
    start: 0, end: 3, whole: true, model: 'auto', mode: 'swap', refs: [], info: null, preserveAudio: true, quote: null, status: 'idle', output: null, takes: [], jobId: null, error: null, ...pos };
}

// Chave da estimativa: se mudar algo que custa, a estimativa antiga não vale.
// Só o que muda o custo (duração e modelo): digitar o pedido não refaz a estimativa.
const veditKey = (n) => JSON.stringify([n.start, n.end, n.whole, n.model, videoOf(incoming(n.id)[0]) || '']);

// Estimativa automática: o node mostra quantos tokens vai usar, sem botão de custo nem caixa de confirmação.
const VEDIT_QUOTING = new Set();
const VEDIT_TIMERS = new Map();
function veditAutoQuote(n, src, p) {
  if (!src || !p.info || p.info.loading || p.info.error || p.bad || veditFresh(n)) return;
  const key = veditKey(n);
  if (VEDIT_QUOTING.has(key) || n.quoteFailed === key) return;
  VEDIT_QUOTING.add(key);
  quoteVideoEdit(n).catch(() => { n.quoteFailed = key; }).finally(() => VEDIT_QUOTING.delete(key));
}
const veditFresh = (n) => !!n.quote && n.quote.key === veditKey(n);

// Info do vídeo ligado (duração, túnel, modelos): buscada uma vez por vídeo.
function veditInfo(n, src) {
  if (!src) return null;
  if (n.info?.url === src) return n.info;
  n.info = { url: src, loading: true };
  api(`/api/video/edit/info?source_url=${encodeURIComponent(src)}`)
    .then((i) => { n.info = { url: src, ...i }; renderNode(n); })
    .catch((err) => { n.info = { url: src, error: err.message }; renderNode(n); });
  return n.info;
}

// Limites e trecho efetivos. Este node só usa modelos que RECEBEM O VÍDEO: sem túnel, não edita.
function veditPlan(n) {
  const info = n.info?.url === videoOf(incoming(n.id)[0]) ? n.info : null;
  const native = !!info?.tunnel;
  const off = !!info && !info.loading && !info.error && !info.tunnel;
  const maxSeconds = info?.native_max || 120;
  const span = n.whole && info?.duration ? info.duration : Math.round((n.end - n.start) * 10) / 10;
  const bad = off || !(span > 0) || span > maxSeconds;
  return { info, native, off, maxSeconds, span, bad };
}

function veditSpanText(p) {
  if (p.off) return 'Sem o túnel público o vídeo não chega ao modelo.';
  return p.bad ? `O trecho precisa ter de 0 a ${p.maxSeconds} s.` : `Trecho de ${Math.round(p.span * 10) / 10} s.`;
}

// Nodes salvos antes da edição inteira não têm estes campos: assumem os padrões novos.
function veditDefaults(n) {
  if (n.whole === undefined) n.whole = true;
  if (!n.model || n.model === 'frames') n.model = 'auto';  // o modo por quadros saiu deste node
  if (!n.mode) n.mode = 'swap';
  if (!n.refs) n.refs = [];
}

function videoEditBody(n) {
  veditDefaults(n);
  const src = videoOf(incoming(n.id)[0]);
  const busy = n.status === 'running';
  const q = veditFresh(n) ? n.quote : null;
  veditInfo(n, src);
  const p = veditPlan(n);
  veditAutoQuote(n, src, p);
  const models = p.info?.models || [];
  const modelOpts = `<option value="auto" ${n.model === 'auto' ? 'selected' : ''}>Automático</option>`
    + models.map((m) => opt(m.id, n.model, esc(`${m.label} · ${fmtTok(m.tokens_per_s)}/s`))).join('');
  const note = !src || !p.info || p.info.loading ? ''
    : p.native ? `<div class="cv-meta"><span>${ic('check')} O modelo recebe o vídeo (até ${p.maxSeconds} s). Vídeos longos vão em pedaços em sequência, cada um levando o final já editado do anterior.</span></div>`
      : p.off ? `<div class="cv-warn">${ic('alert')} <span>Túnel público desligado: este node envia o vídeo ao modelo e precisa do serviço "tunnel" do docker-compose.</span></div>` : '';
  return `
    ${previewBlock(n, { placeholder: { icon: 'video', text: src ? '' : 'Ligue um vídeo na entrada' }, source: src })}
    ${src ? '' : `<div class="cv-meta"><span class="err">${ic('alert')} Ligue um vídeo na entrada</span></div>`}
    ${p.info?.error ? `<div class="cv-error">${ic('x-circle')}<span>${esc(p.info.error)}</span></div>` : ''}
    ${note}
    ${field('Modelo', `<select data-k="model">${modelOpts}</select>`)}
    ${field('Modo', `<select data-k="mode">${VEDIT_MODES.map(([v, l]) => opt(v, n.mode, l)).join('')}</select>`)}
    ${refsBlock(n, 'Foto do personagem, da roupa ou do produto que entra no vídeo. A IA chama de "imagem 1, 2…".')}
    ${field('O que mudar', `<textarea data-k="prompt" rows="2" maxlength="300" placeholder="Ex.: troque o céu por pôr do sol">${esc(n.prompt)}</textarea>`)}
    <div class="cv-row3">
      ${field('Preset', `<select data-k="preset">${VEDIT_PRESETS.map(([v, l]) => opt(v, n.preset, l)).join('')}</select>`)}
      ${field('Força', `<select data-k="strength">${VEDIT_STRENGTH.map(([v, l]) => opt(v, n.strength, l)).join('')}</select>`)}
    </div>
    <label class="vedit-check"><input type="checkbox" data-k="whole" ${n.whole ? 'checked' : ''}> Editar o vídeo inteiro${p.info?.duration ? ` (${p.info.duration} s)` : ''}</label>
    ${n.whole ? '' : `<div class="vedit-trim" role="group" aria-label="Trecho a editar">
        <label class="cv-field"><span class="cv-flabel">Início (s)</span><input type="number" min="0" step="0.5" value="${n.start}" data-k="start" aria-label="Início do trecho (segundos)"></label>
        <label class="cv-field"><span class="cv-flabel">Fim (s)</span><input type="number" min="0" step="0.5" value="${n.end}" data-k="end" aria-label="Fim do trecho (segundos)"></label>
      </div>`}
    <label class="vedit-check"><input type="checkbox" data-k="preserveAudio" ${n.preserveAudio ? 'checked' : ''}> Manter o áudio original</label>
    <div class="cv-meta"><span data-role="vedit-span">${veditSpanText(p)}</span></div>
    ${q ? `<div class="cv-meta"><span data-role="vedit-tokens">Vai usar cerca de ${fmtTok(q.tokens)}.</span></div>` : ''}
    ${n.status === 'error' ? `<div class="cv-error">${ic('x-circle')}<span>${esc(n.error || 'erro')}</span></div>` : ''}
    <div class="cv-foot">
      <button class="btn primary" data-act="vedit-run" ${busy || !src || p.bad || !(n.prompt.trim() || n.preset !== 'none' || refsOf(n).length) ? 'disabled' : ''}>${ic('video')} ${n.output ? 'Editar de novo' : n.whole ? 'Editar vídeo' : 'Editar trecho'}</button>
    </div>`;
}

// Aviso de trecho e botões sem recriar o node (o foco nos campos de início e fim é preservado).
function syncVideoEditState(el, n) {
  const p = veditPlan(n);
  const src = videoOf(incoming(n.id)[0]);
  const busy = n.status === 'running';
  el.querySelector('[data-role=vedit-span]')?.replaceChildren(document.createTextNode(veditSpanText(p)));
  clearTimeout(VEDIT_TIMERS.get(n.id));  // início/fim digitados: refaz a estimativa quando parar de digitar
  VEDIT_TIMERS.set(n.id, setTimeout(() => veditAutoQuote(n, src, veditPlan(n)), 500));
  el.querySelector('[data-act=vedit-run]')?.toggleAttribute('disabled', busy || !src || p.bad || !(n.prompt.trim() || n.preset !== 'none' || refsOf(n).length));
}

function bindVideoEdit(el, n) {
  el.querySelectorAll('[data-k]').forEach((inp) => {
    const k = inp.dataset.k;
    const ev = inp.tagName === 'TEXTAREA' || inp.type === 'number' ? 'input' : 'change';
    inp.addEventListener(ev, () => {
      if (inp.type === 'checkbox') n[k] = inp.checked;
      else n[k] = ['start', 'end'].includes(k) ? +inp.value : inp.value;
      save();
      if (['preset', 'model', 'whole', 'mode'].includes(k)) renderNode(n);  // campos numéricos: só o estado dos botões e o aviso de trecho acompanham
      else syncVideoEditState(el, n);
    });
  });
  bindRefs(el, n);
  el.querySelector('[data-act=vedit-run]')?.addEventListener('click', () => runVideoEdit(n).catch((err) => toast(err.message)));
}

function veditRequest(n) {
  veditDefaults(n);
  return { node_id: n.id, name: n.name, source_url: videoOf(incoming(n.id)[0]), prompt: n.prompt || '', preset: n.preset,
    strength: n.strength, start: +n.start, end: +n.end, whole: !!n.whole, model: n.model || 'auto', mode: n.mode || 'swap', ref_urls: refsOf(n).slice(0, 8), preserve_audio: !!n.preserveAudio };
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
  if (!veditFresh(n)) await quoteVideoEdit(n);  // estimativa antes de gastar (só informa os tokens; sem confirmação)
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
  toast(`${n.name}: ${veditPlan(n).native ? 'editando o vídeo' : 'editando os quadros'}`);
  return watchJob(n);
}
