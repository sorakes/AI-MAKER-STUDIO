// Direção desenhada sobre o 1º frame.
//
//   ROXO   = câmera: a linha é a rota da câmera sobre a imagem (ponto = começo, seta = fim).
//   CORES  = elementos: circule o objeto e/ou puxe uma linha (ponto cheio = onde começa, anel vazio = onde termina).
// Cada cor tem um cartão ("AMARELO") onde se escreve o que deve acontecer. A IA de visão recebe o
// frame com o desenho + os cartões e transforma em instrução de movimento para o modelo de vídeo.
// Sem frame conectado a câmera fica bloqueada (balão explica como liberar).

const DZ_COLORS = {
  camera: { hex: '#a855f7', label: 'CÂMERA' },
  yellow: { hex: '#facc15', label: 'AMARELO' },
  cyan: { hex: '#22d3ee', label: 'CIANO' },
  pink: { hex: '#f472b6', label: 'ROSA' },
  orange: { hex: '#fb923c', label: 'LARANJA' },
  green: { hex: '#4ade80', label: 'VERDE' },
  red: { hex: '#f87171', label: 'VERMELHO' },
  blue: { hex: '#60a5fa', label: 'AZUL' },
  white: { hex: '#f8fafc', label: 'BRANCO' },
  light: { hex: '#fbbf24', label: 'LUZ' },  // seta de direção da luz (modo Relight)
};
const arrowColor = (c) => c === 'camera' || c === 'light';  // essas linhas têm seta no fim
const PAINT_COLORS = Object.keys(DZ_COLORS).filter((c) => !['camera', 'light'].includes(c));
const DZ_PLACEHOLDER = {
  camera: 'O que a câmera faz? Ex.: começa aberta, avança devagar até a garrafa e sobe até o logo.',
  element: 'O que este elemento faz? Ex.: a bola vai levemente para a direita.',
  sketch: 'O que muda nesta região? Ex.: a camiseta passa a ser azul-marinho.',
};

const dz = { node: null, frame: null, img: null, layers: [], active: 'camera', tool: 'draw', stroke: null, history: [], result: null, busy: false, help: true, mode: 'direction' };

// ---------------- Card no nó + balão ----------------

function directionState(n) {
  const frame = startFrameOf(n);
  const d = n.direction;
  return { frame, d, stale: !!(d && frame && d.frame !== frame), has: !!(d && d.layers?.some((l) => l.shapes.length || l.prompt.trim())) };
}

function directionCard(n) {
  const { frame, d, stale, has } = directionState(n);
  if (!frame) {
    return `<div class="cv-camcard locked" data-act="camlocked" title="Câmera bloqueada: clique para saber como liberar">
      <span class="cv-lock">${ic('lock')}</span>
      <div><span class="cv-flabel">Câmera e movimento</span><b>Bloqueado</b><small>${n.cameraHint ? `Sugestão do roteiro: ${esc(n.cameraHint)}` : 'precisa de um frame de entrada'}</small></div>
      <span class="cv-camgo muted">${ic('help')}</span>
    </div>`;
  }
  const elems = (d?.layers || []).filter((l) => l.color !== 'camera' && (l.shapes.length || l.prompt.trim()));
  const camTxt = d?.result?.camera_pt || d?.layers?.find((l) => l.color === 'camera')?.prompt || '';
  return `<div class="cv-camcard" data-act="cam" title="Desenhar câmera e movimentos sobre o frame">
    <canvas width="160" height="90"></canvas>
    <div><span class="cv-flabel">Câmera e movimento</span>
      <b>${has ? esc(camTxt ? camTxt.slice(0, 60) : 'Desenho sem câmera') : 'Desenhar no frame'}</b>
      <small>${has ? `${elems.length ? `${elems.length} elemento${elems.length > 1 ? 's' : ''} · ` : ''}${d.result ? 'a IA já entendeu' : 'ainda não interpretado'}` : 'roxo = câmera · cores = elementos'}</small>
      ${stale ? `<small class="cv-warn">${ic('alert')} desenhado em outro frame: revise</small>` : ''}
      ${has && d.diagram_url && !videoTakesRefs(n) ? `<small class="dz-send off" title="O modelo de vídeo escolhido não recebe imagens. A câmera vai só pelo texto.">${ic('x')} desenho não vai: modelo não aceita imagem</small>` : has && d.diagram_url ? `<small class="dz-send ${n.sendDiagram === false ? 'off' : 'on'}" data-act="senddiagram" title="Clique para ligar/desligar">${ic(n.sendDiagram === false ? 'x' : 'check')} desenho ${n.sendDiagram === false ? 'NÃO vai' : 'vai'} para o modelo de vídeo</small>` : ''}
      ${has ? `<span class="dz-dots">${d.layers.filter((l) => l.shapes.length || l.prompt.trim()).map((l) => `<i data-st="background:${DZ_COLORS[l.color].hex}"></i>`).join('')}</span>` : ''}
    </div>
    <span class="cv-camgo">${ic('route')} ${has ? 'Editar' : 'Desenhar'}</span>
  </div>`;
}

