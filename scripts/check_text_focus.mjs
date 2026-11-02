// Checagem de foco nos campos de texto dos nodes (Chrome com CDP). Uso:
//   node scripts/check_text_focus.mjs <url-do-studio> <cookie studio_session> [video|character|product]
// Para cada node com textarea cria o node, foca o campo e digita 20 caracteres em sequência (evento 'input').
// Passa se o MESMO elemento continua conectado ao DOM e com o foco depois de cada tecla.
// Saída: JSON; código de saída 1 se algum campo perdeu o foco.

const [, , pageUrl, cookieValue, workspace = "video"] = process.argv;
if (!pageUrl || !cookieValue) {
  console.error("uso: node scripts/check_text_focus.mjs <url> <cookie> [workspace]");
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
await send("Network.enable");
const origin = new URL(pageUrl).origin;
await send("Network.setCookie", { name: "studio_session", value: cookieValue, url: `${origin}/`, path: "/", httpOnly: true });
await send("Page.navigate", { url: pageUrl });
await new Promise((r) => setTimeout(r, 8000));
const evalIn = async (expression) => (await send("Runtime.evaluate", { returnByValue: true, awaitPromise: true, expression })).result?.value;

const types = await evalIn(`WS_PALETTE['${workspace}'].filter((k) => PALETTE[k])`);
const results = [];
for (const type of types) {
  const r = await evalIn(`(async () => {
    cv.nodes.length = 0; cv.edges.length = 0;
    const n = PALETTE['${type}'].make({ x: 40, y: 40 });
    cv.nodes.push(n); renderAll();
    await new Promise((res) => setTimeout(res, 600));
    const el = document.querySelector('.cv-node[data-id="' + n.id + '"]');
    const areas = el ? [...el.querySelectorAll('textarea')] : [];
    const out = [];
    for (const [i, ta] of areas.entries()) {
      ta.focus();
      let ok = document.activeElement === ta;
      for (let k = 0; k < 20 && ok; k++) {
        ta.value += 'a';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        await new Promise((res) => setTimeout(res, 30));
        ok = ta.isConnected && document.activeElement === ta;
      }
      out.push({ index: i, kept: ok, typed: ta.value.length });
    }
    return { type: '${type}', textareas: areas.length, results: out };
  })()`);
  results.push(r);
}
const lost = results.flatMap((r) => (r.results || []).filter((x) => !x.kept).map((x) => ({ type: r.type, index: x.index })));
console.log(JSON.stringify({ checked: results.length, lost, results }, null, 2));
ws.close();
process.exit(lost.length ? 1 : 0);
