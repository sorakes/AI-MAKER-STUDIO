// Marcações sobre o preview do node: região pintada (magenta 40%) e traços dos desenhos.
// Tudo vem do documento do node (n.mask em PNG pequeno, desenhos normalizados): nenhum arquivo é criado,
// então a marcação persiste ao recarregar e nunca aparece em Meus arquivos.

const PAINT_MAGENTA = [217, 70, 239];

// O preview usa object-fit: cover; a marcação é desenhada com o mesmo enquadramento da imagem.
function coverBox(boxW, boxH, imgW, imgH) {
  const s = Math.max(boxW / imgW, boxH / imgH);
  const w = imgW * s, h = imgH * s;
  return { s, dx: (boxW - w) / 2, dy: (boxH - h) / 2 };
}

function maskToOverlay(maskImg) {
  const c = document.createElement('canvas');
  c.width = maskImg.naturalWidth; c.height = maskImg.naturalHeight;
  const g = c.getContext('2d');
  g.drawImage(maskImg, 0, 0);
  const d = g.getImageData(0, 0, c.width, c.height);
  for (let i = 0; i < d.data.length; i += 4) {
    const on = d.data[i] > 127;  // máscara: branco = pintado
    d.data[i] = PAINT_MAGENTA[0]; d.data[i + 1] = PAINT_MAGENTA[1]; d.data[i + 2] = PAINT_MAGENTA[2];
    d.data[i + 3] = on ? 102 : 0;  // 40%
  }
  g.putImageData(d, 0, 0);
  return c;
}

function drawStrokes(g, layers, map) {
  layers.forEach((l) => {
    const hex = (typeof DZ_COLORS !== 'undefined' && DZ_COLORS[l.color]?.hex) || '#d946ef';
    l.shapes.forEach((sh) => {
      if (!sh.points || sh.points.length < 2) return;
      g.save();
      g.strokeStyle = hex; g.lineWidth = 3; g.lineCap = 'round'; g.lineJoin = 'round'; g.globalAlpha = 0.9;
      g.beginPath();
      sh.points.forEach(([x, y], i) => {
        const [px, py] = map(x, y);
        if (i === 0) g.moveTo(px, py); else g.lineTo(px, py);
      });
      g.stroke();
      g.restore();
    });
  });
}

function bindPaintOverlay(el, n) {
  requestAnimationFrame(() => drawPaintOverlay(el, n));  // depois de inserido no DOM (tem tamanho)
}

function drawPaintOverlay(el, n) {
  const pv = el.querySelector('.cv-preview');
  if (!pv) return;
  pv.querySelector('.paint-overlay')?.remove();
  pv.querySelector('.paint-badge')?.remove();
  pv.querySelector('.paint-clear')?.remove();
  const layers = [...(n.direction?.layers || []), ...(n.sketch?.layers || [])].filter((l) => l.shapes?.length);
  if (!n.mask && !layers.length) return;
  const cvs = document.createElement('canvas');
  cvs.className = 'paint-overlay';
  cvs.setAttribute('data-st', 'position:absolute;inset:0;width:100%;height:100%;object-fit:cover;pointer-events:none;z-index:2');
  pv.appendChild(cvs);
  const bw = pv.clientWidth, bh = pv.clientHeight;
  if (!bw || !bh) return;  // ainda sem layout: o ResizeObserver desenha quando ganhar tamanho
  cvs.width = bw; cvs.height = bh;
  const g = cvs.getContext('2d');
  // referência de enquadramento: a imagem do preview (ghost da entrada ou a saída). Sem ela carregada, a máscara
  // tem a mesma proporção da imagem de origem e serve de referência.
  const media = pv.querySelector('img, video');
  const loaded = media && (media.naturalWidth || media.videoWidth);
  const ref = loaded ? { w: media.naturalWidth || media.videoWidth, h: media.naturalHeight || media.videoHeight } : null;
  if (media && !loaded) media.addEventListener('load', () => drawPaintOverlay(el, n), { once: true });
  const drawAll = (imgW, imgH) => {
    const { s, dx, dy } = coverBox(bw, bh, imgW, imgH);
    const map = (x, y) => [dx + x * imgW * s, dy + y * imgH * s];
    if (n.mask) {
      const mi = new Image();
      mi.onload = () => {
        const tint = maskToOverlay(mi);
        g.drawImage(tint, dx, dy, imgW * s, imgH * s);  // a máscara (512 px) é esticada ao quadro da imagem
        drawStrokes(g, layers, map);
      };
      mi.src = n.mask;
    } else drawStrokes(g, layers, map);
  };
  if (ref) drawAll(ref.w, ref.h);
  else if (n.mask) {
    const mi = new Image();
    mi.onload = () => drawAll(mi.naturalWidth, mi.naturalHeight);  // sem imagem: a própria máscara define o quadro
    mi.src = n.mask;
  } else drawAll(bw, bh);
  const badge = document.createElement('span');
  badge.className = 'paint-badge';
  badge.setAttribute('data-st', 'position:absolute;left:8px;top:8px;z-index:3;padding:3px 8px;border-radius:999px;background:rgba(217,70,239,.85);color:#fff;font:600 .66rem var(--font-sans);pointer-events:none');
  badge.textContent = n.mask && layers.length ? 'Região pintada · Desenho' : n.mask ? 'Região pintada' : 'Desenho';
  pv.appendChild(badge);
  if (n.mask) {
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.className = 'paint-clear';
    clear.setAttribute('data-act', 'mask-clear');
    clear.setAttribute('data-st', 'position:absolute;right:8px;bottom:8px;z-index:3;padding:4px 9px;border-radius:8px;border:1px solid rgba(255,255,255,.4);background:rgba(0,0,0,.55);color:#fff;font:.68rem var(--font-sans);cursor:pointer');
    clear.textContent = 'Limpar região';
    pv.appendChild(clear);
    clear.addEventListener('click', () => { n.mask = null; save(); renderNode(n); toast('Região limpa'); });
  }
  // redesenha quando o preview muda de tamanho (zoom, redimensionar o node)
  if (!pv._paintRO && window.ResizeObserver) {
    pv._paintRO = new ResizeObserver(() => { if (pv.isConnected) drawPaintOverlay(el, n); });
    pv._paintRO.observe(pv);
  }
}
