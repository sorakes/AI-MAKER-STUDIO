// Auditoria visual (Chrome com CDP): nada pode ultrapassar a borda de seu cartão/cabeçalho/diálogo.
// Uso: node scripts/check_visual_layout.mjs <url-do-studio> <cookie studio_session>
// Cobre, nos 3 idiomas (en, pt, es):
//   - TODOS os itens da paleta lateral de cada área (vídeo, personagem, produto), com título, descrição e
//     etiqueta injetados longos;
//   - o cabeçalho de cada tipo de node (nome longo, selo BETA);
//   - os diálogos: Storyboard, máscara e direção.
// Medição por getBoundingClientRect/scrollWidth. Título de palette/cabeçalho tem de ficar em 1 linha.
// Saída: JSON. Código 1 se houver vazamento. Pré-requisito: Chrome com --remote-debugging-port=9333.

const [, , pageUrl, cookieValue] = process.argv;
if (!pageUrl || !cookieValue) {
  console.error("uso: node scripts/check_visual_layout.mjs <url> <cookie>");
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
  if (r.exceptionDetails) return 'EXC: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text || '').slice(0, 300);
  return r.result?.value;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// mede os descendentes de um contêiner: nenhum pode passar da borda direita/inferior dele (+1px de tolerância)
const MEASURE = `
window.__measure = (box, root) => {
  const bad = [];
  const br = box.getBoundingClientRect();
  root.querySelectorAll('*').forEach((d) => {
    if (d.closest('.cv-port') || d.classList.contains('cv-port')) return;
    const r = d.getBoundingClientRect();
    if (!r.width && !r.height) return;
    if (r.right > br.right + 1) bad.push('dir ' + (d.className?.baseVal ?? d.className ?? d.tagName).toString().slice(0, 50) + ' +' + Math.round(r.right - br.right) + 'px');
  });
  return bad;
};
window.__titleLines = (el) => {
  if (!el) return 0;
  const lh = parseFloat(getComputedStyle(el).lineHeight) || parseFloat(getComputedStyle(el).fontSize) * 1.2;
  return Math.round(el.getBoundingClientRect().height / lh);
};`;
await evalIn(MEASURE);

const langs = ["en", "pt", "es"];
const LONG = "Descrição muito longa para testar a quebra de linha e o corte com reticências no cartão do item";
const report = [];
const byLang = {};
for (const lang of langs) {
  await evalIn(`localStorage.setItem('lang', '${lang}'); true`);
  await send("Page.navigate", { url: `${origin}/` });
  await sleep(8000);
  await evalIn(MEASURE);  // o reload apaga os auxiliares: injeta de novo
  const htmlLang = await evalIn(`document.documentElement.lang`);
  byLang[lang] = { htmlLang, start: report.length };
  for (const wsName of ["video", "character", "product"]) {
    const res = await evalIn(`(async () => {
      cv.ws = '${wsName}'; renderPalette();
      await new Promise((r) => setTimeout(r, 300));
      const items = [...document.querySelectorAll('#cvPalette .cv-pal[data-node]')];
      const out = [];
      for (const it of items) {
        const k = it.dataset.node;
        const p = PALETTE[k];
        const saved = { title: p.title, tag: p.tag, what: p.what, how: p.how };
        p.title = 'Título extremamente longo para testar o corte de linha ' + k;
        p.tag = 'etiqueta-muito-longa-sem-espacos-' + k + '-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx';
        p.what = '${LONG}';
        p.how = 'Como usar com um texto sem espaços: ' + 'x'.repeat(120);
        renderPalette();
        await new Promise((r) => setTimeout(r, 60));
        const el = document.querySelector('#cvPalette .cv-pal[data-node="' + k + '"]');
        if (el) {
          const box = el;
          const bad = __measure(box, box);
          const title = el.querySelector('.cv-pal-head b');
          const lines = __titleLines(title);
          if (lines > 1) bad.push('título em ' + lines + ' linhas');
          if (el.scrollWidth > el.clientWidth + 1) bad.push('scroll do cartão ' + (el.scrollWidth - el.clientWidth) + 'px');
          out.push({ kind: 'palette', key: k, lang: '${lang}', ws: '${wsName}', bad });
        }
        Object.assign(p, saved);
      }
      renderPalette();
      return out;
    })()`);
    if (!Array.isArray(res)) { report.push({ kind: 'error', key: wsName, lang, bad: ['falha ao medir: ' + JSON.stringify(res).slice(0, 200)] }); continue; }
    report.push(...res);
  }
  // cabeçalhos de cada tipo de node, com nome longo e selo
  const heads = await evalIn(`(async () => {
    const out = [];
    for (const k of Object.keys(PALETTE)) {
      cv.nodes.length = 0; cv.edges.length = 0;
      const n = PALETTE[k].make({ x: 40, y: 40 });
      n.name = 'Nome de nó extremamente longo sem espaços ' + 'n'.repeat(70);
      cv.nodes.push(n); renderAll();
      await new Promise((r) => setTimeout(r, 120));
      const el = document.querySelector('.cv-node[data-id="' + n.id + '"]');
      if (!el) continue;
      const head = el.querySelector('.cv-head');
      const bad = __measure(el, el);
      const title = el.querySelector('.cv-head .title');
      if (__titleLines(title) > 1) bad.push('título do nó em mais de 1 linha');
      if (head && head.scrollWidth > head.clientWidth + 1) bad.push('cabeçalho com scroll ' + (head.scrollWidth - head.clientWidth) + 'px');
      out.push({ kind: 'node-head', key: k, lang: '${lang}', bad });
    }
    return out;
  })()`);
  if (!Array.isArray(heads)) report.push({ kind: 'error', key: 'heads', lang, bad: ['falha: ' + JSON.stringify(heads).slice(0, 200)] }); else report.push(...heads);
  // diálogos: storyboard e máscara e direção (abre cada um e mede o painel)
  const dialogs = await evalIn(`(async () => {
    const out = [];
    document.getElementById('cvStoryboard').click();
    await new Promise((r) => setTimeout(r, 500));
    const sb = document.querySelector('dialog.sb-dialog');
    if (sb) out.push({ kind: 'dialog', key: 'storyboard', lang: '${lang}', bad: __measure(sb, sb) });
    sb?.close();
    return out;
  })()`);
  if (!Array.isArray(dialogs)) report.push({ kind: 'error', key: 'dialogs', lang, bad: ['falha: ' + JSON.stringify(dialogs).slice(0, 200)] }); else report.push(...dialogs);
}
const leaks = report.filter((r) => r.bad && r.bad.length);
for (const lang of langs) {
  const from = byLang[lang].start;
  const to = langs[langs.indexOf(lang) + 1] ? byLang[langs[langs.indexOf(lang) + 1]].start : report.length;
  byLang[lang].checked = report.slice(from, to).length;
  byLang[lang].leaking = report.slice(from, to).filter((r) => r.bad?.length).length;
}
console.log(JSON.stringify({ checked: report.length, leaking: leaks.length, byLang, leaks: leaks.slice(0, 60) }, null, 2));
ws.close();
process.exit(leaks.length ? 1 : 0);
