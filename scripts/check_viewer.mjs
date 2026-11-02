// Visualizador em modal (Chrome com CDP). Uso: node scripts/check_viewer.mjs <url-do-studio> <cookie studio_session>
// Confere: duplo clique numa prévia de node abre o modal; ← troca de item; Esc fecha; o foco volta à prévia;
// duplo clique num item do painel "Meus arquivos" também abre; Esc fecha. Código 1 se alguma condição falhar.
const [, , pageUrl, cookieValue] = process.argv;
if (!pageUrl || !cookieValue) {
  console.error("uso: node scripts/check_viewer.mjs <url> <cookie>");
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
for (const d of ["Page", "Runtime", "Network", "Input"]) await send(`${d}.enable`);
const origin = new URL(pageUrl).origin;
await send("Network.setCookie", { name: "studio_session", value: cookieValue, url: `${origin}/`, path: "/", httpOnly: true });
const evalIn = async (expression) => {
  const r = await send("Runtime.evaluate", { returnByValue: true, awaitPromise: true, expression });
  if (r.exceptionDetails) return { exception: (r.exceptionDetails.exception?.description || r.exceptionDetails.text || "").slice(0, 300) };
  return r.result?.value;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const key = async (k, code, vk) => {
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: k, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: k, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk });
  await sleep(250);
};
const ARROW_LEFT = ["ArrowLeft", "ArrowLeft", 37];
const ESC = ["Escape", "Escape", 27];

await send("Emulation.setDeviceMetricsOverride", { width: 1400, height: 900, deviceScaleFactor: 1, mobile: false });
await send("Page.navigate", { url: `${origin}/` });
await sleep(8000);

// duas imagens do próprio usuário (ou a mesma duas vezes se só houver uma): a lista do node com takes
const setup = await evalIn(`(async () => {
  let files = (await (await fetch('/api/files')).json()).files.filter((f) => f.kind === 'image' && !f.asset);
  if (files.length < 2) {  // duas imagens distintas, para a seta ← trocar de item de verdade
    const c = document.createElement('canvas'); c.width = 96; c.height = 64;
    const g = c.getContext('2d'); g.fillStyle = '#c03'; g.fillRect(0, 0, 96, 64);
    const blob = await new Promise((r) => c.toBlob(r, 'image/png'));
    await uploadFile(new File([blob], 'viewer-check-b.png', { type: 'image/png' }));
    files = (await (await fetch('/api/files')).json()).files.filter((f) => f.kind === 'image' && !f.asset);
  }
  if (files.length < 2) return { error: 'precisa de 2 imagens' };
  const a = files[0], b = files[1];
  cv.nodes.length = 0; cv.edges.length = 0;
  const ed = PALETTE.edit.make({ x: 120, y: 120 });
  ed.name = 'Visualizador'; ed.op = 'outpaint'; ed.status = 'done';
  ed.takes = [{ kind: 'image', url: a.url, thumb: a.thumb, name: a.name }, { kind: 'image', url: b.url, thumb: b.thumb, name: b.name }];
  ed.output = ed.takes[1];
  cv.nodes.push(ed);
  renderAll();
  await new Promise((r) => setTimeout(r, 500));
  window.__viewerNode = ed.id;
  return { id: ed.id, takes: ed.takes.length, first: a.url, second: b.url };
})()`);
if (setup?.exception || setup?.error) { console.error(setup.exception || setup.error); process.exit(1); }

// 1) duplo clique na prévia do node abre o modal, na mídia que está no node
const previewSel = `.cv-node[data-id="${setup.id}"] .cv-preview img`;
const opened = await evalIn(`(() => {
  const img = document.querySelector('${previewSel}');
  if (!img) return { error: 'prévia ausente' };
  img.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
  return { ok: true };
})()`);
await sleep(600);
const afterOpen = await evalIn(`(() => {
  const d = document.getElementById('viewerDialog');
  return { open: d.open, name: document.getElementById('viewerName').textContent, meta: document.getElementById('viewerMeta').textContent };
})()`);

// 2) ← vai para o item anterior (o node abriu no 2º take)
await send("Input.dispatchKeyEvent", { type: "keyDown", key: "ArrowLeft", code: "ArrowLeft", windowsVirtualKeyCode: 37 });
await send("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowLeft", code: "ArrowLeft", windowsVirtualKeyCode: 37 });
await sleep(400);
const afterArrow = await evalIn(`({ meta: document.getElementById('viewerMeta').textContent, prevDisabled: document.getElementById('viewerPrev').disabled })`);

// 3) Esc fecha e o foco volta à prévia (ela ganhou tabindex por script)
await key(...ESC);
await sleep(300);
const afterEsc = await evalIn(`(() => ({ open: document.getElementById('viewerDialog').open,
  focusedIsPreview: !!(document.activeElement && document.activeElement.closest && document.activeElement.closest('.cv-node[data-id="${setup.id}"]')) }))()`);

// 3b) vídeo: duplo clique na prévia de vídeo abre o modal com controles nativos; Esc fecha
const vid = await evalIn(`(async () => {
  const f = (await (await fetch('/api/files')).json()).files.find((x) => x.kind === 'video' && !x.asset);
  if (!f) return { skipped: true };
  const ed = PALETTE.edit.make({ x: 520, y: 120 });
  ed.name = 'Vídeo'; ed.op = 'outpaint'; ed.status = 'done';
  ed.output = { kind: 'video', url: f.url, thumb: f.thumb, name: f.name }; ed.takes = [ed.output];
  cv.nodes.push(ed); renderAll();
  await new Promise((r) => setTimeout(r, 600));
  return { id: ed.id };
})()`);
let vidOpen = { skipped: true }, vidState = { skipped: true }, vidClosed = { skipped: true };
if (!vid?.skipped && !vid?.exception) {
  vidOpen = await evalIn(`(() => {
    const v = document.querySelector('.cv-node[data-id="${vid.id}"] .cv-preview video');
    if (!v) return { error: 'prévia de vídeo ausente' };
    v.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
    return { ok: true };
  })()`);
  await sleep(700);
  vidState = await evalIn(`({ open: document.getElementById('viewerDialog').open, hasVideo: !!document.querySelector('#viewerStage video[controls]') })`);
  await key(...ESC);
  await sleep(300);
  vidClosed = await evalIn(`({ open: document.getElementById('viewerDialog').open })`);
}

// 4) painel "Meus arquivos": duplo clique num item de imagem abre o modal; Esc fecha
const libOpen = await evalIn(`(() => {
  const item = document.querySelector('.cv-lib-item.image');
  if (!item) return { error: 'sem item de imagem no painel' };
  item.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
  return { ok: true };
})()`);
await sleep(600);
const libState = await evalIn(`({ open: document.getElementById('viewerDialog').open })`);
await key(...ESC);
await sleep(300);
const libClosed = await evalIn(`({ open: document.getElementById('viewerDialog').open })`);

const out = { setup, opened, afterOpen, afterArrow, afterEsc, vid, vidOpen, vidState, vidClosed, libOpen, libState, libClosed };
console.log(JSON.stringify(out, null, 2));
ws.close();
const ok = afterOpen.open === true && afterOpen.meta.startsWith('2 / 2') && afterArrow.meta.startsWith('1 / 2')
  && afterArrow.prevDisabled === true && afterEsc.open === false  // no 1º item, "Anterior" fica desativado
  && (vid.skipped || (vidState.open === true && vidState.hasVideo === true && vidClosed.open === false))
  && afterEsc.focusedIsPreview === true && libState.open === true && libClosed.open === false;
process.exit(ok ? 0 : 1);
