// Áreas do estúdio: Video Studio, Personagens e Produtos. Cada uma é um canvas de nós próprio.
// Personagens/produtos continuam sendo itens da biblioteca (assets): o nó Personagem/Produto é a ficha
// deles, e o nó Elenco do Video Studio puxa as posições geradas.

const AS_FIELDS = {
  character: [['idade', 'Idade aparente', 'ex.: 30 anos'], ['aparencia', 'Aparência', 'ex.: pele morena, olhos castanhos'],
    ['cabelo', 'Cabelo', 'ex.: cacheado curto, castanho'], ['roupa', 'Roupa', 'ex.: camisa branca da marca, jeans'],
    ['acessorios', 'Acessórios', 'ex.: relógio prata, boné'], ['estilo', 'Estilo / personalidade', 'ex.: alegre, esportivo']],
  product: [['categoria', 'Categoria', 'ex.: bebida, cosmético, tênis'], ['material', 'Material', 'ex.: vidro verde, alumínio'],
    ['cores', 'Cores', 'ex.: verde, prata, vermelho'], ['rotulo', 'Rótulo / logo', 'ex.: rótulo prata com estrela vermelha'],
    ['tamanho', 'Tamanho / formato', 'ex.: garrafa long neck 330ml']],
};
const WS_OF_VIEW = { canvas: 'video', character: 'character', product: 'product', inventory: null };
const VIEW_OF_WS = { video: 'canvas', character: 'character', product: 'product' };
// Permissões do grupo: só aparecem as abas liberadas para a conta.
const wsAllowed = (ws) => state.me?.permissions?.[`ws_${ws}`] !== false;
const firstAllowedWs = () => ['video', 'character', 'product'].find(wsAllowed) || 'video';

function applyPermissions() {
  document.querySelectorAll('#studioTabs .tab').forEach((t) => { t.hidden = t.dataset.view !== 'inventory' && !wsAllowed(WS_OF_VIEW[t.dataset.view]); });
  const p = state.me?.permissions || {};
  document.body.classList.toggle('no-upload', p.upload === false);
  document.body.classList.toggle('no-delete', p.delete_files === false);
  if (!['video', 'character', 'product'].some(wsAllowed)) {
    $('#cvEmpty').innerHTML = '<b>Sem acesso ao estúdio</b><p>Sua conta ainda não tem nenhuma área liberada. Fale com o administrador.</p>';
  }
}

function initStudioTabs() {
  applyPermissions();
  document.querySelectorAll('#studioTabs .tab').forEach((t) => t.addEventListener('click', () => showStudioView(t.dataset.view)));
  window.addEventListener('hashchange', () => showStudioView(location.hash.slice(1) || 'canvas', true));
  const start = location.hash.slice(1);
  if (start === 'inventory') showStudioView(start, true);
  else if (start && WS_OF_VIEW[start] && start !== 'canvas' && wsAllowed(WS_OF_VIEW[start])) showStudioView(start, true);
  else markTabs(VIEW_OF_WS[cv.ws] || 'canvas');
}

function markTabs(view) {
  document.querySelectorAll('#studioTabs .tab').forEach((t) => t.classList.toggle('active', t.dataset.view === view));
  const inv = view === 'inventory';
  if (!inv) document.body.dataset.ws = WS_OF_VIEW[view];
  document.body.classList.toggle('on-inventory', inv);
  document.body.classList.toggle('on-canvas', !inv);
  $('#view-inventory')?.classList.toggle('active', inv);
  $('#view-canvas')?.classList.toggle('active', !inv);
}

async function showStudioView(view, fromHash = false) {
  if (view === 'inventory') {
    markTabs(view);
    if (!fromHash) history.replaceState(null, '', '#inventory');
    return loadInventory().catch((e) => toast(e.message));
  }
  if (!WS_OF_VIEW[view]) view = 'canvas';
  if (!wsAllowed(WS_OF_VIEW[view])) view = VIEW_OF_WS[firstAllowedWs()];
  markTabs(view);
  if (!fromHash) history.replaceState(null, '', view === 'canvas' ? location.pathname : `#${view}`);
  await loadAssets().catch(() => {});
  await switchWorkspace(WS_OF_VIEW[view]);
}

async function loadAssets() {
  const r = await api('/api/assets');
  state.assets = r.items;
  return r.items;
}

// Abre a aba do item e centraliza o nó dele.
async function focusAsset(assetId) {
  const a = (state.assets || []).find((x) => x.id === assetId);
  if (!a) return;
  await showStudioView(a.kind);
  let n = cv.nodes.find((x) => GEN_KIND[x.type] && x.assetId === assetId);
  if (!n) {
    for (const t of cv.tabs[a.kind] || []) {
      const d = await loadWorkspaceDoc(t.key);
      if (d.nodes.some((x) => GEN_KIND[x.type] && x.assetId === assetId)) { await switchWorkspace(a.kind, t.key); break; }
    }
    n = cv.nodes.find((x) => GEN_KIND[x.type] && x.assetId === assetId);
  }
  if (!n) return;
  const r = $('#cvBoard').getBoundingClientRect();
  cv.pan = { x: r.width / 2 - (n.x + 180) * cv.zoom, y: 80 - n.y * cv.zoom };
  applyView();
  select(n.id);
}
