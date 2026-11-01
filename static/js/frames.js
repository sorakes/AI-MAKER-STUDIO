// escolha de frame
//
//  • Seletor de frame reutilizável (vídeo + régua + ±1 frame a 16 fps).
//  • Gerar vídeo com um VÍDEO na entrada: escolhe de qual frame o próximo vídeo começa.
//    Na montagem, o vídeo anterior é cortado exatamente nesse frame (continuidade).
//  • Nó Extrair frame: vídeo entra → frame escolhido sai como imagem
//    (1º frame de um vídeo, referência R, entrada A/B de transição).

const FPS = 24; // vídeos dos provedores saem em ~24 fps

function framePickerHTML(video, dur, t, useLabel) {
  return `<div class="tr-pick" data-role="picker">
    <video class="cv-media" data-role="scrub" src="${video}" muted playsinline preload="auto"></video>
    <div class="tr-row">
      <input type="range" data-role="pos" min="0" max="${dur || 5}" step="${1 / FPS}" value="${t}">
      <span class="mono" data-role="poslabel">${(+t).toFixed(2)}s</span>
    </div>
    <div class="tr-row">
      <button class="btn small ghost" data-act="first" title="Primeiro frame" aria-label="Primeiro frame">${ic('skip-back')}</button>
      <button class="btn small ghost" data-act="back" title="1 frame para trás">−1f</button>
      <button class="btn small ghost" data-act="fwd" title="1 frame para frente">+1f</button>
      <button class="btn small ghost" data-act="last" title="Último frame" aria-label="Último frame">${ic('skip-forward')}</button>
      <button class="btn small primary" data-act="use">${useLabel}</button>
    </div>
  </div>`;
}

// onUse(t, frame) recebe o tempo escolhido e o PNG extraído pelo servidor.
function bindFramePicker(box, videoUrl, onUse) {
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
    if (Math.abs(+range.max - vid.duration) > 0.1) range.max = vid.duration;
    seek(+range.value);
  });
  range.addEventListener('input', () => seek(+range.value));
  box.querySelector('[data-act=first]').onclick = () => seek(0);
  box.querySelector('[data-act=last]').onclick = () => seek(+range.max);
  box.querySelector('[data-act=back]').onclick = () => seek(+range.value - 1 / FPS);
  box.querySelector('[data-act=fwd]').onclick = () => seek(+range.value + 1 / FPS);
  box.querySelector('[data-act=use]').onclick = async () => {
    const t = +range.value;
    const btn = box.querySelector('[data-act=use]');
    btn.disabled = true;
    try {
      const frame = await api('/api/media/frame', { url: videoUrl, t });
      await onUse(t, frame);
    } finally { btn.disabled = false; }
  };
}

// ---------------- Vídeo → próximo vídeo a partir do frame escolhido ----------------

// 1º frame efetivo de um gerador de vídeo: o frame escolhido (se for da entrada atual) ou o padrão.
function startFrameOf(n) {
  const up = incoming(n.id)[0];
  if (!up) return null;
  if (n.inFrame && n.inFrame.src === up.id && videoOf(up) === n.inFrame.video) return n.inFrame.url;
  return frameOf(up);
}

function startPickerBlock(n) {
  const up = incoming(n.id)[0];
  const video = videoOf(up);
  if (!video) return '';
  const chosen = n.inFrame && n.inFrame.src === up.id && n.inFrame.video === video ? n.inFrame : null;
  const dur = durationOf(up);
  return `<details class="tr-fine cv-startpick" ${chosen ? '' : ''}>
    <summary>Começar do frame: <b>${chosen ? `${chosen.t.toFixed(2)}s` : 'último (padrão)'}</b> de "${esc(up.name)}"</summary>
    ${framePickerHTML(video, dur, chosen ? chosen.t : (dur || 5), 'Gerar a partir deste frame')}
    <div class="cv-meta"><span>Na montagem, "${esc(up.name)}" é cortado neste frame.</span>${chosen ? '<button class="btn small ghost" data-act="resetstart">voltar ao último</button>' : ''}</div>
  </details>`;
}

function bindStartPicker(el, n) {
  const box = el.querySelector('.cv-startpick [data-role=picker]');
  if (!box) return;
  const up = incoming(n.id)[0];
  bindFramePicker(box, videoOf(up), (t, frame) => {
    n.inFrame = { src: up.id, video: videoOf(up), t, url: frame.url };
    n.autoKey = null;
    save();
    renderNode(n);
    toast(`O próximo vídeo começa em ${t.toFixed(2)}s de "${up.name}"`);
  });
  el.querySelector('[data-act=resetstart]')?.addEventListener('click', () => {
    n.inFrame = null;
    n.autoKey = null;
    save();
    renderNode(n);
  });
}

// ---------------- Nó Extrair frame ----------------

function newFrameNode(pos) {
  return { id: uid('frame'), type: 'frame', name: t('Frame extraído'), t: null, frame: null, src: null, ...pos };
}

// Saída do nó: o frame escolhido (se ainda for da mesma entrada) ou o último frame do vídeo.
function extractedFrame(n) {
  const up = incoming(n.id)[0];
  if (!up) return null;
  if (n.frame && n.src === up.id && n.video === videoOf(up)) return n.frame.url;
  return frameOf(up);
}

function frameNodeBody(n) {
  const up = incoming(n.id)[0];
  const video = videoOf(up);
  const out = extractedFrame(n);
  const chosen = n.frame && n.src === up?.id && n.video === video;
  const dur = durationOf(up);
  return `
    ${video ? framePickerHTML(video, dur, chosen ? n.t : (dur || 5), 'Extrair este frame')
      : `<div class="cv-meta"><span class="err">${up ? `"${esc(up.name)}" ainda não tem vídeo` : 'Ligue um vídeo na entrada'}</span></div>`}
    ${out ? `<div class="cv-meta"><span>Saída: ${chosen ? `frame ${(+n.t).toFixed(2)}s` : 'último frame (padrão)'}</span><span>imagem ●</span></div>
      <img class="cv-media" src="${out}" alt="Frame extraído">` : ''}
    <div class="cv-meta"><span>Use a saída como 1º frame de um vídeo, referência (R) ou A/B de transição.</span></div>`;
}

function bindFrameNode(el, n) {
  const box = el.querySelector('[data-role=picker]');
  if (!box) return;
  const up = incoming(n.id)[0];
  bindFramePicker(box, videoOf(up), (t, frame) => {
    Object.assign(n, { t, frame, src: up.id, video: videoOf(up) });
    save();
    renderNode(n);
    // Quem usa esta imagem precisa refazer o prompt.
    outgoing(n.id).forEach((x) => { invalidate(x); renderNode(x); });
    toast(`Frame ${t.toFixed(2)}s extraído`);
  });
}
