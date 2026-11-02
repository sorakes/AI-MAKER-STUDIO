// Áudio: node com 3 abas (Voz, Música, Efeito). Voz e música são modelos reais (OpenRouter); efeito é biblioteca
// sintetizada com ffmpeg. A saída é um arquivo de áudio com prévia, e pode ir para a Montagem (trilha) ou para a Fala.

const AUDIO_TABS = [['voice', 'Voz'], ['music', 'Música'], ['sfx', 'Efeito']];
const AUDIO_GENRES = [['pop', 'Pop'], ['rock', 'Rock'], ['electronic', 'Eletrônica'], ['jazz', 'Jazz'], ['classical', 'Clássica'],
  ['lofi', 'Lo-fi'], ['hiphop', 'Hip hop'], ['acoustic', 'Acústica']];
const AUDIO_MOODS = [['happy', 'Alegre'], ['epic', 'Épico'], ['calm', 'Calmo'], ['tense', 'Tenso'], ['romantic', 'Romântico'],
  ['mysterious', 'Misterioso']];
const AUDIO_SFX = [['rain', 'Chuva'], ['wind', 'Vento'], ['whoosh', 'Whoosh'], ['beep', 'Bip']];
const AUDIO_VOICES = [['alloy', 'Alloy'], ['nova', 'Nova'], ['onyx', 'Onyx'], ['shimmer', 'Shimmer']];

function newAudio(pos) {
  return { id: uid('aud'), type: 'audio', name: t('Áudio'), tab: 'voice', text: '', voice: 'alloy', speed: 1,
    genre: 'pop', mood: 'happy', musicPrompt: '', seconds: 10, sfxKind: 'rain', sfxSeconds: 3,
    status: 'idle', output: null, takes: [], jobId: null, error: null, ...pos };
}

// Áudio ligado à entrada de um nó (ex.: a trilha da Montagem).
const incomingAudioUrl = (n) => {
  const src = cv.edges.filter((e) => e.to === n.id).map((e) => byId(e.from)).find((s) => s?.type === 'audio' && s.output?.url);
  return src ? src.output.url : null;
};

function audioBody(n) {
  const tabs = AUDIO_TABS.map(([k, l]) => `<button type="button" role="tab" class="aud-tab${n.tab === k ? ' on' : ''}"
      id="aud-tab-${k}-${n.id}" aria-selected="${n.tab === k}" aria-controls="aud-panel-${n.id}" tabindex="${n.tab === k ? 0 : -1}"
      data-tab="${k}">${l}</button>`).join('');
  const busy = n.status === 'running';
  let panel;
  if (n.tab === 'voice') {
    panel = `${field('Texto da fala', `<textarea data-k="text" rows="3" maxlength="500" placeholder="Ex.: Bem-vindo à nova coleção de verão.">${esc(n.text)}</textarea>`)}
      <div class="cv-row3">
        ${field('Voz', `<select data-k="voice">${AUDIO_VOICES.map(([v, l]) => opt(v, n.voice, l)).join('')}</select>`)}
        ${field('Velocidade', `<select data-k="speed">${[0.75, 1, 1.25, 1.5].map((v) => opt(v, n.speed, `${v}×`)).join('')}</select>`)}
      </div>
      <div class="cv-meta"><span>Voz por modelo de áudio (OpenRouter). Custa centavos.</span></div>`;
  } else if (n.tab === 'music') {
    panel = `<div class="cv-row3">
        ${field('Gênero', `<select data-k="genre">${AUDIO_GENRES.map(([v, l]) => opt(v, n.genre, l)).join('')}</select>`)}
        ${field('Humor', `<select data-k="mood">${AUDIO_MOODS.map(([v, l]) => opt(v, n.mood, l)).join('')}</select>`)}
        ${field('Duração', `<select data-k="seconds">${[5, 10, 15, 20, 30].map((v) => opt(v, n.seconds, `${v} s`)).join('')}</select>`)}
      </div>
      ${field('Detalhes (opcional)', `<input data-k="musicPrompt" maxlength="300" value="${esc(n.musicPrompt)}" placeholder="Ex.: violão e percussão leve">`)}
      <div class="cv-meta"><span>Música por prompt (Lyria). Cada clipe é cobrado e depois cortado na duração pedida.</span></div>`;
  } else {
    panel = `<div class="cv-row3">
        ${field('Efeito', `<select data-k="sfxKind">${AUDIO_SFX.map(([v, l]) => opt(v, n.sfxKind, l)).join('')}</select>`)}
        ${field('Duração', `<select data-k="sfxSeconds">${[1, 2, 3, 5, 10].map((v) => opt(v, n.sfxSeconds, `${v} s`)).join('')}</select>`)}
      </div>
      <div class="cv-warn">${ic('alert')} <span>Efeito sintetizado no servidor (ffmpeg). Não é gravação real.</span></div>`;
  }
  const go = { voice: 'Gerar voz', music: 'Gerar música', sfx: 'Gerar efeito' }[n.tab];
  const disabled = busy || (n.tab === 'voice' && !n.text.trim());
  return `
    ${previewBlock(n, { placeholder: { icon: 'music', text: 'Escolha a aba, configure e gere' } })}
    <div class="aud-tabs" role="tablist" aria-label="Tipo de áudio">${tabs}</div>
    <div class="aud-panel" role="tabpanel" id="aud-panel-${n.id}" aria-labelledby="aud-tab-${n.tab}-${n.id}">${panel}</div>
    ${n.status === 'error' ? `<div class="cv-error">${ic('x-circle')}<span>${esc(n.error || 'erro')}</span></div>` : ''}
    <div class="cv-foot">
      <button class="btn primary" data-act="gen" ${disabled ? 'disabled' : ''}>${ic('music')} ${n.output ? `${go} de novo` : go}</button>
    </div>`;
}

