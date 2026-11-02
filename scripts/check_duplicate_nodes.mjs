// Nós de saída duplicados e clique duplo (Chrome com CDP, sem custo). Uso:
//   node scripts/check_duplicate_nodes.mjs <url-do-studio> <cookie studio_session>
// A API de jobs é simulada no navegador (window.api): cada 'Editar região' vira um job falso que termina em 2 consultas.
// Conta quantos jobs foram pedidos, quantos nós existem e quantos nós de mídia nasceram do job. Código 1 se houver
// mais de 1 job por clique ou algum nó extra (cópia) depois do job concluído, inclusive após recarregar a página.
const [, , pageUrl, cookieValue] = process.argv;
if (!pageUrl || !cookieValue) {
  console.error("uso: node scripts/check_duplicate_nodes.mjs <url> <cookie>");
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

// mock da API de jobs: POST /api/image/inpaint cria um job; GET do job devolve 'running' e depois 'done'
const MOCK = `(() => {
  window.__mock = { posts: [], polls: {} };
  if (!window.__realApi) window.__realApi = window.api;
  window.api = async (path, body, method) => {
    if (path === '/api/image/inpaint') {
      const jid = 'job-mock-' + (window.__mock.posts.length + 1);
      window.__mock.posts.push(jid); window.__mock.polls[jid] = 0;
      return { id: jid, status: 'running', progress: 0, steps: [], spec: {} };
    }
    const m = /^\\/api\\/jobs\\/(.+)$/.exec(path);
    if (m && window.__mock.polls[m[1]] !== undefined) {
      const jid = m[1];
      const k = ++window.__mock.polls[jid];
      const out = '/files/1/renders/' + jid + '_0.png';
      if (k < 2) return { id: jid, status: 'running', progress: 50, steps: [{ status: 'running', label: 'Editar região', model_label: 'mock' }], detail: '' };
      return { id: jid, status: 'done', progress: 100, steps: [{ status: 'done', cost: 0.0344, label: 'Editar região', model_label: 'mock' }],
        media: { kind: 'image', url: out, thumb: out, name: jid + '_0.png' }, output: out, result: null, spec: { prompt: 'p' } };
    }
    return window.__realApi(path, body, method);
  };
  return true;
})()`;

await send("Emulation.setDeviceMetricsOverride", { width: 1400, height: 1000, deviceScaleFactor: 1, mobile: false });
await send("Page.navigate", { url: `${origin}/` });
await sleep(8000);

// canvas de teste: uma imagem de entrada e um nó Editar região com máscara pintada
const setup = await evalIn(`(async () => {
  cv.nodes.length = 0; cv.edges.length = 0;
  const c = document.createElement('canvas'); c.width = 64; c.height = 64;
  const g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(16, 16, 32, 32);
  const src = { id: uid('media'), type: 'media', x: 40, y: 120, name: 'Imagem', media: { kind: 'image', url: '/files/1/uploads/1900d1a7_flores.jpg', thumb: '/files/1/uploads/1900d1a7_flores.jpg', name: 'imagem.png' } };
  const ed = PALETTE.edit.make({ x: 420, y: 120 });
  ed.name = 'Editar região'; ed.op = 'inpaint'; ed.output = null; ed.status = 'idle'; ed.prompt = 'remova';
  ed.mask = c.toDataURL('image/png');
  cv.nodes.push(src, ed); cv.edges.push({ id: uid('e'), from: src.id, to: ed.id });
  renderAll(); save();
  window.__dupEdit = ed.id;
  return ${MOCK};
})()`);
if (setup?.exception) { console.error(setup.exception); process.exit(1); }
await sleep(300);

// clique duplo rápido no botão 'Editar região'
const dbl = await evalIn(`(() => {
  const btn = document.querySelector('.cv-node[data-id="' + window.__dupEdit + '"] [data-act=edit]');
  if (!btn) return { error: 'botão ausente' };
  btn.click(); btn.click();
  return { clicked: 2 };
})()`);
await sleep(7000);

const after = await evalIn(`(() => ({
  posts: window.__mock.posts.length,
  nodesInState: cv.nodes.length,
  mediaInState: cv.nodes.filter((n) => n.type === 'media').length,
  domNodes: document.querySelectorAll('.cv-node').length,
  domWithSameId: [...document.querySelectorAll('.cv-node')].length - new Set([...document.querySelectorAll('.cv-node')].map((e) => e.dataset.id)).size,
  editStatus: cv.nodes.find((n) => n.id === window.__dupEdit)?.status,
}))()`);

// recarrega a página depois do job concluído e conta de novo (o canvas salvo não pode ganhar cópias)
await send("Page.navigate", { url: `${origin}/` });
await sleep(8000);
const reloaded = await evalIn(`(() => ({
  nodesInState: cv.nodes.length,
  mediaInState: cv.nodes.filter((n) => n.type === 'media').length,
  domNodes: document.querySelectorAll('.cv-node').length,
  domWithSameId: [...document.querySelectorAll('.cv-node')].length - new Set([...document.querySelectorAll('.cv-node')].map((e) => e.dataset.id)).size,
}))()`);

// cópias de um mesmo job já salvas no canvas (o caso do print): ao carregar, viram um só nó
await evalIn(`(() => {
  const base = cv.nodes.find((n) => n.jobId === undefined && n.type === 'edit') || cv.nodes.find((n) => n.type === 'edit');
  if (!base) return false;
  base.jobId = 'job-dupe-1'; base.status = 'done';
  for (let i = 0; i < 2; i++) cv.nodes.push({ ...JSON.parse(JSON.stringify(base)), id: uid('edit'), x: base.x + 60 * (i + 1), y: base.y + 60 * (i + 1) });
  renderAll(); save();
  return true;
})()`);
await sleep(1200);
await send("Page.navigate", { url: `${origin}/` });
await sleep(8000);
const dupes = await evalIn(`(() => ({
  withJob: cv.nodes.filter((n) => n.jobId === 'job-dupe-1').length,
  domNodes: document.querySelectorAll('.cv-node').length,
}))()`);

const out = { dbl, after, reloaded, dupes };
console.log(JSON.stringify(out, null, 2));
ws.close();
const ok = after.posts === 1 && after.nodesInState === 2 && after.domNodes === 2 && after.domWithSameId === 0
  && reloaded.nodesInState === 2 && reloaded.domNodes === 2 && dupes.withJob === 1;
process.exit(ok ? 0 : 1);