function bindDirectionCard(el, n) {
  el.querySelector('[data-act=cam]')?.addEventListener('click', (e) => { if (!e.target.closest('[data-act=senddiagram]')) openDirection(n); });
  el.querySelector('[data-act=senddiagram]')?.addEventListener('click', (e) => { e.stopPropagation(); n.sendDiagram = n.sendDiagram === false; save(); renderNode(n); });
  el.querySelector('[data-act=camlocked]')?.addEventListener('click', (e) => showBalloon(e.currentTarget, `
    <b>${ic('lock')} Câmera bloqueada</b>
    <p>A câmera e os movimentos são desenhados <b>em cima do 1º frame</b> do vídeo. Sem frame, não há onde desenhar.</p>
    <ol>
      <li>Ligue uma <b>imagem</b> ou <b>vídeo</b> na entrada deste nó (bolinha da esquerda). Pode ser um upload ou a saída de um <b>Gerar imagem</b>.</li>
      <li>Clique aqui de novo: em <b data-st="color:${DZ_COLORS.camera.hex}">roxo</b> você desenha a rota da câmera; com outras cores, circula objetos e puxa linhas: o ponto cheio é onde o objeto começa e o anel vazio, onde termina.</li>
      <li>Escreva em cada cartão o que deve acontecer. A IA olha o frame e o desenho e move a câmera do vídeo a partir dele.</li>
    </ol>
    <p class="muted">Sem frame, o vídeo ainda pode ser gerado só pelo texto: a IA escolhe a câmera.</p>`));
  const mini = el.querySelector('.cv-camcard:not(.locked) canvas');
  if (mini) drawDirectionThumb(mini, n);
}

function showBalloon(anchor, html) {
  document.querySelector('.dz-balloon')?.remove();
  const b = document.createElement('div');
  b.className = 'dz-balloon';
  b.innerHTML = `${html}<button class="btn small primary" data-close>Entendi</button>`;
  document.body.appendChild(b);
  const r = anchor.getBoundingClientRect();
  const w = 320;
  b.style.left = `${Math.max(12, Math.min(window.innerWidth - w - 12, r.left + r.width / 2 - w / 2))}px`;
  const below = r.bottom + 12 + b.offsetHeight < window.innerHeight;
  b.style.top = `${below ? r.bottom + 10 : Math.max(12, r.top - b.offsetHeight - 10)}px`;
  b.classList.toggle('up', !below);
  const close = () => { b.remove(); window.removeEventListener('mousedown', outside, true); };
  const outside = (e) => { if (!b.contains(e.target)) close(); };
  b.querySelector('[data-close]').onclick = close;
  setTimeout(() => window.addEventListener('mousedown', outside, true));
}

function drawDirectionThumb(canvas, n) {
  const g = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  g.fillStyle = '#0d1016'; g.fillRect(0, 0, W, H);
  const frame = startFrameOf(n);
  if (!frame) return;
  const img = new Image();
  img.onload = () => {
    const s = Math.max(W / img.width, H / img.height);
    g.drawImage(img, (W - img.width * s) / 2, (H - img.height * s) / 2, img.width * s, img.height * s);
    (n.direction?.layers || []).forEach((l) => l.shapes.forEach((sh) => paintShape(g, sh, DZ_COLORS[l.color].hex, W, H, 1.2, arrowColor(l.color))));
  };
  img.src = frame;
}

