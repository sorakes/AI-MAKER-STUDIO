// Checagem de vazamento nos nodes do canvas (Chrome com CDP). Uso:
//   node scripts/check_node_overflow.mjs <url-do-studio> <cookie studio_session> [video|character|product]
// Pré-requisito: Chrome headless com --remote-debugging-port=9333 e o app no ar.
// Para cada tipo de node da área de vídeo cria o node, injeta textos extremos (modelo de 80 caracteres,
// arquivo de 60, erro de 200 sem espaços, frases longas em pt/es) e mede, com getBoundingClientRect,
// se algum descendente passa da borda direita do node ou se o conteúdo transborda.
// Saída: JSON com as violações (vazio = nenhum node vaza).

const [, , pageUrl, cookieValue, workspace = 'video'] = process.argv;
if (!pageUrl || !cookieValue) {
  console.error("uso: node scripts/check_node_overflow.mjs <url> <cookie>");
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
await send("Page.enable");
await send("Runtime.enable");
const origin = new URL(pageUrl).origin;
await send("Network.enable");
await send("Network.setCookie", { name: "studio_session", value: cookieValue, url: `${origin}/`, path: "/", httpOnly: true });
await send("Page.navigate", { url: pageUrl });
await new Promise((r) => setTimeout(r, 8000));
const evalIn = async (expression) => (await send("Runtime.evaluate", { returnByValue: true, awaitPromise: true, expression })).result?.value;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const types = await evalIn(`WS_PALETTE['${workspace}'].filter((k) => PALETTE[k])`);
const LONG_PT = "Ligue um vídeo na entrada e escreva o que deve acontecer em cada plano com detalhes longos para testar a quebra";
const LONG_ES = "Conecta un video a la entrada y escribe lo que debe pasar en cada plano con detalles largos para probar el salto";
const results = [];
for (const type of types) {
  const report = await evalIn(`(async () => {
    cv.nodes.length = 0; cv.edges.length = 0;
    const model = { id: 'OVF', kind: 'video', label: 'M'.repeat(40) + ' (Nano Banana 2 Lite) ' + 'x'.repeat(30), caps: { audio: true, i2v: true, t2v: true, refs: true, durations: [4, 5, 8] }, cost: 0.05, cost_unit: 'second', available: true };
    state.models.video = [model, ...(state.models.video || []).filter((m) => m.id !== 'OVF')];
    const n = PALETTE['${type}'].make({ x: 40, y: 40 });
    n.name = 'N'.repeat(70);
    for (const k of ['intent', 'prompt', 'script', 'text', 'brief', 'motion', 'bridgeIdea', 'idea']) if (k in n) n[k] = '${LONG_PT}'.replace(/ /g, (c, i) => (i % 9 === 0 ? ' ' : ' '));
    n.model = 'OVF'; n.status = 'error'; n.error = 'e'.repeat(200);
    n.output = { kind: n.type === 'audio' ? 'audio' : 'video', url: '/files/1/renders/' + 'f'.repeat(60) + '.mp4', name: 'f'.repeat(60) + '.mp4', duration: 5 };
    if (n.type === 'audio') n.tab = 'voice';
    cv.nodes.push(n);
    renderAll();
    await new Promise((r) => setTimeout(r, 700));
    const el = document.querySelector('.cv-node[data-id="' + n.id + '"]');
    if (!el) return { type: '${type}', error: 'nó não renderizou' };
    const box = el.getBoundingClientRect();
    const bad = [];
    el.querySelectorAll('*').forEach((d) => {
      if (d.closest('.cv-port') || d.classList.contains('cv-port')) return;
      const r = d.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return;
      if (r.right > box.right + 1) bad.push((d.className && d.className.baseVal === undefined ? d.className : d.tagName).toString().slice(0, 60) + ' +' + Math.round(r.right - box.right) + 'px');
    });
    el.querySelectorAll('.cv-content, .cv-head, .cv-foot').forEach((c) => {
      if (c.scrollWidth > c.clientWidth + 1) bad.push(c.className.toString().slice(0, 40) + ' scroll ' + (c.scrollWidth - c.clientWidth) + 'px');
    });
    return { type: '${type}', width: Math.round(box.width), violations: bad.slice(0, 12), count: bad.length };
  })()`);
  results.push(report);
}
// controle: um elemento de 600 px dentro de um node TEM de ser detectado (prova que a checagem funciona)
const control = await evalIn(`(() => {
  const el = document.querySelector('.cv-node'); if (!el) return { ok: false };
  const wrap = document.createElement('div'); const d = document.createElement('span'); d.style.width = '600px'; d.style.display = 'inline-block'; d.textContent = 'controle';
  wrap.appendChild(d); el.querySelector('.cv-content')?.appendChild(wrap);
  const box = el.getBoundingClientRect(); return { ok: d.getBoundingClientRect().right > box.right + 1 };
})()`);
const leaking = results.filter((r) => r.count);
console.log(JSON.stringify({ checked: results.length, leaking: leaking.length, controlDetected: control.ok, results }, null, 2));
ws.close();
process.exit(leaking.length || !control.ok ? 1 : 0);
