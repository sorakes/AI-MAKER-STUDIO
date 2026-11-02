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

// Contorno fechado vira região cheia (mesma regra de outpaint.fill_enclosed no servidor): o que não é alcançável
// da borda do quadro e não está pintado é o interior. Trabalha em até 512 px e amplia; traço aberto não muda.
function fillEnclosed(bin, W, H) {
  const step = Math.max(1, Math.ceil(Math.max(W, H) / 512));
  const sw = Math.ceil(W / step), sh = Math.ceil(H / step);
  const small = new Uint8Array(sw * sh);
  for (let y = 0; y < H; y++) {
    const row = ((y / step) | 0) * sw;
    for (let x = 0; x < W; x++) if (bin[y * W + x]) small[row + ((x / step) | 0)] = 1;
  }
  const r = Math.max(1, Math.round(0.01 * Math.max(sw, sh)));  // fechamento de ~1% da dimensão
  const closed = erodeGrid(dilateGrid(small, sw, sh, r), sw, sh, r);
  const out = new Uint8Array(sw * sh);  // 1 = alcançado da borda do quadro
  const queue = new Int32Array(sw * sh);
  let head = 0, tail = 0;
  const push = (i) => { if (!closed[i] && !out[i]) { out[i] = 1; queue[tail++] = i; } };
  for (let x = 0; x < sw; x++) { push(x); push((sh - 1) * sw + x); }
  for (let y = 0; y < sh; y++) { push(y * sw); push(y * sw + sw - 1); }
  while (head < tail) {
    const i = queue[head++], x = i % sw, y = (i / sw) | 0;
    if (x > 0) push(i - 1);
    if (x < sw - 1) push(i + 1);
    if (y > 0) push(i - sw);
    if (y < sh - 1) push(i + sw);
  }
  const res = Uint8Array.from(bin);
  for (let y = 0; y < H; y++) {
    const row = ((y / step) | 0) * sw;
    for (let x = 0; x < W; x++) {
      const k = y * W + x;
      if (res[k]) continue;
      const si = row + ((x / step) | 0);
      if (!closed[si] && !out[si]) res[k] = 1;  // interior: livre e não alcançado de fora
    }
  }
  return res;
}

// dilatação e erosão quadradas (raio r), separáveis: linhas e depois colunas
function dilateGrid(g, w, h, r) { return morphGrid(g, w, h, r, 1); }
function erodeGrid(g, w, h, r) { return morphGrid(g, w, h, r, 0); }
function morphGrid(g, w, h, r, dilate) {
  const tmp = new Uint8Array(w * h), out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let v = dilate ? 0 : 1;
      for (let dx = -r; dx <= r; dx++) {
        const xx = x + dx;  // fora do quadro: dilatação ignora, erosão também (como o filtro do Pillow)
        const s = xx < 0 || xx >= w ? (dilate ? 0 : 1) : g[y * w + xx];
        if (dilate ? s : !s) { v = dilate ? 1 : 0; break; }
      }
      tmp[y * w + x] = v;
    }
  }
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let v = dilate ? 0 : 1;
      for (let dy = -r; dy <= r; dy++) {
        const yy = y + dy;
        const s = yy < 0 || yy >= h ? (dilate ? 0 : 1) : tmp[yy * w + x];
        if (dilate ? s : !s) { v = dilate ? 1 : 0; break; }
      }
      out[y * w + x] = v;
    }
  }
  return out;
}

function maskDataUrl() {
  const W = mk.mask.width;
  const H = mk.mask.height;
  const d = mk.ctx.getImageData(0, 0, W, H).data;
  let bin = new Uint8Array(W * H);
  let painted = 0;
  for (let p = 0, i = 3; p < W * H; p++, i += 4) if (d[i] > 127) { bin[p] = 1; painted++; }
  if (!painted) return null;
  if (mk.fill !== false) bin = fillEnclosed(bin, W, H);  // padrão ligado: o interior do contorno entra
  const out = document.createElement('canvas');
  out.width = W;
  out.height = H;
  const og = out.getContext('2d');
  const o = og.createImageData(W, H);
  for (let p = 0; p < W * H; p++) {
    const v = bin[p] ? 255 : 0, i = p * 4;
    o.data[i] = v; o.data[i + 1] = v; o.data[i + 2] = v; o.data[i + 3] = 255;
  }
  og.putImageData(o, 0, 0);
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
  n.mask_fill = mk.fill !== false;  // o servidor preenche contornos fechados só se esta opção estiver ligada
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
  $('#maskFill')?.addEventListener('change', (e) => { mk.fill = e.target.checked; });
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