// ---------------- Editor ----------------

function initDirection() {
  $('#dzCancel').onclick = () => $('#dzDialog').close();
  $('#dzSave').onclick = saveDirection;
  $('#dzUndo').onclick = dzUndo;
  $('#dzClear').onclick = () => { dzPush(); dz.layers.forEach((l) => { l.shapes = []; }); dzRender(); };
  $('#dzHelpBtn').onclick = () => { dz.help = !dz.help; $('#dzHelp').hidden = !dz.help; };
  $('#dzToolDraw').onclick = () => setTool('draw');
  $('#dzToolErase').onclick = () => setTool('erase');
  $('#dzAddEl').onclick = addElement;
  $('#dzInterpret').onclick = () => interpretDirection().catch((e) => toast(e.message));
  bindDzStage();
  window.addEventListener('resize', () => { if ($('#dzDialog').open) { dzSize(); dzRender(); } });
  window.addEventListener('keydown', (e) => {
    if (!$('#dzDialog').open || ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); dzUndo(); }
    if (e.key.toLowerCase() === 'e') setTool('erase');
    if (e.key.toLowerCase() === 'd') setTool('draw');
  });
}

function openDirection(n) {
  const frame = startFrameOf(n);
  if (!frame) return;
  dz.mode = 'direction';
  setChrome('camera');
  dz.node = n;
  dz.frame = frame;
  const saved = n.direction;
  dz.layers = saved?.layers ? JSON.parse(JSON.stringify(saved.layers)) : [];
  if (!dz.layers.find((l) => l.color === 'camera')) dz.layers.unshift({ color: 'camera', label: 'CÂMERA', prompt: '', shapes: [] });
  dz.result = saved?.frame === frame ? saved?.result || null : null;
  dz.active = 'camera';
  dz.tool = 'draw';
  dz.history = [];
  $('#dzTitle').textContent = n.name;
  $('#dzHelp').hidden = !dz.help;
  $('#dzStale').hidden = !(saved && saved.frame !== frame);
  dz.img = new Image();
  dz.img.onload = () => { dzSize(); dzRender(); };
  dz.img.src = frame;
  $('#dzImg').src = frame;
  $('#dzDialog').showModal();
  renderDzCards();
  renderDzResult();
  setTool('draw');
}

// Modo "Desenhar para regerar" (imagem gerada): mesmo editor, sem câmera nem interpretação.
function openSketch(n) {
  const out = n.output;
  if (!out?.url || out.kind === 'video') return toast('Gere a imagem primeiro');
  dz.mode = 'sketch';
  dz.node = n;
  dz.frame = out.url;
  const saved = n.sketch?.layers;
  dz.layers = saved ? JSON.parse(JSON.stringify(saved)) : [{ color: 'red', label: DZ_COLORS.red.label, prompt: '', shapes: [] }];
  dz.active = dz.layers[0].color;
  dz.result = null;
  dz.tool = 'draw';
  dz.history = [];
  setChrome('sketch');
  $('#dzTitle').textContent = n.name;
  dz.img = new Image();
  dz.img.onload = () => { dzSize(); dzRender(); };
  dz.img.src = out.url;
  $('#dzImg').src = out.url;
  $('#dzDialog').showModal();
  renderDzCards();
  renderDzResult();
  setTool('draw');
}

