// Estilos inline sem atributo style (CSP com style-src sem 'unsafe-inline').
// O markup traz data-st="<declarações css>" e o CSSOM aplica (el.style.cssText), que a CSP permite.
// Vale para o HTML da página e para qualquer nó inserido depois (innerHTML dos renderizadores).
(function () {
  const apply = (el) => {
    const css = el.getAttribute('data-st');
    if (css != null) el.style.cssText = css;
  };
  const walk = (node) => {
    if (!node || node.nodeType !== 1) return;
    if (node.hasAttribute('data-st')) apply(node);
    node.querySelectorAll('[data-st]').forEach(apply);
  };
  walk(document.documentElement);
  new MutationObserver((muts) => {
    for (const m of muts) {
      if (m.type === 'attributes') apply(m.target);
      else m.addedNodes.forEach(walk);
    }
  }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-st'] });
})();
