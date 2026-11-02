// Verificação de pixels do overlay de marcação (Chrome com CDP). Uso:
//   node scripts/check_overlay_pixels.mjs <url-do-studio> <cookie studio_session> <imagem-url-no-servidor> [pasta-de-screenshots]
// Pinta uma região no editor de máscara do node Expandir/Editar, salva, e AMOSTRA os pixels do canvas de overlay
// (getImageData): conta pixels magenta com alfa > 0 e mede a caixa deles. Depois tira screenshot do node e
// repete tudo após recarregar o canvas. Passa se o magenta aparece, na região pintada, antes e depois do reload.
// Saída: JSON. Código 1 se o overlay não aparecer.
import fs from "node:fs";
import path from "node:path";

const [, , pageUrl, cookieValue, imageUrl, shotDir] = process.argv;
if (!pageUrl || !cookieValue || !imageUrl) {
  console.error("uso: node scripts/check_overlay_pixels.mjs <url> <cookie> <imagem-url> [pasta]");
  process.exit(2);
}
const targets = await (await fetch("http://127.0.0.1:9333/json/list")).json();
const page = targets.find((t) => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
const send = (method, params = {}) => new Promise((res) => {
  const i = ++id;
  pending.set(i, res);
  ws.send(JSON.stringify({ id: i, method, params }));
});
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); }
};
await new Promise((r) => (ws.onopen = r));
for (const d of ["Page", "Runtime", "Network"]) await send(`${d}.enable`);
const origin = new URL(pageUrl).origin;
await send("Network.setCookie", { name: "studio_session", value: cookieValue, url: `${origin}/`, path: "/", httpOnly: true });
const evalIn = async (expression) => {
  const r = await send("Runtime.evaluate", { returnByValue: true, awaitPromise: true, expression });
  if (r.exceptionDetails) return { exception: (r.exceptionDetails.exception?.description || r.exceptionDetails.text || "").slice(0, 300) };
  return r.result?.value;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// amostra o overlay: pixels magenta com alfa > 0 e a caixa deles (em % do canvas)
const SAMPLE = `(() => {
  const el = document.querySelector('.cv-node[data-id="' + window.__pixNodeId + '"]');
  const cvs = el?.querySelector('.cv-preview .paint-overlay');
  if (!el) return { overlay: false, why: 'nó não está no DOM' };
  if (!cvs) return { overlay: false, why: 'sem canvas de overlay', hasPreview: !!el.querySelector('.cv-preview'), badge: !!el.querySelector('.paint-badge'), maskSaved: !!cv.nodes.find((x) => x.id === window.__pixNodeId)?.mask };
  if (!cvs.width) return { overlay: false, why: 'canvas com largura 0' };
  const d = cvs.getContext('2d').getImageData(0, 0, cvs.width, cvs.height).data;
  let n = 0, x0 = cvs.width, y0 = cvs.height, x1 = -1, y1 = -1;
  for (let i = 0; i < d.length; i += 4) {
    if (d[i + 3] > 0 && d[i] > 150 && d[i + 1] < 140 && d[i + 2] > 150) {
      n++; const p = i / 4, x = p % cvs.width, y = Math.floor(p / cvs.width);
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
  }
  const W = cvs.width, H = cvs.height;
  return { overlay: true, size: [W, H], magentaPixels: n,
    boxPct: n ? [Math.round(x0 / W * 100), Math.round(y0 / H * 100), Math.round(x1 / W * 100), Math.round(y1 / H * 100)] : null };
})()`;

const navigate = async () => {
  await send("Page.navigate", { url: `${origin}/` });
  await sleep(8000);
};
await navigate();
// nó de imagem ligado ao editor Expandir/Editar
const ids = await evalIn(`(async () => {
  cv.nodes.length = 0; cv.edges.length = 0;
  const src = { id: uid('media'), type: 'media', x: 40, y: 120, name: 'Imagem', media: { kind: 'image', url: '${imageUrl}', thumb: '${imageUrl}', name: 'imagem.png' } };
  const ed = PALETTE.edit.make({ x: 420, y: 120 });
  ed.name = 'Expandir / editar região'; ed.output = null; ed.status = 'idle';
  cv.nodes.push(src, ed); cv.edges.push({ id: uid('e'), from: src.id, to: ed.id });
  renderAll(); save();
  await new Promise((r) => setTimeout(r, 900));
  window.__pixNodeId = ed.id; return ed.id; })()`);
if (ids?.exception) { console.error(ids.exception); process.exit(1); }

// pinta uma região (30%–60% em x e y) pelo editor de máscara real e salva
const paint = await evalIn(`(async () => {
  const n = cv.nodes.find((x) => x.id === window.__pixNodeId);
  openMask(n);
  for (let i = 0; i < 50 && !(mk.ctx && mk.mask && mk.mask.width); i++) await new Promise((r) => setTimeout(r, 100));
  if (!(mk.ctx && mk.mask)) return { ok: false, why: 'editor de máscara não carregou a imagem', src: upscaleSource(n) || null, incoming: incoming(n.id).length, dialogOpen: !!document.querySelector('#maskDialog')?.open };
  mk.ctx.fillStyle = '#fff';
  mk.ctx.fillRect(mk.mask.width * 0.3, mk.mask.height * 0.3, mk.mask.width * 0.3, mk.mask.height * 0.3);
  const before = !!n.mask;
  saveMask();
  return { ok: !!n.mask, hadMaskBefore: before, maskW: mk.mask.width, maskH: mk.mask.height }; })()`);
if (!paint?.ok) console.error('pintura:', JSON.stringify(paint));
await sleep(1200);

const shot = async (name) => {
  if (!shotDir) return null;
  const rect = await evalIn(`(() => { const el = document.querySelector('.cv-node[data-id="' + window.__pixNodeId + '"]'); const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; })()`);
  const { data } = await send("Page.captureScreenshot", { format: "png", clip: { ...rect, scale: 1 } });
  const file = path.join(shotDir, name);
  fs.writeFileSync(file, Buffer.from(data, "base64"));
  return file;
};
await evalIn(`(() => { if (typeof fitView === 'function') fitView(); return true; })()`);
await sleep(700);
const before = await evalIn(SAMPLE);
const beforeShot = await shot("overlay-antes-do-reload.png");

// recarrega o canvas: o overlay tem de voltar sozinho
await navigate();
await evalIn(`window.__pixNodeId = cv.nodes.find((n) => n.type === 'edit')?.id`);
await sleep(1500);
await evalIn(`(() => { if (typeof fitView === 'function') fitView(); return true; })()`);
await sleep(700);
const after = await evalIn(SAMPLE);
const afterShot = await shot("overlay-depois-do-reload.png");

const out = { paint, before, after, screenshots: { beforeShot, afterShot } };
console.log(JSON.stringify(out, null, 2));
ws.close();
const ok = before?.magentaPixels > 0 && after?.magentaPixels > 0;
process.exit(ok ? 0 : 1);