// Esconde o que é só de câmera no modo de regerar e troca os rótulos do editor.
const CHROME = {
  camera: { sub: 'Câmera e movimento · desenhe no frame', save: 'Salvar' },
  sketch: { sub: 'Desenhar para regerar · marque o que muda', save: 'Regerar imagem' },
  light: { sub: 'Direção da luz · arraste a seta de onde vem a luz', save: 'Usar esta luz' },
};
// Cada modo do editor esconde o que não serve a ele (câmera, interpretação) e troca os rótulos.
function setChrome(mode) {
  const plain = mode !== 'camera';
  $('#dzInterpret').hidden = plain;
  $('#dzInterpret').closest('.dz-res-box').hidden = plain;
  $('#dzHelpBtn').hidden = plain;
  $('#dzHelp').hidden = plain || !dz.help;
  $('#dzSave').lastChild.textContent = ` ${CHROME[mode].save}`;
  const small = document.querySelector('.dz-title small');
  if (small) small.textContent = CHROME[mode].sub;
}

function dzSize() {
  if (!dz.img?.naturalWidth) return;
  const box = $('#dzStage').getBoundingClientRect();
  const ar = dz.img.naturalWidth / dz.img.naturalHeight;
  const w = Math.max(200, Math.min(box.width - 2, (box.height - 2) * ar));
  const h = w / ar;
  for (const el of [$('#dzImg'), $('#dzCanvas')]) { el.style.width = `${w}px`; el.style.height = `${h}px`; }
  $('#dzCanvas').width = Math.round(w * devicePixelRatio);
  $('#dzCanvas').height = Math.round(h * devicePixelRatio);
}

const layerOf = (color) => dz.layers.find((l) => l.color === color);

function setTool(t) {
  dz.tool = t;
  $('#dzToolDraw').classList.toggle('active', t === 'draw');
  $('#dzToolErase').classList.toggle('active', t === 'erase');
  $('#dzCanvas').style.cursor = t === 'erase' ? 'cell' : 'crosshair';
  const c = DZ_COLORS[dz.active];
  $('#dzToolDraw').style.setProperty('--tc', c.hex);
  $('#dzToolDraw').querySelector('span').textContent = `Desenhar · ${c.label}`;
}

function setActive(color) {
  dz.active = color;
  setTool('draw');
  renderDzCards();
}

function addElement() {
  const free = PAINT_COLORS.find((c) => !layerOf(c));
  if (!free) return toast('Todas as cores já estão em uso');
  dzPush();
  dz.layers.push({ color: free, label: DZ_COLORS[free].label, prompt: '', shapes: [] });
  setActive(free);
  setTimeout(() => document.querySelector(`.dz-card[data-color="${free}"] textarea`)?.focus(), 50);
}

function dzPush() {
  dz.history.push(JSON.stringify(dz.layers));
  if (dz.history.length > 60) dz.history.shift();
}

function dzUndo() {
  const h = dz.history.pop();
  if (!h) return toast('Nada para desfazer');
  dz.layers = JSON.parse(h);
  if (!layerOf(dz.active)) dz.active = 'camera';
  dz.result = null;
  renderDzCards();
  renderDzResult();
  dzRender();
}

// ---------------- Desenho ----------------

function pathLen(pts) {
  let L = 0;
  for (let i = 1; i < pts.length; i++) L += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  return L;
}

function finishShape(pts) {
  if (pts.length < 2 || pathLen(pts) < 0.015) return null;
  const step = Math.max(1, Math.ceil(pts.length / 120));
  const simple = pts.filter((_, i) => i % step === 0 || i === pts.length - 1);
  const closed = Math.hypot(simple[0][0] - simple.at(-1)[0], simple[0][1] - simple.at(-1)[1]) < Math.max(0.05, pathLen(simple) * 0.12);
  return { type: closed && pathLen(simple) > 0.08 ? 'loop' : 'path', points: simple.map(([x, y]) => [+x.toFixed(4), +y.toFixed(4)]) };
}

