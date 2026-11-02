// Máscara do "Editar região" (inpaint): pincel com tamanho ajustável, borracha e desfazer.
// Reaproveita o diálogo e o layout do editor de direção (dz-dialog, dz-stage, dz-canvaswrap). A máscara é
// desenhada num canvas do tamanho real da imagem; ao salvar vira PNG preto e branco (branco = região a editar).

const mk = { node: null, mask: null, ctx: null, tool: 'brush', size: 28, drawing: false, last: null, history: [], dispW: 0, dispH: 0 };
const MASK_COLOR = '#ff00ff';

function setMaskTool(t) {
  mk.tool = t;
  $('#maskBrush').setAttribute('aria-pressed', String(t === 'brush'));
  $('#maskEraser').setAttribute('aria-pressed', String(t === 'eraser'));
  $('#maskCanvas').style.cursor = t === 'eraser' ? 'cell' : 'crosshair';
}

// Ajusta a imagem e o canvas de exibição à área do diálogo (mesma conta do editor de direção).
function fitMask() {
  const img = $('#maskImg');
  const cvs = $('#maskCanvas');
  const box = $('#maskStage').getBoundingClientRect();
  const ar = mk.mask.width / mk.mask.height;
  const w = Math.max(200, Math.min(box.width - 2, (box.height - 2) * ar));
  const h = w / ar;
  for (const el of [img, cvs]) { el.style.width = `${w}px`; el.style.height = `${h}px`; }
  cvs.width = Math.round(w * devicePixelRatio);
  cvs.height = Math.round(h * devicePixelRatio);
  mk.dispW = cvs.width;
  mk.dispH = cvs.height;
}

function redrawMask() {
  const cvs = $('#maskCanvas');
  const g = cvs.getContext('2d');
  g.clearRect(0, 0, cvs.width, cvs.height);
  g.drawImage(mk.mask, 0, 0, cvs.width, cvs.height);
}

// Carrega uma máscara salva (PNG preto e branco) de volta no canvas, como traço magenta.
function loadMaskBW(dataUrl) {
  const im = new Image();
  im.onload = () => {
    const tmp = document.createElement('canvas');
    tmp.width = mk.mask.width;
    tmp.height = mk.mask.height;
    const tg = tmp.getContext('2d');
    tg.drawImage(im, 0, 0, tmp.width, tmp.height);
    const d = tg.getImageData(0, 0, tmp.width, tmp.height);
    for (let i = 0; i < d.data.length; i += 4) {
      const on = d.data[i] > 127;
      d.data[i] = 255; d.data[i + 1] = 0; d.data[i + 2] = 255; d.data[i + 3] = on ? 255 : 0;
    }
    mk.ctx.putImageData(d, 0, 0);
    redrawMask();
  };
  im.src = dataUrl;
}

function openMask(n) {
  const src = upscaleSource(n);
  if (!src) return toast('Ligue uma imagem na entrada');
  mk.node = n;
  mk.history = [];
  mk.tool = 'brush';
  const img = new Image();
  img.onload = () => {
    mk.mask = document.createElement('canvas');
    mk.mask.width = img.naturalWidth;
    mk.mask.height = img.naturalHeight;
    mk.ctx = mk.mask.getContext('2d');
    fitMask();
    if (n.mask) loadMaskBW(n.mask); else redrawMask();
  };
  img.src = src;
  $('#maskImg').src = src;
  $('#maskTitle').textContent = n.name;
  $('#maskDialog').showModal();
  setMaskTool('brush');
}

function maskPos(e) {
  const r = $('#maskCanvas').getBoundingClientRect();
  return [Math.max(0, Math.min(mk.mask.width, ((e.clientX - r.left) / r.width) * mk.mask.width)),
    Math.max(0, Math.min(mk.mask.height, ((e.clientY - r.top) / r.height) * mk.mask.height))];
}

function snapshotMask() {
  mk.history.push(mk.mask.toDataURL('image/png'));
  if (mk.history.length > 20) mk.history.shift();
}