function bindAudio(el, n) {
  const numeric = ['speed', 'seconds', 'sfxSeconds'];
  el.querySelectorAll('[data-k]').forEach((inp) => {
    const ev = inp.tagName === 'TEXTAREA' || inp.type === 'text' ? 'input' : 'change';
    inp.addEventListener(ev, () => {
      const k = inp.dataset.k;
      n[k] = numeric.includes(k) ? +inp.value : inp.value;
      save();
      if (k === 'text') el.querySelector('[data-act=gen]').disabled = !n.text.trim() || n.status === 'running';
    });
  });
  const tabs = [...el.querySelectorAll('[data-tab]')];
  const pick = (k, focus) => { n.tab = k; save(); renderNode(n); if (focus) document.getElementById(`aud-tab-${k}-${n.id}`)?.focus(); };
  tabs.forEach((b) => b.addEventListener('click', () => pick(b.dataset.tab, false)));
  tabs.forEach((b) => b.addEventListener('keydown', (e) => {
    const i = AUDIO_TABS.findIndex(([k]) => k === b.dataset.tab);
    const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    pick(AUDIO_TABS[(i + step + AUDIO_TABS.length) % AUDIO_TABS.length][0], true);
  }));
  el.querySelector('[data-act=gen]')?.addEventListener('click', () => runAudio(n).catch((err) => toast(err.message)));
}

// Gera o arquivo do aba escolhida (um job, com cota, fila e versões). O resultado chega pelo watchJob.
async function runAudio(n) {
  if (n.status === 'running') return;
  let path;
  let body = { node_id: n.id, name: n.name };
  if (n.tab === 'voice') {
    path = '/api/audio/speech';
    body = { ...body, text: n.text.trim(), voice: n.voice, speed: +n.speed };
  } else if (n.tab === 'music') {
    path = '/api/audio/music';
    body = { ...body, genre: n.genre, mood: n.mood, prompt: n.musicPrompt || '', seconds: +n.seconds };
  } else {
    path = '/api/audio/sfx';
    body = { ...body, kind: n.sfxKind, seconds: +n.sfxSeconds };
  }
  let job;
  try {
    job = await api(path, body);
  } catch (err) {
    Object.assign(n, { status: 'error', error: err.message });
    renderNode(n);
    throw err;
  }
  Object.assign(n, { jobId: job.id, status: 'running', progress: 0, error: null, cost: null, stage: 'enviado' });
  renderNode(n);
  save();
  toast(`${n.name}: gerando ${{ voice: 'a voz', music: 'a música', sfx: 'o efeito' }[n.tab]}`);
  return watchJob(n);
}

// Fala: texto sem áudio -> gera a voz primeiro, e só então anima o personagem com ela.
async function speechForSpeak(n) {
  const job = await api('/api/audio/speech', { node_id: n.id, name: `${n.name} · voz`, text: n.script.trim(),
    voice: n.voice || 'alloy', speed: 1 });
  for (let i = 0; i < 300; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    const j = await api(`/api/jobs/${job.id}`);
    if (j.status === 'done') return j.media;
    if (['error', 'interrupted'].includes(j.status)) throw new Error(j.error || j.status);
  }
  throw new Error('a voz demorou demais');
}