// camera: a linha de câmera leva seta no fim; linhas de elementos levam anel vazio no fim. Loops não mudam.
function paintShape(g, sh, hex, W, H, scale = 1, camera = false) {
  const pts = sh.points;
  if (pts.length < 2) return;
  const lw = 4 * scale;
  g.save();
  g.lineCap = 'round'; g.lineJoin = 'round';
  g.strokeStyle = 'rgba(0,0,0,.55)'; g.lineWidth = lw + 3 * scale;
  const trace = () => { g.beginPath(); pts.forEach(([x, y], i) => (i ? g.lineTo(x * W, y * H) : g.moveTo(x * W, y * H))); if (sh.type === 'loop') g.closePath(); };
  trace(); g.stroke();
  g.strokeStyle = hex; g.lineWidth = lw;
  trace(); g.stroke();
  if (sh.type === 'loop') { g.globalAlpha = 0.16; g.fillStyle = hex; trace(); g.fill(); g.globalAlpha = 1; }
  else {
    const [x0, y0] = pts[0];
    g.fillStyle = hex; g.beginPath(); g.arc(x0 * W, y0 * H, lw * 1.4, 0, Math.PI * 2); g.fill();  // início: ponto cheio
    const a = pts.at(-1);
    if (camera) {  // rota da câmera: seta no fim
      const b = pts[Math.max(0, pts.length - 5)];
      const ang = Math.atan2((a[1] - b[1]) * H, (a[0] - b[0]) * W);
      const L = 14 * scale;
      g.beginPath();
      g.moveTo(a[0] * W, a[1] * H);
      g.lineTo(a[0] * W - L * Math.cos(ang - 0.45), a[1] * H - L * Math.sin(ang - 0.45));
      g.lineTo(a[0] * W - L * Math.cos(ang + 0.45), a[1] * H - L * Math.sin(ang + 0.45));
      g.closePath(); g.fill();
    } else {  // elemento: anel vazado no fim (sombra por baixo, como os traços)
      const r = lw * 1.9;
      g.beginPath(); g.arc(a[0] * W, a[1] * H, r, 0, Math.PI * 2);
      g.strokeStyle = 'rgba(0,0,0,.55)'; g.lineWidth = lw + 3 * scale; g.stroke();
      g.beginPath(); g.arc(a[0] * W, a[1] * H, r, 0, Math.PI * 2);
      g.strokeStyle = hex; g.lineWidth = lw; g.stroke();
    }
  }
  g.restore();
}

function paintTag(g, l, W, H, scale) {
  const sh = l.shapes[0];
  if (!sh) return;
  const [x, y] = sh.points[0];
  g.save();
  g.font = `700 ${11 * scale}px Inter, sans-serif`;
  const text = DZ_COLORS[l.color].label;
  const tw = g.measureText(text).width + 10 * scale;
  const px = Math.min(W - tw - 4, Math.max(4, x * W - tw / 2)), py = Math.max(4, y * H - 26 * scale);
  g.fillStyle = DZ_COLORS[l.color].hex; g.fillRect(px, py, tw, 17 * scale);
  g.fillStyle = l.color === 'white' || l.color === 'yellow' ? '#111' : '#fff';
  g.textBaseline = 'middle'; g.fillText(text, px + 5 * scale, py + 9 * scale);
  g.restore();
}

function dzRender() {
  const cvs = $('#dzCanvas');
  const g = cvs.getContext('2d');
  const W = cvs.width, H = cvs.height, d = devicePixelRatio;
  g.clearRect(0, 0, W, H);
  dz.layers.forEach((l) => l.shapes.forEach((sh) => paintShape(g, sh, DZ_COLORS[l.color].hex, W, H, d * (l.color === dz.active ? 1.1 : 0.9), arrowColor(l.color))));
  if (dz.stroke) paintShape(g, { type: 'path', points: dz.stroke }, DZ_COLORS[dz.active].hex, W, H, d * 1.1, arrowColor(dz.active));
  dz.layers.forEach((l) => paintTag(g, l, W, H, d));
}