function undoMask() {
  const prev = mk.history.pop();
  if (!prev) return toast('Nada para desfazer');
  const im = new Image();
  im.onload = () => { mk.ctx.clearRect(0, 0, mk.mask.width, mk.mask.height); mk.ctx.drawImage(im, 0, 0); redrawMask(); };
  im.src = prev;
}

function maskDataUrl() {
  const W = mk.mask.width;
  const H = mk.mask.height;
  const d = mk.ctx.getImageData(0, 0, W, H);
  const out = document.createElement('canvas');
  out.width = W;
  out.height = H;
  const og = out.getContext('2d');
  const o = og.createImageData(W, H);
  let painted = 0;
  for (let i = 0; i < d.data.length; i += 4) {
    const v = d.data[i + 3] > 127 ? 255 : 0;
    if (v) painted++;
    o.data[i] = v; o.data[i + 1] = v; o.data[i + 2] = v; o.data[i + 3] = 255;
  }
  og.putImageData(o, 0, 0);
  if (!painted) return null;
  // máscara compacta (até 512 px) no documento do node; o servidor reajusta ao tamanho da imagem
  const k = Math.min(1, 512 / Math.max(W, H));
  const small = document.createElement('canvas');
  small.width = Math.max(1, Math.round(W * k));
  small.height = Math.max(1, Math.round(H * k));
  small.getContext('2d').drawImage(out, 0, 0, small.width, small.height);
  return small.toDataURL('image/png');
}

function saveMask() {
  const url = maskDataUrl();
  if (!url) return toast('Pinte a região que será editada');
  const n = mk.node;
  n.mask = url;
  n.maskAt = Date.now();
  $('#maskDialog').close();
  renderNode(n);
  save();
  toast('Região salva: agora descreva o que colocar nela');
}

function bindMask() {
  const cvs = $('#maskCanvas');
  cvs.addEventListener('pointerdown', (e) => {
    if (!mk.ctx) return;
    snapshotMask();
    mk.drawing = true;
    mk.last = maskPos(e);
    cvs.setPointerCapture?.(e.pointerId);
    strokeMask(mk.last, mk.last);
  });
  cvs.addEventListener('pointermove', (e) => {
    if (!mk.drawing) return;
    const p = maskPos(e);
    strokeMask(mk.last, p);
    mk.last = p;
    redrawMask();
  });
  const end = () => { mk.drawing = false; };
  cvs.addEventListener('pointerup', end);
  cvs.addEventListener('pointercancel', end);
  $('#maskBrush').onclick = () => setMaskTool('brush');
  $('#maskEraser').onclick = () => setMaskTool('eraser');
  $('#maskSize').addEventListener('input', (e) => { mk.size = +e.target.value; });
  $('#maskUndo').onclick = undoMask;
  $('#maskClear').onclick = () => { snapshotMask(); mk.ctx.clearRect(0, 0, mk.mask.width, mk.mask.height); redrawMask(); };
  $('#maskCancel').onclick = () => $('#maskDialog').close();
  $('#maskSave').onclick = saveMask;
  window.addEventListener('resize', () => { if ($('#maskDialog').open && mk.mask) { fitMask(); redrawMask(); } });
  window.addEventListener('keydown', (e) => {
    if (!$('#maskDialog').open) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); undoMask(); }
    if (e.key.toLowerCase() === 'b') setMaskTool('brush');
    if (e.key.toLowerCase() === 'e') setMaskTool('eraser');
  });
}

// Traço no canvas da máscara (coordenadas reais da imagem). Tamanho do pincel em pixels da tela.
function strokeMask(a, b) {
  const scale = mk.mask.width / (mk.dispW || mk.mask.width);
  const g = mk.ctx;
  g.save();
  g.lineCap = 'round';
  g.lineJoin = 'round';
  g.lineWidth = mk.size * scale * (devicePixelRatio || 1);
  g.strokeStyle = MASK_COLOR;
  g.globalCompositeOperation = mk.tool === 'eraser' ? 'destination-out' : 'source-over';
  g.beginPath();
  g.moveTo(a[0], a[1]);
  g.lineTo(b[0], b[1]);
  g.stroke();
  g.restore();
}

bindMask();
