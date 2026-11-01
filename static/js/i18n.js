// Interface translation. The source UI text is Portuguese; English is the default language.
// Each dictionary maps the source text to the target language. Entries with {} are patterns
// (the {} parts are kept, translated too when known). The DOM is translated as it renders
// (MutationObserver), so templates, toasts and server messages all go through here.
(() => {
  const LANGS = { en: 'English', pt: 'Português', es: 'Español' };
  const DICTS = {};
  const VIA = {}; // código -> { texto em inglês: tradução }
  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* sem storage */ } },
  };
  const pick = () => {
    const saved = store.get('lang');
    if (saved && LANGS[saved]) return saved;
    const def = store.get('lang_default');
    return def && LANGS[def] ? def : 'en';
  };
  let lang = pick();
  if (lang !== 'pt') {
    const st = document.createElement('style');
    st.id = 'i18n-wait';
    st.textContent = 'body{visibility:hidden}';
    document.head.appendChild(st);
    setTimeout(() => st.remove(), 1500); // nunca deixa a página escondida
  }
  let exact = new Map();
  let patterns = [];
  let fragRe = null;
  let fragMap = new Map();
  const missing = new Set();
  const DEBUG = store.get('i18n_debug') === '1'; // lista tudo que ficou sem tradução
  const PT_HINT = /[ãõçáéíóúâêô]|\b(não|nós?|para|com|sem|uma?|dos?|das?|que|em|ao|ou|mais|já|até|clique|arraste|gerar|salvar|apagar|excluir|adicionar|novo|nova|ligue|vídeo|imagem)\b/i;
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const norm = (s) => s.replace(/\s+/g, ' ').trim();

  function build() {
    exact = new Map(); patterns = []; fragMap = new Map(); fragRe = null;
    let d = DICTS[lang];
    if (VIA[lang]) { // idioma traduzido a partir do inglês: o que faltar cai no inglês
      const en = DICTS.en || {};
      d = Object.fromEntries(Object.entries(en).map(([k, v]) => [k, VIA[lang][v] ?? v]));
      Object.assign(d, DICTS[lang] || {});
    }
    if (!d) return;
    const add = (k, v) => {
      k = norm(k);
      if (!k || v == null) return;
      if (k.includes('{}')) {
        const re = new RegExp(`^${k.split('{}').map(esc).join('(.+?)')}$`, 's');
        patterns.push({ re, v, n: k.length });
      } else {
        exact.set(k, v);
        if (k.length >= 18) fragMap.set(k, v);
      }
    };
    for (const [k, v] of Object.entries(d)) {
      add(k, v);
      // {} nas pontas costuma ser um ícone (elemento à parte): registra também sem ele
      if (k.startsWith('{} ') && v.startsWith('{} ')) add(k.slice(3), v.slice(3));
      if (k.endsWith(' {}') && v.endsWith(' {}')) add(k.slice(0, -3), v.slice(0, -3));
    }
    patterns.sort((a, b) => b.n - a.n);
    const frags = [...fragMap.keys()].sort((a, b) => b.length - a.length);
    if (frags.length) fragRe = new RegExp(frags.map(esc).join('|'), 'g');
  }

  function tr(s) {
    if (lang === 'pt' || s == null) return s;
    const raw = String(s);
    const k = norm(raw);
    if (!k || !/[A-Za-zÀ-ú]/.test(k)) return raw;
    let out = exact.get(k);
    if (out == null) {
      for (const p of patterns) {
        const m = k.match(p.re);
        if (m) {
          let i = 1;
          out = p.v.replace(/\{\}/g, () => { const g = m[i++] ?? ''; return exact.get(norm(g)) ?? g; });
          break;
        }
      }
    }
    if (out == null && fragRe) {
      const r = k.replace(fragRe, (f) => fragMap.get(f));
      if (r !== k) out = r;
    }
    if (out == null) {
      if ((DEBUG ? /[A-Za-zÀ-ú]{2}/.test(k) : PT_HINT.test(k)) && missing.size < 5000) missing.add(k);
      return raw;
    }
    const lead = raw.match(/^\s*/)[0];
    const trail = raw.match(/\s*$/)[0];
    return lead + out + trail;
  }

  // ---------------- DOM ----------------
  const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'CODE', 'PRE', 'svg', 'SVG']);
  const ATTR_OK = new Set(['TEXTAREA']); // conteúdo é do usuário, mas o placeholder/título traduz
  const ATTRS = ['title', 'placeholder', 'aria-label', 'alt', 'label', 'data-tip'];
  const done = new WeakMap(); // nó de texto -> valor que nós escrevemos

  const skipEl = (el, forAttrs = false) => {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      if (forAttrs && e === el && ATTR_OK.has(e.tagName)) continue;
      if (SKIP.has(e.tagName) || e.isContentEditable || e.hasAttribute('data-noi18n')) return true;
    }
    return false;
  };

  function textNode(n) {
    if (done.get(n) === n.data) return;
    const t = tr(n.data);
    if (t !== n.data) n.data = t;
    done.set(n, n.data);
  }

  function attrs(el) {
    for (const a of ATTRS) {
      const v = el.getAttribute?.(a);
      if (v && el.__i18nA?.[a] !== v) {
        const t = tr(v);
        if (t !== v) el.setAttribute(a, t);
        (el.__i18nA ||= {})[a] = t;
      }
    }
    if (el.tagName === 'INPUT' && /^(button|submit)$/i.test(el.type) && el.value) {
      const t = tr(el.value);
      if (t !== el.value) el.value = t;
    }
  }

  // blocos com formatação (<b>, <a>…) traduzidos inteiros: <li data-i18n-html>
  function htmlBlocks(root) {
    const els = root.matches?.('[data-i18n-html]') ? [root] : [...(root.querySelectorAll?.('[data-i18n-html]') || [])];
    for (const el of els) {
      if (el.hasAttribute('data-noi18n')) continue;
      const k = norm(el.innerHTML);
      const v = VIA[lang] ? (VIA[lang][DICTS.en?.[k]] ?? DICTS.en?.[k]) : DICTS[lang]?.[k];
      if (v) el.innerHTML = v;
      el.setAttribute('data-noi18n', '');
    }
  }

  function walk(root) {
    if (lang === 'pt' || !root) return;
    if (root.nodeType === 1 || root.nodeType === 9) htmlBlocks(root);
    if (root.nodeType === 3) { if (!skipEl(root.parentElement)) textNode(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
    if (root.nodeType === 1 && ATTR_OK.has(root.tagName)) { attrs(root); return; }
    if (root.nodeType === 1 && skipEl(root)) return;
    if (root.nodeType === 1) attrs(root);
    const w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => {
        if (n.nodeType !== 1) return NodeFilter.FILTER_ACCEPT;
        if (ATTR_OK.has(n.tagName) && !n.hasAttribute('data-noi18n')) { attrs(n); return NodeFilter.FILTER_REJECT; }
        return SKIP.has(n.tagName) || n.isContentEditable || n.hasAttribute('data-noi18n') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      },
    });
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (n.nodeType === 3) textNode(n); else attrs(n);
    }
  }

  let obs = null;
  function start() {
    if (lang === 'pt' || obs) return;
    walk(document.documentElement);
    if (document.title) document.title = tr(document.title);
    document.getElementById('i18n-wait')?.remove();
    obs = new MutationObserver((list) => {
      for (const m of list) {
        if (m.type === 'childList') m.addedNodes.forEach(walk);
        else if (m.type === 'characterData') { if (!skipEl(m.target.parentElement)) textNode(m.target); }
        else if (m.type === 'attributes' && !skipEl(m.target, true)) attrs(m.target);
      }
    });
    obs.observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }

  // diálogos nativos também
  for (const fn of ['alert', 'confirm', 'prompt']) {
    const orig = window[fn].bind(window);
    window[fn] = (msg, ...rest) => orig(tr(msg), ...rest);
  }

  window.I18N = {
    LANGS,
    get lang() { return lang; },
    add(code, dict) { DICTS[code] = { ...(DICTS[code] || {}), ...dict }; if (code === lang || (code === 'en' && VIA[lang])) build(); },
    fromEnglish(code, dict) { VIA[code] = { ...(VIA[code] || {}), ...dict }; if (code === lang) build(); },
    t: tr,
    setLang(code) { if (!LANGS[code]) return; store.set('lang', code); location.reload(); },
    setDefault(code) { // padrão do admin (vale para quem nunca escolheu)
      if (!LANGS[code]) return;
      const before = pick();
      store.set('lang_default', code);
      if (!store.get('lang') && before !== code) location.reload();
    },
    missing: () => [...missing].sort(),
    walk,
  };
  window.t = tr;
  window.LOC = lang === 'pt' ? 'pt-BR' : lang === 'es' ? 'es-ES' : 'en-US';
  document.documentElement.lang = lang === 'pt' ? 'pt-BR' : lang;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