function bindDzStage() {
  const cvs = $('#dzCanvas');
  const pos = (e) => { const r = cvs.getBoundingClientRect(); return [Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), Math.max(0, Math.min(1, (e.clientY - r.top) / r.height))]; };
  cvs.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    cvs.setPointerCapture(e.pointerId);
    const p = pos(e);
    if (dz.tool === 'erase') return eraseAt(p);
    dz.stroke = [p];
  });
  cvs.addEventListener('pointermove', (e) => {
    if (dz.tool === 'erase' && e.buttons === 1) return eraseAt(pos(e));
    if (!dz.stroke) return;
    const p = pos(e), last = dz.stroke.at(-1);
    if (Math.hypot(p[0] - last[0], p[1] - last[1]) > 0.003) { dz.stroke.push(p); dzRender(); }
  });
  cvs.addEventListener('pointerup', () => {
    if (!dz.stroke) return;
    const sh = finishShape(dz.stroke);
    dz.stroke = null;
    if (sh) {
      dzPush();
      let l = layerOf(dz.active);
      if (!l) { l = { color: dz.active, label: DZ_COLORS[dz.active].label, prompt: '', shapes: [] }; dz.layers.push(l); }
      if (dz.mode === 'light') l.shapes = [{ type: 'path', points: sh.points }];  // uma seta só
      else l.shapes.push(sh);
      dz.result = null;
      renderDzCards();
      renderDzResult();
      if (dz.active !== 'camera' && !l.prompt.trim()) setTimeout(() => document.querySelector(`.dz-card[data-color="${l.color}"] textarea`)?.focus(), 30);
    }
    dzRender();
  });
}

function eraseAt([x, y]) {
  const tol = 0.02;
  for (const l of dz.layers) {
    const i = l.shapes.findIndex((sh) => sh.points.some((p) => Math.hypot(p[0] - x, p[1] - y) < tol));
    if (i >= 0) {
      dzPush();
      l.shapes.splice(i, 1);
      dz.result = null;
      renderDzCards();
      renderDzResult();
      return dzRender();
    }
  }
}

// ---------------- Cartões por cor ----------------

function renderDzCards() {
  $('#dzCards').innerHTML = dz.layers.map((l) => {
    const c = DZ_COLORS[l.color];
    const cam = l.color === 'camera';
    const light = l.color === 'light';
    const loops = l.shapes.filter((s) => s.type === 'loop').length, paths = l.shapes.length - loops;
    const shapesTxt = [loops && `${loops} círculo${loops > 1 ? 's' : ''}`, paths && `${paths} linha${paths > 1 ? 's' : ''}`].filter(Boolean).join(' · ') || 'nada desenhado';
    return `<div class="dz-card ${dz.active === l.color ? 'active' : ''}" data-color="${l.color}" data-st="--c:${c.hex}">
      <div class="dz-card-head">
        <span class="dz-swatch"></span><b>${c.label}</b><small>${cam ? 'rota da câmera' : light ? 'direção da luz' : 'elemento'} · ${shapesTxt}</small>
        ${l.shapes.length ? `<button class="cv-hbtn" data-clear="${l.color}" title="Apagar traços desta cor" aria-label="Apagar traços desta cor">${ic('undo')}</button>` : ''}
        ${cam || light ? '' : `<button class="cv-hbtn" data-remove="${l.color}" title="Remover elemento" aria-label="Remover elemento">${ic('trash')}</button>`}
      </div>
      <textarea rows="${cam ? 3 : 2}" data-prompt="${l.color}" ${light ? 'hidden' : ''} placeholder="${esc(cam ? DZ_PLACEHOLDER.camera : dz.mode === 'sketch' ? DZ_PLACEHOLDER.sketch : DZ_PLACEHOLDER.element)}">${esc(l.prompt)}</textarea>
      ${cam && !l.shapes.length ? '<small class="dz-tip">Desenhe em roxo por onde a câmera vai: o ponto é o começo e a seta, o fim.</small>' : ''}
      ${!cam && !light && !l.shapes.length ? '<small class="dz-tip">Circule o objeto ou puxe uma linha: o ponto cheio marca o começo e o anel vazio, o fim.</small>' : ''}
      ${light && !l.shapes.length ? '<small class="dz-tip">Arraste a seta de onde vem a luz até para onde ela vai.</small>' : ''}
    </div>`;
  }).join('');
  document.querySelectorAll('.dz-card').forEach((card) => card.addEventListener('mousedown', (e) => {
    if (e.target.closest('button')) return;
    if (dz.active !== card.dataset.color) setActive(card.dataset.color);
  }));
  document.querySelectorAll('.dz-card textarea').forEach((t) => {
    t.addEventListener('focus', () => { if (dz.active !== t.dataset.prompt) { dz.active = t.dataset.prompt; setTool('draw'); document.querySelectorAll('.dz-card').forEach((c) => c.classList.toggle('active', c.dataset.color === dz.active)); dzRender(); } });
    t.addEventListener('input', () => { layerOf(t.dataset.prompt).prompt = t.value; dz.result = null; renderDzResult(); });
  });
  document.querySelectorAll('[data-clear]').forEach((b) => b.addEventListener('click', () => { dzPush(); layerOf(b.dataset.clear).shapes = []; dz.result = null; renderDzCards(); renderDzResult(); dzRender(); }));
  document.querySelectorAll('[data-remove]').forEach((b) => b.addEventListener('click', () => {
    dzPush();
    dz.layers = dz.layers.filter((l) => l.color !== b.dataset.remove);
    if (dz.active === b.dataset.remove) dz.active = 'camera';
    dz.result = null;
    setTool('draw');
    renderDzCards(); renderDzResult(); dzRender();
  }));
  $('#dzAddEl').disabled = PAINT_COLORS.every((c) => layerOf(c));
  $('#dzAddEl').hidden = dz.mode === 'light';
  setTool(dz.tool);
}

