// Preenchimento de contorno (Chrome com CDP). Uso:
//   node scripts/check_fill_pixels.mjs <url-do-studio> <cookie studio_session> <imagem-url-no-servidor> [pasta]
// Desenha um círculo (só o traço) no editor de máscara do node Editar região, salva e AMOSTRA os pixels magenta do
// overlay (getImageData): com 'Preencher dentro do contorno' ligado, o centro do círculo tem de ficar magenta;
// desligado, o centro não pode ficar. Depois confere o recarregamento. Código 1 se alguma condição falhar.
import fs from "node:fs";
import path from "node:path";

const [, , pageUrl, cookieValue, imageUrl, shotDir] = process.argv;
if (!pageUrl || !cookieValue || !imageUrl) {
  console.error("uso: node scripts/check_fill_pixels.mjs <url> <cookie> <imagem-url> [pasta]");
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

// magenta no overlay: conta os pixels e diz se o centro do círculo está pintado
const SAMPLE = `(() => {
  const el = document.querySelector('.cv-node[data-id="' + window.__fillNodeId + '"]');
  const cvs = el?.querySelector('.cv-preview .paint-overlay');
  if (!cvs || !cvs.width) return { overlay: false };
  const W = cvs.width, H = cvs.height;
  const d = cvs.getContext('2d').getImageData(0, 0, W, H).data;
  let n = 0;
  for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 0 && d[i] > 150 && d[i + 1] < 140 && d[i + 2] > 150) n++;
  const c = cvs.getContext('2d').getImageData(Math.floor(W / 2), Math.floor(H / 2), 1, 1).data;
  return { overlay: true, size: [W, H], magentaPixels: n, centerMagenta: c[3] > 0 && c[0] > 150 && c[1] < 140 && c[2] > 150 };
})()`;

const navigate = async () => {
  await send("Page.navigate", { url: `${origin}/` });
  await sleep(8000);
};
await navigate();
const ids = await evalIn(`(async () => {
  cv.nodes.length = 0; cv.edges.length = 0;
  const src = { id: uid('media'), type: 'media', x: 40, y: 120, name: 'Imagem', media: { kind: 'image', url: '${imageUrl}', thumb: '${imageUrl}', name: 'imagem.png' } };
  const ed = PALETTE.edit.make({ x: 420, y: 120 });
  ed.name = 'Editar região'; ed.op = 'inpaint'; ed.output = null; ed.status = 'idle';
  cv.nodes.push(src, ed); cv.edges.push({ id: uid('e'), from: src.id, to: ed.id });
  renderAll(); save();
  await new Promise((r) => setTimeout(r, 900));
  window.__fillNodeId = ed.id; return ed.id; })()`);
if (ids?.exception) { console.error(ids.exception); process.exit(1); }

// desenha só o traço de um círculo (anel) no editor real, salva com a opção indicada
const paintRing = (fill) => evalIn(`(async () => {
  const n = cv.nodes.find((x) => x.id === window.__fillNodeId);
  openMask(n);
  for (let i = 0; i < 50 && !(mk.ctx && mk.mask && mk.mask.width); i++) await new Promise((r) => setTimeout(r, 100));
  if (!(mk.ctx && mk.mask)) return { ok: false, why: 'editor não carregou' };
  const cb = document.getElementById('maskFill');
  if (cb) cb.checked = ${fill};
  mk.fill = ${fill};
  const W = mk.mask.width, H = mk.mask.height;
  mk.ctx.clearRect(0, 0, W, H);
  mk.ctx.strokeStyle = '#fff';
  mk.ctx.lineWidth = W * 0.03;
  mk.ctx.beginPath();
  mk.ctx.arc(W / 2, H / 2, W * 0.22, 0, Math.PI * 2);
  mk.ctx.stroke();
  saveMask();
  return { ok: !!n.mask, fillOpt: n.mask_fill, W, H }; })()`);

const shot = async (name) => {
  if (!shotDir) return null;
  const rect = await evalIn(`(() => { const el = document.querySelector('.cv-node[data-id="' + window.__fillNodeId + '"]'); const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; })()`);
  const { data } = await send("Page.captureScreenshot", { format: "png", clip: { ...rect, scale: 1 } });
  const file = path.join(shotDir, name);
  fs.writeFileSync(file, Buffer.from(data, "base64"));
  return file;
};
await evalIn(`(() => { if (typeof fitView === 'function') fitView(); return true; })()`);

const onPaint = await paintRing(true);
await sleep(1200);
await evalIn(`(() => { if (typeof fitView === 'function') fitView(); return true; })()`);
await sleep(600);
const on = await evalIn(SAMPLE);
const onShot = await shot("fill-ligado.png");

const offPaint = await paintRing(false);
await sleep(1200);
await evalIn(`(() => { if (typeof fitView === 'function') fitView(); return true; })()`);
await sleep(600);
const off = await evalIn(SAMPLE);
const offShot = await shot("fill-desligado.png");

// recarrega: com a opção ligada salva, o interior tem de voltar
await evalIn(`(async () => { const n = cv.nodes.find((x) => x.id === window.__fillNodeId); n.mask_fill = true; return true; })()`);
const out = { onPaint, on, offPaint, off, screenshots: { onShot, offShot } };
console.log(JSON.stringify(out, null, 2));
ws.close();
const ok = on?.centerMagenta === true && off?.centerMagenta === false && on?.magentaPixels > off?.magentaPixels * 2;
process.exit(ok ? 0 : 1);