// ---------------- IA ----------------

// Frame + desenho numa imagem só (o que o modelo de visão vê).
function annotatedImage(maxSide = 1280) {
  const img = dz.img;
  const s = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
  const W = Math.round(img.naturalWidth * s), H = Math.round(img.naturalHeight * s);
  const c = document.createElement('canvas');
  c.width = W; c.height = H;
  const g = c.getContext('2d');
  g.drawImage(img, 0, 0, W, H);
  const k = W / 900;
  dz.layers.forEach((l) => l.shapes.forEach((sh) => paintShape(g, sh, DZ_COLORS[l.color].hex, W, H, k * 1.3, arrowColor(l.color))));
  dz.layers.forEach((l) => paintTag(g, l, W, H, k * 1.4));
  return c.toDataURL('image/jpeg', maxSide <= 640 ? 0.8 : 0.86);
}

const activeLayers = () => dz.layers.filter((l) => l.shapes.length || l.prompt.trim());

async function interpretDirection() {
  if (!activeLayers().length) { dz.result = null; renderDzResult(); return null; }
  dz.busy = true;
  renderDzResult();
  try {
    dz.result = await api('/api/direction/interpret', {
      annotated: annotatedImage(), intent: dz.node.intent || '', duration: dz.node.duration || 5,
      layers: activeLayers().map((l) => ({ color: l.color, label: l.label, prompt: l.prompt, shapes: l.shapes })),
    });
    return dz.result;
  } finally {
    dz.busy = false;
    renderDzResult();
  }
}

function renderDzResult() {
  const box = $('#dzResult');
  if (dz.busy) { box.innerHTML = `<div class="dz-res-empty">${ic('loader', 'spin')} A IA está olhando o frame e o desenho…</div>`; return; }
  const r = dz.result;
  if (!r) {
    box.innerHTML = `<div class="dz-res-empty">${activeLayers().length ? 'Clique em <b>Ver como a IA entendeu</b> (ou salve: a interpretação é feita ao salvar).' : 'Desenhe e escreva nos cartões para a IA interpretar.'}</div>`;
    return;
  }
  box.innerHTML = `
    <div class="dz-res-row" data-st="--c:${DZ_COLORS.camera.hex}"><span class="dz-swatch"></span><div><b>Câmera</b><p>${esc(r.camera_pt || r.camera_en || '—')}</p></div></div>
    ${(r.elements || []).map((e) => `<div class="dz-res-row" data-st="--c:${DZ_COLORS[e.color]?.hex || '#fff'}"><span class="dz-swatch"></span><div><b>${esc(DZ_COLORS[e.color]?.label || e.color)}${e.target_en ? ` · <span>${esc(e.target_en)}</span>` : ''}</b><p>${esc(e.action_pt || e.action_en || '—')}</p></div></div>`).join('')}
    ${(r.warnings_pt || []).map((w) => `<div class="cv-warn">${ic('alert')} ${esc(w)}</div>`).join('')}
    <small class="dz-src">via ${esc(r.source || '')}</small>`;
}

// Regerar: a visão lê a imagem com as marcações; o gerador recebe a imagem original (sem traços).
async function saveSketch() {
  const n = dz.node;
  const layers = activeLayers();
  if (!layers.length) return toast('Circule uma região ou escreva o que deve mudar');
  const btn = $('#dzSave');
  btn.disabled = true;
  try {
    const job = await api('/api/image/regenerate-from-sketch', {
      node_id: n.id, name: n.name, source_url: n.output.url, annotated: annotatedImage(),
      layers: layers.map((l) => ({ color: l.color, label: l.label, prompt: l.prompt, shapes: l.shapes })),
      intent: n.intent || '', model: n.model || 'auto', aspect: n.aspect || '16:9',
      quality: n.quality === 'final' ? 'final' : 'draft', ref_image_urls: refsOf(n),
    });
    n.sketch = { layers: JSON.parse(JSON.stringify(dz.layers)) };
    Object.assign(n, { jobId: job.id, status: 'running', progress: 0, error: null, stage: 'enviado', cost: null });
    $('#dzDialog').close();
    renderNode(n);
    save();
    toast(`${n.name}: regerando a partir do desenho`);
    watchJob(n);  // a versão anterior continua nas versões do nó (takes)
  } catch (err) {
    toast(`Não deu para regerar: ${err.message}`);
  } finally { btn.disabled = false; }
}

// Modo Relight: a seta de luz vira o vetor da direção (origem = de onde a luz vem, ponta = para onde vai).
function openLight(n) {
  const src = frameOf(incoming(n.id)[0]);
  if (!src) return toast('Ligue uma imagem na entrada');
  dz.mode = 'light';
  dz.node = n;
  dz.frame = src;
  const d = n.lightDir;
  dz.layers = [{ color: 'light', label: DZ_COLORS.light.label, prompt: '', shapes: d ? [{ type: 'path', points: [[d[0], d[1]], [d[2], d[3]]] }] : [] }];
  dz.active = 'light';
  dz.result = null;
  dz.tool = 'draw';
  dz.history = [];
  setChrome('light');
  $('#dzStale').hidden = true;
  $('#dzTitle').textContent = n.name;
  dz.img = new Image();
  dz.img.onload = () => { dzSize(); dzRender(); };
  dz.img.src = src;
  $('#dzImg').src = src;
  $('#dzDialog').showModal();
  renderDzCards();
  renderDzResult();
  setTool('draw');
}

function saveLight() {
  const n = dz.node;
  const sh = layerOf('light')?.shapes[0];
  n.lightDir = sh ? [sh.points[0][0], sh.points[0][1], sh.points.at(-1)[0], sh.points.at(-1)[1]] : null;
  renderNode(n);
  save();
  $('#dzDialog').close();
  toast(n.lightDir ? 'Direção da luz salva' : 'Sem direção: a luz segue só o preset');
}

async function saveDirection() {
  if (dz.mode === 'light') return saveLight();
  if (dz.mode === 'sketch') return saveSketch();
  const n = dz.node;
  const btn = $('#dzSave');
  btn.disabled = true;
  try {
    if (activeLayers().length && !dz.result) await interpretDirection().catch((e) => toast(`Não deu para interpretar agora: ${e.message}`));
    // o desenho vai no próprio documento do node (JPEG pequeno): nenhum arquivo é criado em Meus arquivos
    const diagram = activeLayers().length ? annotatedImage(640) : null;
    n.direction = activeLayers().length ? { frame: dz.frame, layers: dz.layers, result: dz.result, diagram_url: diagram } : null;
    n.camAnalysis = n.direction?.result?.camera_move ? { move: n.direction.result.camera_move } : null; // usado nas transições
    n.autoKey = null;
    renderNode(n);
    save();
    $('#dzDialog').close();
    toast(n.direction ? 'Direção salva: entra no prompt do vídeo' : 'Direção removida: a IA escolhe a câmera');
  } finally { btn.disabled = false; }
}
