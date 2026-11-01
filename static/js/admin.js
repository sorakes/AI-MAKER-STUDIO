// Painel admin: usuários, limites, keys cifradas, modelos, logs, arquivos.

const A = { users: [], models: [], providers: [], logOffset: 0, files: [], sel: new Set() };
const KIND_LABEL = { text: 'Texto (prompts)', vision: 'Visão (ler imagens)', image: 'Imagem', video: 'Vídeo' };
const LIMIT_FIELDS = [
  ['tokens_month', 'Tokens por mês', 'number'],
  ['storage_mb', 'Espaço em disco (MB)', 'number'],
];

document.addEventListener('DOMContentLoaded', async () => {
  await loadMe();
  document.querySelectorAll('#tabs .tab').forEach((t) => t.addEventListener('click', () => show(t.dataset.view)));
  $('#btnNewUser').onclick = () => userDialog(null);
  $('#btnAddModel').onclick = addModelDialog;
  ['logUser', 'logKind', 'logStatus'].forEach((id) => { $(`#${id}`).onchange = () => { A.logOffset = 0; loadLogs(); }; });
  $('#logPrev').onclick = () => { A.logOffset = Math.max(0, A.logOffset - 100); loadLogs(); };
  $('#logNext').onclick = () => { A.logOffset += 100; loadLogs(); };
  $('#logCsv').onclick = exportCsv;
  $('#fileUser').onchange = loadFiles;
  $('#fileKind').onchange = renderFiles;
  $('#fileDelSel').onclick = () => deleteFiles([...A.sel]);
  $('#btnSaveSettings').onclick = saveSettings;
  show(location.hash.slice(1) || 'overview');
  window.addEventListener('hashchange', () => show(location.hash.slice(1)));
});

function show(view) {
  if (!$(`#v-${view}`)) view = 'overview';
  document.querySelectorAll('#tabs .tab').forEach((t) => t.classList.toggle('active', t.dataset.view === view));
  document.querySelectorAll('.aview').forEach((v) => v.classList.toggle('active', v.id === `v-${view}`));
  if (location.hash.slice(1) !== view) history.replaceState(null, '', `#${view}`);
  ({ overview: loadOverview, users: loadUsers, groups: loadGroups, sso: loadSso, models: loadModels, branding: loadBranding, prompts: loadPrompts, logs: async () => { await ensureUsers(); loadLogs(); },
    files: async () => { await ensureUsers(); loadFiles(); }, settings: loadSettings })[view]().catch((e) => toast(e.message));
}

async function ensureUsers() {
  if (!A.users.length) A.users = await api('/api/admin/users');
  const opts = '<option value="">Todos os usuários</option>' + A.users.map((u) => `<option value="${u.id}">${esc(u.email)}</option>`).join('');
  ['logUser', 'fileUser'].forEach((id) => { const s = $(`#${id}`); const v = s.value; s.innerHTML = opts; s.value = v; });
}

// ---------------- Painel ----------------

async function loadOverview() {
  const o = await api('/api/admin/overview');
  const k = Object.fromEntries(o.by_kind.map((r) => [r.kind, r]));
  const spend = o.by_kind.reduce((t, r) => t + r.cost, 0);
  $('#kpis').innerHTML = [
    ['Usuários', o.users, `${o.active_users} ativos no mês`],
    ['Imagens no mês', k.image?.n || 0, fmtUsd(k.image?.cost || 0)],
    ['Vídeos no mês', k.video?.n || 0, fmtUsd(k.video?.cost || 0)],
    ['Gasto estimado', fmtUsd(spend), `texto ${fmtUsd((k.text?.cost || 0) + (k.vision?.cost || 0))}`],
    ['Erros no mês', o.errors, 'ver em Logs', o.errors ? 'warn' : ''],
    ['Armazenamento', fmtBytes(o.storage_bytes), `${o.files} arquivos`],
    ['Renders rodando', o.running_jobs, ''],
  ].map(([t, v, s, c]) => `<div class="kpi ${c || ''}"><small>${t}</small><b>${v}</b><span>${s}</span></div>`).join('');
  const days = Array.from({ length: new Date().getDate() }, (_, i) => o.days.find((d) => d.d === i) || { d: i, cost: 0, n: 0 });
  const max = Math.max(0.0001, ...days.map((d) => d.cost));
  $('#chartDays').innerHTML = spend ? days.map((d) => `<div style="height:${(d.cost / max) * 100}%" data-tip="dia ${d.d + 1}: ${fmtUsd(d.cost)} · ${d.n} gerações"></div>`).join('')
    : '<div class="empty">Sem gastos neste mês ainda.</div>';
  $('#topUsers').innerHTML = '<tr><th>Usuário</th><th class="num">Gerações</th><th class="num">Gasto</th></tr>'
    + o.top_users.map((u) => `<tr><td>${esc(u.name || u.email)}<br><small class="mono">${esc(u.email)}</small></td><td class="num">${u.n}</td><td class="num">${fmtUsd(u.cost)}</td></tr>`).join('');
  $('#provSummary').innerHTML = o.providers.map((p) => `<span class="tag ${p.configured && p.enabled ? 'ok' : 'err'}">${esc(p.label)}: ${p.configured ? (p.enabled ? `key ${esc(p.hint)}` : 'desativado') : 'sem key'}</span>`).join('')
    + ' <a class="btn small ghost" href="#models" data-show="models">Configurar</a>';
}

// clique em links gerados com data-show (CSP não permite onclick inline)
document.addEventListener('click', (e) => {
  const a = e.target.closest?.('[data-show]');
  if (a) show(a.dataset.show);
});

// ---------------- Usuários ----------------

const meter = (used, max, fmt = (v) => v) => (max == null ? `<small>${fmt(used)} · ∞</small>`
  : `<small>${fmt(used)} / ${fmt(max)}</small><div class="meter"><i class="${used / Math.max(1e-9, max) > 0.85 ? 'hot' : ''}" style="width:${max ? Math.min(100, (used / max) * 100) : 100}%"></i></div>`);

async function loadUsers() {
  A.users = await api('/api/admin/users');
  if (!G.data) await ensureGroups();
  if (!A.models.length) A.models = await api('/api/admin/models');
  $('#usersTbl').innerHTML = `<tr><th>Conta</th><th>Papel e grupos</th><th>Tokens do mês</th><th>Espaço</th><th>Último login</th><th></th></tr>`
    + A.users.map((u) => {
      const l = u.limits;
      return `<tr>
        <td><b>${esc(u.name || '—')}</b><br><small class="mono">${esc(u.email)}</small></td>
        <td><span class="tag ${u.is_admin ? 'admin' : ''}">${u.is_admin ? 'admin' : 'usuário'}</span>${u.auth_provider === 'microsoft' ? ` <span class="tag ms" title="Entra com Microsoft">${ic('cloud')} Microsoft</span>` : ''} ${u.active ? '' : '<span class="tag off">desativada</span>'}${l.models ? ` <span class="tag">${l.models.length} modelos</span>` : ''}
          ${u.groups.length ? `<div class="gtags">${u.groups.map(groupTag).join('')}</div>` : ''}</td>
        <td title="${fmtUsd(u.usage.spend)} no mês">${meter(Math.round(u.usage.tokens), l.tokens_month, (v) => Number(v).toLocaleString(LOC))}<div><small class="muted">${fmtUsd(u.usage.spend)}</small></div></td>
        <td>${meter(u.storage_bytes / 2 ** 20, l.storage_mb, (v) => (v === l.storage_mb ? `${v} MB` : fmtBytes(v * 2 ** 20)))}</td>
        <td><small>${fmtDate(u.last_login)}</small></td>
        <td><div class="row-actions">
          <button class="btn small ghost" data-edit="${u.id}">Editar</button>
          <button class="btn small ghost" data-files="${u.id}" title="Arquivos" aria-label="Arquivos">${ic('folder')}</button>
          <button class="btn small ghost" data-logs="${u.id}" title="Logs" aria-label="Logs">${ic('list')}</button>
          ${u.id !== state.me.id ? `<button class="btn small ghost" data-del="${u.id}" title="Excluir conta e arquivos" aria-label="Excluir conta e arquivos">${ic('trash')}</button>` : ''}
        </div></td></tr>`;
    }).join('');
  document.querySelectorAll('[data-edit]').forEach((b) => { b.onclick = () => userDialog(A.users.find((u) => u.id === +b.dataset.edit)); });
  document.querySelectorAll('[data-del]').forEach((b) => { b.onclick = () => deleteUser(A.users.find((u) => u.id === +b.dataset.del)); });
  document.querySelectorAll('[data-files]').forEach((b) => { b.onclick = () => { show('files'); setTimeout(() => { $('#fileUser').value = b.dataset.files; loadFiles(); }, 50); }; });
  document.querySelectorAll('[data-logs]').forEach((b) => { b.onclick = () => { show('logs'); setTimeout(() => { $('#logUser').value = b.dataset.logs; A.logOffset = 0; loadLogs(); }, 50); }; });
}

function limitsInputs(lim, defaults) {
  // Vazio = herda o padrão (só na conta) · ∞ = sem limite · número = limite.
  return LIMIT_FIELDS.map(([k, label]) => `<label>${label}<input inputmode="decimal" name="lim_${k}" value="${k in lim ? (lim[k] == null ? '∞' : lim[k]) : ''}" placeholder="${defaults ? `padrão: ${defaults[k] ?? '∞'}` : '∞ = sem limite'}"></label>`).join('');
}

function modelChecks(selected) {
  const all = selected == null;
  return `<label class="inline"><input type="checkbox" name="all_models" ${all ? 'checked' : ''}> Todos os modelos habilitados (inclusive os que forem adicionados depois)</label>
    <div class="checks" id="modelChecks" ${all ? 'hidden' : ''}>${A.models.filter((m) => ['image', 'video'].includes(m.kind)).map((m) =>
      `<label><input type="checkbox" name="model" value="${m.id}" ${all || selected.includes(m.id) ? 'checked' : ''}> ${esc(m.label)}</label>`).join('')}</div>`;
}

function readLimits(f) {
  const lim = {};
  LIMIT_FIELDS.forEach(([k]) => {
    const v = f[`lim_${k}`].value.trim().replace(',', '.');
    if (v === '∞' || /^sem/i.test(v)) lim[k] = null;
    else if (v !== '') lim[k] = Math.max(0, +v || 0);
  });
  lim.models = f.all_models?.checked ? null : [...f.querySelectorAll('[name=model]:checked')].map((c) => +c.value);
  return lim;
}

function openDialog(html, onSubmit) {
  const dlg = $('#dlg');
  const f = $('#dlgForm');
  f.innerHTML = html;
  f.onsubmit = async (e) => {
    e.preventDefault();
    if (e.submitter?.value === 'cancel') return dlg.close();
    const btn = e.submitter;
    if (btn) btn.disabled = true;
    try {
      if (await onSubmit(f) !== false) dlg.close();
    } catch (err) { toast(err.message); } finally { if (btn) btn.disabled = false; }
  };
  f.querySelector('[name=all_models]')?.addEventListener('change', (e) => { f.querySelector('#modelChecks').hidden = e.target.checked; });
  f.querySelectorAll('button[value=cancel]').forEach((b) => { b.type = 'button'; b.onclick = () => dlg.close(); });
  dlg.showModal();
  return f;
}

async function userDialog(u) {
  const [settings] = await Promise.all([api('/api/admin/settings'), ensureGroups()]);
  const manualIds = new Set((u?.groups || []).filter((g) => g.source === 'manual').map((g) => g.id));
  const msGroups = (u?.groups || []).filter((g) => g.source === 'microsoft');
  const custom = u ? u.custom_limits : {};
  openDialog(`<h3>${u ? 'Editar conta' : 'Nova conta'}</h3>
    <label>Nome<input name="name" value="${esc(u?.name || '')}"></label>
    <label>E-mail (login)<input name="email" type="email" required value="${esc(u?.email || '')}"></label>
    <label>${u ? 'Nova senha (vazio = manter)' : 'Senha inicial (8+ caracteres)'}<input name="password" type="text" ${u ? '' : 'required minlength="8"'} placeholder="${u ? '••••••••' : ''}" value="${u ? '' : genPassword()}"></label>
    <div class="limits-grid">
      <label>Papel<select name="role"><option value="user">Usuário</option><option value="admin" ${u?.role === 'admin' ? 'selected' : ''}>Administrador</option></select></label>
      <label>Status<select name="active"><option value="1">Ativa</option><option value="0" ${u && !u.active ? 'selected' : ''}>Desativada</option></select></label>
    </div>
    <div class="section-label"><span>Grupos</span><span class="hint">permissões e limites vêm dos grupos</span></div>
    ${G.data.groups.length ? `<div class="checks">${G.data.groups.map((g) => `<label><input type="checkbox" name="ugroup" value="${g.id}" ${manualIds.has(g.id) ? 'checked' : ''}> <span class="gdot" style="--g:${esc(g.color)}"></span> ${esc(g.name)}</label>`).join('')}</div>` : '<p class="note">Nenhum grupo criado (aba Grupos).</p>'}
    ${msGroups.length ? `<p class="note">${ic('cloud')} Pela Microsoft: ${msGroups.map(groupTag).join(' ')} (atualiza a cada login)</p>` : ''}
    <div class="section-label"><span>Limites próprios da conta</span><span class="hint">vazio = do grupo/padrão · ∞ = sem limite</span></div>
    <div class="limits-grid">${limitsInputs(custom, settings.default_limits)}</div>
    <div class="section-label"><span>Modelos liberados</span></div>
    ${modelChecks(custom.models === undefined ? settings.default_limits.models : custom.models)}
    <div class="dlg-actions"><button class="btn ghost" value="cancel">Cancelar</button><button class="btn primary" value="ok">${u ? 'Salvar' : 'Criar conta'}</button></div>`,
  async (f) => {
    const lim = readLimits(f);
    const body = { name: f.name.value, email: f.email.value, role: f.role.value, limits: lim,
      group_ids: [...f.querySelectorAll('[name=ugroup]:checked')].map((c) => +c.value) };
    if (u) {
      await api(`/api/admin/users/${u.id}`, { ...body, active: f.active.value === '1', password: f.password.value || null }, 'PATCH');
      toast('Conta atualizada');
    } else {
      await api('/api/admin/users', { ...body, password: f.password.value });
      toast(`Conta criada: ${body.email} · senha: ${f.password.value}`);
    }
    loadUsers();
  });
}

function genPassword() {
  const c = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  return Array.from(crypto.getRandomValues(new Uint32Array(12)), (x) => c[x % c.length]).join('');
}

async function deleteUser(u) {
  if (!confirm(`Excluir a conta ${u.email}?\nTodos os arquivos (${fmtBytes(u.storage_bytes)}), o canvas e os renders dela serão apagados. Os logs ficam.`)) return;
  const r = await api(`/api/admin/users/${u.id}`, undefined, 'DELETE');
  toast(`Conta excluída · ${fmtBytes(r.freed)} liberados`);
  loadUsers();
}

// ---------------- Keys & modelos ----------------

async function loadModels() {
  [A.providers, A.models] = await Promise.all([api('/api/admin/providers'), api('/api/admin/models')]);
  $('#providers').innerHTML = A.providers.map((p) => `
    <div class="prov" data-p="${p.id}">
      <h4>${esc(p.label)} <span class="tag ${p.configured ? (p.enabled ? 'ok' : 'off') : 'err'}">${p.configured ? (p.enabled ? `${p.keys.length} key(s)` : 'desativado') : 'sem key'}</span></h4>
      <div class="kinds">Usado para: ${p.kinds.map((k) => t(KIND_LABEL[k])).join(', ')} · <a href="${p.site}" target="_blank" rel="noopener">obter key ${ic('external')}</a></div>
      <div class="keys">${p.keys.length ? p.keys.map((k) => `
        <div class="key-row ${k.enabled ? '' : 'off'}" data-k="${k.id}">
          <button class="star ${k.is_default ? 'on' : ''}" data-act="kdefault" title="${k.is_default ? 'Key padrão do provedor' : 'Tornar padrão'}" aria-label="${k.is_default ? 'Key padrão do provedor' : 'Tornar padrão'}">${ic('star')}</button>
          <div class="key-main"><b>${esc(k.name)}</b><small class="mono">${esc(k.hint || '')}${k.models ? ` · usada por ${k.models} modelo(s)` : ''}${k.readable ? '' : ' · <span class="err">ilegível (SECRET_KEY mudou)</span>'}</small></div>
          <div class="row-actions">
            <button class="btn small ghost" data-act="ktest">Testar</button>
            <button class="btn small ghost" data-act="krename" title="Renomear" aria-label="Renomear">${ic('pencil')}</button>
            <button class="btn small ghost" data-act="ktoggle">${k.enabled ? 'Desativar' : 'Ativar'}</button>
            <button class="btn small ghost" data-act="kdel" title="Remover" aria-label="Remover">${ic('trash')}</button>
          </div>
          <div class="key-result" data-role="kres"></div>
        </div>`).join('') : `<p class="note">${p.from_env ? 'Usando a key da variável de ambiente.' : 'Nenhuma key cadastrada.'}</p>`}</div>
      <div class="key-add">
        <input data-role="kname" placeholder="Nome (ex.: Cliente A, Produção, Testes)">
        <input type="password" data-role="key" placeholder="colar a key" autocomplete="off">
        <button class="btn small primary" data-act="kadd">${ic('plus')} Adicionar</button>
      </div>
      <div class="keyrow">
        <button class="btn small ghost" data-act="toggle">${p.enabled ? 'Desativar provedor' : 'Ativar provedor'}</button>
      </div>
      <div class="result" data-role="result"></div>
    </div>`).join('');
  document.querySelectorAll('.prov').forEach((el) => {
    const pid = el.dataset.p;
    const prov = A.providers.find((p) => p.id === pid);
    const out = (box, r) => { box.className = `${box.className.split(' ')[0]} ${r.ok ? 'ok' : 'err'}`; box.textContent = r.message; };
    el.querySelector('[data-act=kadd]').onclick = async () => {
      const name = el.querySelector('[data-role=kname]').value.trim();
      const key = el.querySelector('[data-role=key]').value.trim();
      if (!key) return toast('Cole a key');
      const r = await api(`/api/admin/providers/${pid}/keys`, { name: name || `Key ${prov.keys.length + 1}`, key });
      await loadModels();
      const box = document.querySelector(`.prov[data-p="${pid}"] [data-role=result]`);
      if (box) out(box, { ok: r.test.ok, message: `Salva e criptografada · ${r.test.message}` });
    };
    el.querySelector('[data-act=toggle]').onclick = async () => { await api(`/api/admin/providers/${pid}`, { enabled: !prov.enabled }, 'PUT'); loadModels(); };
    el.querySelectorAll('.key-row').forEach((row) => {
      const kid = row.dataset.k;
      const key = prov.keys.find((k) => String(k.id) === kid);
      row.querySelector('[data-act=ktest]').onclick = async () => out(row.querySelector('[data-role=kres]'), await api(`/api/admin/keys/${kid}/test`, {}));
      row.querySelector('[data-act=kdefault]').onclick = async () => { await api(`/api/admin/keys/${kid}`, { is_default: true }, 'PATCH'); loadModels(); };
      row.querySelector('[data-act=ktoggle]').onclick = async () => { await api(`/api/admin/keys/${kid}`, { enabled: !key.enabled }, 'PATCH'); loadModels(); };
      row.querySelector('[data-act=krename]').onclick = async () => {
        const name = prompt('Novo nome da key', key.name);
        if (name) { await api(`/api/admin/keys/${kid}`, { name }, 'PATCH'); loadModels(); }
      };
      row.querySelector('[data-act=kdel]').onclick = async () => {
        if (!confirm(`Remover a key "${key.name}"?${key.models ? ` ${key.models} modelo(s) voltam a usar a key padrão.` : ''}`)) return;
        await api(`/api/admin/keys/${kid}`, undefined, 'DELETE');
        loadModels();
      };
    });
  });
  renderModels();
}

function capsTags(m) {
  const c = m.caps || {};
  const t = [];
  if (m.kind === 'video') {
    if (c.t2v) t.push('texto→vídeo');
    if (c.i2v) t.push('imagem→vídeo');
    if (c.end_frame) t.push('frame final (transição)');
    if (c.durations?.length) t.push(c.durations.length > 3 ? `${Math.min(...c.durations)}–${Math.max(...c.durations)}s` : `${c.durations.join('/')}s`);
    if (c.audio) t.push('áudio');
  }
  if (m.kind === 'image') {
    if (c.t2i) t.push('texto→imagem');
    if (c.refs) t.push('referências');
  }
  return t.map((x) => `<span class="tag">${x}</span>`).join('');
}

// Lista de keys do provedor direto na linha do modelo.
function keySelect(m) {
  const prov = A.providers.find((p) => p.id === m.provider);
  const ks = prov?.keys || [];
  const def = ks.find((k) => k.is_default);
  return `<label class="model-key" title="Key que este modelo usa nas chamadas">${ic('key')}
    <select data-act="keyid" class="${m.available ? '' : 'missing'}">
      ${ks.length ? `<option value="0" ${m.key_id ? '' : 'selected'}>Padrão${def ? `: ${esc(def.name)}` : ''}</option>` : '<option value="0" selected>Nenhuma key cadastrada</option>'}
      ${ks.map((k) => `<option value="${k.id}" ${m.key_id === k.id ? 'selected' : ''} ${k.enabled ? '' : 'disabled'}>${esc(k.name)} · ${esc(k.hint || '')}${k.enabled ? '' : ' (desativada)'}</option>`).join('')}
      <option value="new">＋ Cadastrar nova key…</option>
    </select></label>`;
}

// Funções que o estúdio usa e se há um modelo (ativo e com key) cobrindo cada uma.
const NEEDS = [
  { key: 'text', label: 'Escrever os prompts', uses: 'prompts técnicos, Roteiro, transições, sugestões', kind: 'text', need: {}, add: { provider: 'openrouter', kind: 'text', q: 'deepseek' }, optional: 'sem ele o app monta prompts por regras' },
  { key: 'vision', label: 'Ler imagens (visão)', uses: 'legenda do 1º frame, desenho da câmera, referências', kind: 'vision', need: {}, add: { provider: 'openrouter', kind: 'vision', q: 'gemini' }, optional: 'sem ele a IA não olha as imagens' },
  { key: 't2i', label: 'Imagem a partir de texto', uses: 'nó Gerar imagem, 1º frame de vídeo', kind: 'image', need: { t2i: true }, add: { provider: 'openrouter', kind: 'image', q: 'nano banana' } },
  { key: 'refs', label: 'Imagem com referências', uses: 'Personagens, Produtos, logo/produto no 1º frame', kind: 'image', need: { refs: true }, add: { provider: 'openrouter', kind: 'image', q: 'nano banana' } },
  { key: 't2v', label: 'Vídeo a partir de texto', uses: 'Gerar vídeo sem nada ligado', kind: 'video', need: { t2v: true }, add: { provider: 'openrouter', kind: 'video', q: 'kling' }, optional: 'sem ele o app gera uma imagem e anima' },
  { key: 'i2v', label: 'Vídeo a partir de imagem', uses: 'Gerar vídeo com 1º frame ligado', kind: 'video', need: { i2v: true }, add: { provider: 'openrouter', kind: 'video', q: 'kling' } },
  { key: 'vrefs', label: 'Vídeo com várias referências', uses: 'elenco, produto e desenho de câmera direto no vídeo', kind: 'video', need: { refs: true }, add: { provider: 'openrouter', kind: 'video', q: 'seedance' }, optional: 'sem ele as referências entram só no 1º frame' },
  { key: 'end', label: 'Vídeo com frame final', uses: 'nó Transição A→B', kind: 'video', need: { i2v: true, end_frame: true }, add: { provider: 'openrouter', kind: 'video', q: 'kling' } },
];

// Vídeo que recebe várias imagens + texto juntos (★ Recomendados no estúdio). Teste real 01/11/2026.
const REC_VIDEO_OR = [
  { id: 'bytedance/seedance-2.0-fast', label: 'Seedance 2.0 Fast', note: 'até 9 imagens · ótimo custo, manteve cena e produto no teste' },
  { id: 'bytedance/seedance-2.0', label: 'Seedance 2.0', note: 'até 9 imagens · até 1080p/4K' },
  { id: 'kwaivgi/kling-video-o1', label: 'Kling O1', note: 'até 7 imagens · produto fiel no teste' },
  { id: 'kwaivgi/kling-v3.0-std', label: 'Kling v3.0 Standard', note: 'até 7 imagens · ótimo no teste' },
  { id: 'alibaba/wan-3.0', label: 'Wan 3.0', note: 'até 5 imagens · barato (480p)' },
  { id: 'alibaba/happyhorse-1.1', label: 'HappyHorse 1.1', note: 'até 4 imagens · cena e produto fiéis no teste' },
  { id: 'bytedance/seedance-2.0-mini', label: 'Seedance 2.0 mini', note: 'até 9 imagens · o mais barato dos bons' },
];

function renderRecommended() {
  const card = (r) => {
    const inst = A.models.find((m) => m.provider === 'openrouter' && m.model_id === r.id && m.kind === 'video');
    return `<div class="need ${inst ? (inst.available ? 'ok' : 'nokey') : 'opt'}">
      <span class="need-ic">${ic(inst ? 'check' : 'star')}</span>
      <div class="need-main" title="${esc(r.id)}"><b>${esc(r.label)}</b><small>${esc(r.note)}</small></div>
      <div class="need-model"></div>
      ${inst ? `<span class="tag ${inst.available ? 'ok' : 'err'}">${inst.available ? 'instalado' : 'sem key'}</span>` : `<button class="btn small primary" data-rec="${esc(r.id)}">${ic('plus')} Adicionar</button>`}
    </div>`;
  };
  return `<div class="section-label"><span>★ Vídeo recomendado · várias imagens + texto</span><span class="hint">usa a key do OpenRouter</span></div>
    <p class="note">Recebem o frame, o elenco, o produto e o desenho de câmera junto com o texto e aparecem como <b>★ Recomendados</b> no estúdio. Os outros só recebem 1º/último frame. Teste real em 01/11/2026 com cena + produto.</p>
    <div class="needs">${REC_VIDEO_OR.map(card).join('')}</div>`;
}

function renderNeeds() {
  const ok = (m, need) => Object.entries(need).every(([k, v]) => !v || m.caps?.[k]);
  const rows = NEEDS.map((n) => {
    const cands = A.models.filter((m) => m.kind === n.kind && ok(m, n.need));
    const live = cands.filter((m) => m.available).sort((a, b) => b.is_default - a.is_default);
    const state = live.length ? 'ok' : cands.length ? 'nokey' : n.optional ? 'opt' : 'miss';
    const what = live.length ? `${esc(live[0].label)}${live.length > 1 ? ` <small>+${live.length - 1}</small>` : ''}`
      : cands.length ? `${esc(cands[0].label)} <small>sem key ativa</small>` : `<small>${n.optional ? `falta · ${esc(n.optional)}` : 'falta'}</small>`;
    return `<div class="need ${state}">
      <span class="need-ic">${ic(state === 'ok' ? 'check' : 'alert')}</span>
      <div class="need-main"><b>${esc(n.label)}</b><small>${esc(n.uses)}</small></div>
      <div class="need-model">${what}</div>
      ${state === 'ok' ? '' : `<button class="btn small ${state === 'miss' ? 'primary' : 'ghost'}" data-need="${n.key}">${state === 'nokey' ? `${ic('key')} Ver keys` : `${ic('plus')} Adicionar`}</button>`}
    </div>`;
  }).join('');
  const missing = NEEDS.filter((n) => !A.models.some((m) => m.kind === n.kind && m.available && ok(m, n.need)) && !n.optional).length;
  $('#needsBox').innerHTML = `<div class="section-label"><span>O que o estúdio precisa</span><span class="hint">${missing ? `${missing} função(ões) sem modelo` : 'tudo coberto'}</span></div>
    <p class="note">Um modelo pode cobrir várias funções (ex.: um vídeo com texto→vídeo, imagem→vídeo e frame final cobre as três). ✓ = coberto por um modelo ativo com key.</p>
    <div class="needs">${rows}</div>${renderRecommended()}`;
  document.querySelectorAll('[data-rec]').forEach((b) => b.addEventListener('click', async () => {
    const prov = 'openrouter';
    const r = REC_VIDEO_OR.find((x) => x.id === b.dataset.rec);
    b.disabled = true;
    try {
      const m = await api('/api/admin/models', { provider: prov, model_id: r.id, kind: 'video', label: r.label });
      toast(`${m.label} adicionado`);
      A.models = await api('/api/admin/models');
      renderModels();
    } catch (e) { toast(e.message); b.disabled = false; }
  }));
  document.querySelectorAll('[data-need]').forEach((b) => b.addEventListener('click', () => {
    const n = NEEDS.find((x) => x.key === b.dataset.need);
    const hasModel = A.models.some((m) => m.kind === n.kind && ok(m, n.need));
    if (hasModel) return document.querySelector('#providers').scrollIntoView({ behavior: 'smooth' });
    addModelDialog(n.add);
  }));
}

function renderModels() {
  renderNeeds();
  const provOk = Object.fromEntries(A.providers.map((p) => [p.id, p.configured && p.enabled]));
  $('#modelsList').innerHTML = ['video', 'image', 'text', 'vision'].map((kind) => {
    const list = A.models.filter((m) => m.kind === kind);
    return `<div class="model-group"><h5>${KIND_LABEL[kind]}</h5>${list.length ? list.map((m) => `
      <div class="model ${m.enabled ? '' : 'off'}" data-m="${m.id}">
        <button class="star ${m.is_default ? 'on' : ''}" data-act="default" title="Padrão do Automático" aria-label="Padrão do Automático">${ic('star')}</button>
        <div class="name"><b>${esc(m.label)}</b> <span class="tag">${esc(m.provider)}</span>${m.available ? ` <span class="tag key">${ic('key')} ${esc(m.key_name || 'padrão')}${m.key_id ? '' : ' (padrão)'}</span>` : ' <span class="tag err">sem key</span>'}
          <small>${esc(m.model_id)}</small><div class="caps">${capsTags(m)}</div></div>
        ${keySelect(m)}
        <div class="price" title="${m.kind === 'text' || m.kind === 'vision' ? 'Cobrado por token: o valor exato vem em cada resposta e vai para os logs' : 'Estimativa usada nos limites de gasto; o valor cobrado vem do provedor'}">${m.cost ? `~${fmtUsd(m.cost)}${m.cost_unit === 'second' ? '/s' : m.kind === 'video' ? '/vídeo' : '/imagem'}` : (m.kind === 'text' || m.kind === 'vision' ? 'por token (real)' : 'sem preço · ↻')}</div>
        <div class="row-actions">
          <label class="inline" title="Aparece no estúdio"><input type="checkbox" data-act="enabled" ${m.enabled ? 'checked' : ''}> ativo</label>
          <button class="btn small ghost" data-act="edit">Editar</button>
          <button class="btn small ghost" data-act="refresh" title="Reler capacidades e preço do provedor" aria-label="Reler capacidades e preço do provedor">${ic('refresh')}</button>
          <button class="btn small ghost" data-act="delete" title="Remover" aria-label="Remover">${ic('trash')}</button>
        </div>
      </div>`).join('') : '<p class="note">Nenhum modelo.</p>'}</div>`;
  }).join('');
  document.querySelectorAll('.model').forEach((el) => {
    const m = A.models.find((x) => x.id === +el.dataset.m);
    const patch = async (body) => { await api(`/api/admin/models/${m.id}`, body, 'PATCH'); A.models = await api('/api/admin/models'); renderModels(); };
    el.querySelector('[data-act=default]').onclick = () => patch({ is_default: !m.is_default });
    el.querySelector('[data-act=enabled]').onchange = (e) => patch({ enabled: e.target.checked });
    el.querySelector('[data-act=keyid]')?.addEventListener('change', async (e) => {
      if (e.target.value === 'new') {
        e.target.value = String(m.key_id || 0);
        document.querySelector(`.prov[data-p="${m.provider}"] [data-role=kname]`)?.focus();
        document.querySelector(`.prov[data-p="${m.provider}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        return toast('Cadastre a key no provedor acima: ela aparece nesta lista');
      }
      await patch({ key_id: +e.target.value });
      toast(`${m.label}: usando a key ${A.models.find((x) => x.id === m.id)?.key_name || 'padrão'}`);
    });
    el.querySelector('[data-act=edit]').onclick = () => editModelDialog(m);
    el.querySelector('[data-act=refresh]')?.addEventListener('click', async () => {
      await api(`/api/admin/models/${m.id}/refresh`, {}).then(() => toast('Capacidades e preço atualizados')).catch((e) => toast(e.message));
      A.models = await api('/api/admin/models');
      renderModels();
    });
    el.querySelector('[data-act=delete]').onclick = async () => {
      if (!confirm(`Remover ${m.label}? Nós que usam ele passam para o Automático.`)) return;
      await api(`/api/admin/models/${m.id}`, undefined, 'DELETE');
      A.models = await api('/api/admin/models');
      renderModels();
    };
  });
}

function editModelDialog(m) {
  openDialog(`<h3>${esc(m.label)}</h3><small class="mono">${esc(m.provider)} · ${esc(m.model_id)}</small>
    <label>Nome no estúdio<input name="label" value="${esc(m.label)}"></label>
    <label>Key usada por este modelo<select name="key_id"><option value="0">Padrão do provedor${(() => { const d = A.providers.find((p) => p.id === m.provider)?.keys.find((k) => k.is_default); return d ? ` (${esc(d.name)})` : ''; })()}</option>
      ${(A.providers.find((p) => p.id === m.provider)?.keys || []).map((k) => `<option value="${k.id}" ${m.key_id === k.id ? 'selected' : ''}>${esc(k.name)} · ${esc(k.hint || '')}${k.enabled ? '' : ' (desativada)'}</option>`).join('')}</select></label>
    <div class="limits-grid">
      <label>Custo estimado (US$)<input name="cost" type="number" step="0.0001" min="0" value="${m.cost}"></label>
      <label>Cobrado por<select name="cost_unit"><option value="run">geração</option><option value="second" ${m.cost_unit === 'second' ? 'selected' : ''}>segundo de vídeo</option></select></label>
    </div>
    ${m.kind === 'video' || m.kind === 'image' ? `<div class="section-label"><span>Capacidades</span><span class="hint">detectadas pelo schema; corrija se precisar</span></div>
    <div class="checks">${(m.kind === 'video' ? [['t2v', 'texto→vídeo'], ['i2v', 'imagem→vídeo'], ['end_frame', 'frame final']] : [['t2i', 'texto→imagem'], ['refs', 'referências']])
      .map(([k, l]) => `<label><input type="checkbox" name="cap_${k}" ${m.caps[k] ? 'checked' : ''}> ${l}</label>`).join('')}</div>` : ''}
    <label>Parâmetros fixos (JSON, enviados em toda chamada)<textarea name="params" rows="4" placeholder='ex.: {"generate_audio": true}'>${esc(Object.keys(m.params || {}).length ? JSON.stringify(m.params, null, 2) : '')}</textarea></label>
    ${m.fields?.props ? `<details><summary class="note">Campos aceitos pelo modelo</summary><pre class="mono note">${esc(Object.entries(m.fields.props).map(([k, v]) => `${k}: ${v.types.join('|')}${v.enum?.length ? ` [${v.enum.join(', ')}]` : ''}`).join('\n'))}</pre></details>` : ''}
    <div class="dlg-actions"><button class="btn ghost" value="cancel">Cancelar</button><button class="btn primary" value="ok">Salvar</button></div>`,
  async (f) => {
    let params = {};
    if (f.params.value.trim()) {
      try { params = JSON.parse(f.params.value); } catch { toast('JSON inválido nos parâmetros'); return false; }
    }
    const caps = {};
    f.querySelectorAll('[name^=cap_]').forEach((c) => { caps[c.name.slice(4)] = c.checked; });
    await api(`/api/admin/models/${m.id}`, { label: f.label.value, key_id: +f.key_id.value, cost: +f.cost.value, cost_unit: f.cost_unit.value, params, caps }, 'PATCH');
    A.models = await api('/api/admin/models');
    renderModels();
  });
}

function addModelDialog(preset = {}) {
  const f = openDialog(`<h3>Adicionar modelo</h3>
    <div class="limits-grid">
      <label>Provedor<select name="provider"><option value="openrouter">OpenRouter (vídeo, imagem, texto, visão)</option></select></label>
      <label>Tipo<select name="kind"></select></label>
    </div>
    <label>Buscar no catálogo<input name="q" placeholder="ex.: kling, veo, seedream, flux, nano banana…" autocomplete="off"></label>
    <div class="suggest" id="suggest"></div>
    <label>ID do modelo<input name="model_id" required placeholder="ex.: bytedance/seedance-2.0-fast"></label>
    <label>Nome no estúdio (opcional)<input name="label"></label>
    <label class="inline"><input type="checkbox" name="is_default"> Tornar padrão do Automático</label>
    <p class="note" id="addHint">O app lê o catálogo do provedor: tipo, capacidades (texto→vídeo, imagem→vídeo, frame final), durações e preço são detectados sozinhos.</p>
    <div class="dlg-actions"><button class="btn ghost" value="cancel">Cancelar</button><button class="btn primary" value="ok">Adicionar</button></div>`,
  async (form) => {
    const m = await api('/api/admin/models', { provider: form.provider.value, model_id: form.model_id.value, kind: form.kind.value || null,
      label: form.label.value || null, is_default: form.is_default.checked });
    toast(`${m.label} adicionado (${KIND_LABEL[m.kind]})`);
    A.models = await api('/api/admin/models');
    renderModels();
  });
  const kinds = {
    openrouter: [['video', 'Vídeo'], ['image', 'Imagem'], ['text', 'Texto · escreve os prompts'], ['vision', 'Visão · lê imagens']] };
  const setKinds = () => {
    f.kind.innerHTML = kinds[f.provider.value].map(([v, l]) => `<option value="${v}">${l}</option>`).join('');
    search();
  };
  let t;
  const search = () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      const pid = f.provider.value;
      const kind = f.kind.value;
      $('#suggest').innerHTML = '<div>buscando…</div>';
      try {
        const rows = await api(`/api/admin/catalog/${pid}?q=${encodeURIComponent(f.q.value)}&kind=${pid === 'openrouter' ? kind : kind}`);
        const recs = [];
        const recHtml = '';
        $('#suggest').innerHTML = recHtml + (rows.length ? rows.map((r) => {
          const tags = r.tags || (r.category ? [r.category] : []);
          return `<div class="sug-row ${r.max_refs ? 'rec' : ''}" data-id="${esc(r.id)}" data-name="${esc(r.name)}">
            <div class="sug-top"><b>${esc(r.name)}</b>${r.price ? `<span class="sug-price">${esc(r.price)}</span>` : ''}</div>
            <code>${esc(r.id)}</code>
            ${tags.length || r.description ? `<div class="sug-tags">${tags.map((t) => `<span class="tag">${esc(t)}</span>`).join('')}${r.description ? `<span class="sug-desc">${esc(r.description)}</span>` : ''}</div>` : ''}
          </div>`;
        }).join('') : recs.length ? '' : '<div class="sug-row">Nada encontrado</div>');
        $('#suggest').querySelectorAll('[data-id]').forEach((d) => d.addEventListener('click', () => { f.model_id.value = d.dataset.id; f.label.value = d.dataset.name; }));
      } catch (e) { $('#suggest').innerHTML = `<div>${esc(e.message)}</div>`; }
    }, 300);
  };
  f.provider.onchange = setKinds;
  f.kind.onchange = search;
  f.q.oninput = search;
  if (preset.provider) f.provider.value = preset.provider;
  setKinds();
  if (preset.kind != null) f.kind.value = preset.kind;
  if (preset.q) f.q.value = preset.q;
  if (preset.kind != null || preset.q) search();
}

// ---------------- Logs ----------------

async function loadLogs() {
  const qs = new URLSearchParams({ limit: 100, offset: A.logOffset });
  if ($('#logUser').value) qs.set('user_id', $('#logUser').value);
  if ($('#logKind').value) qs.set('kind', $('#logKind').value);
  if ($('#logStatus').value) qs.set('status', $('#logStatus').value);
  const r = await api(`/api/admin/logs?${qs}`);
  A.logRows = r.rows;
  $('#logTotals').textContent = `${r.total} registros · custo somado ${fmtUsd(r.cost)}`;
  $('#logPage').textContent = r.total ? `${A.logOffset + 1}–${Math.min(r.total, A.logOffset + 100)} de ${r.total}` : '';
  $('#logPrev').disabled = A.logOffset === 0;
  $('#logNext').disabled = A.logOffset + 100 >= r.total;
  $('#logsTbl').innerHTML = '<tr><th>Quando</th><th>Usuário</th><th>Tipo</th><th>Modelo</th><th class="num">Custo</th><th>Status</th><th>Detalhe</th></tr>'
    + r.rows.map((g) => `<tr><td><small>${fmtDate(g.ts)}</small></td><td><small>${esc(g.email || '—')}</small></td><td><span class="tag">${esc(g.kind)}</span></td>
      <td><small class="mono">${esc(g.model || '')}</small></td><td class="num">${g.cost ? fmtUsd(g.cost) : ''}</td>
      <td><span class="tag ${g.status === 'ok' ? 'ok' : 'err'}">${esc(g.status)}</span></td><td class="detail" title="${esc(g.detail || '')}">${esc(g.detail || '')}</td></tr>`).join('');
}

async function exportCsv() {
  const qs = new URLSearchParams({ limit: 500, offset: 0 });
  ['user_id:logUser', 'kind:logKind', 'status:logStatus'].forEach((p) => { const [k, id] = p.split(':'); if ($(`#${id}`).value) qs.set(k, $(`#${id}`).value); });
  const r = await api(`/api/admin/logs?${qs}`);
  const cols = ['ts', 'email', 'kind', 'provider', 'model', 'cost', 'status', 'detail'];
  const csv = [cols.join(',')].concat(r.rows.map((g) => cols.map((c) => `"${String(c === 'ts' ? new Date(g.ts * 1000).toISOString() : g[c] ?? '').replace(/"/g, '""')}"`).join(','))).join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = `logs-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
}

// ---------------- Arquivos ----------------

async function loadFiles() {
  const uid = $('#fileUser').value;
  const r = await api(`/api/admin/files${uid ? `?user_id=${uid}` : ''}`);
  A.files = r.files;
  A.sel.clear();
  renderFiles();
}

function renderFiles() {
  const kind = $('#fileKind').value;
  const list = A.files.filter((f) => !kind || f.kind === kind);
  $('#fileTotals').textContent = `${list.length} arquivos · ${fmtBytes(list.reduce((t, f) => t + f.size, 0))}`;
  $('#fileDelSel').disabled = !A.sel.size;
  setBtn($('#fileDelSel'), 'trash', `Apagar selecionados${A.sel.size ? ` (${A.sel.size})` : ''}`);
  $('#filesGrid').innerHTML = list.map((f) => {
    const thumb = f.kind === 'image' ? f.url : f.kind === 'video' ? f.url.replace(/\.[^.]+$/, '_first.png') : null;
    return `<div class="fcard ${A.sel.has(f.url) ? 'sel' : ''}">
      <input type="checkbox" data-sel="${esc(f.url)}" ${A.sel.has(f.url) ? 'checked' : ''}>
      <div class="thumb" data-open="${esc(f.url)}" ${thumb ? `style="background-image:url('${thumb}')"` : ''}>${f.kind === 'audio' ? ic('music') : f.kind === 'video' ? ic('play') : ''}</div>
      <div class="meta"><b title="${esc(f.name)}">${esc(f.name)}</b>${esc(f.email)} · ${f.folder}<br>${fmtBytes(f.size)} · ${fmtDate(f.mtime)}</div>
    </div>`;
  }).join('') || '<p class="note">Nenhum arquivo.</p>';
  document.querySelectorAll('[data-sel]').forEach((c) => { c.onchange = () => { c.checked ? A.sel.add(c.dataset.sel) : A.sel.delete(c.dataset.sel); renderFiles(); }; });
  document.querySelectorAll('[data-open]').forEach((d) => { d.onclick = () => window.open(d.dataset.open, '_blank'); });
}

async function deleteFiles(urls) {
  if (!urls.length || !confirm(`Apagar ${urls.length} arquivo(s)? Não dá para desfazer.`)) return;
  const r = await api('/api/admin/files/delete', { urls });
  toast(`${urls.length} arquivo(s) apagados · ${fmtBytes(r.freed)} liberados`);
  loadFiles();
}

// ---------------- Configurações ----------------

async function loadSettings() {
  const s = await api('/api/admin/settings');
  if (!A.models.length) A.models = await api('/api/admin/models');
  $('#setLimits').innerHTML = limitsInputs(s.default_limits) + `<div style="grid-column: span 2">${modelChecks(s.default_limits.models)}</div>`;
  $('#setTokenRate').value = s.token_rate;
  $('#setLang').value = s.default_lang || 'en';
  const hint = () => { const r = +$('#setTokenRate').value || 0; $('#setTokenHint').textContent = r ? `1 token = US$ ${(1 / r).toFixed(4)} · um vídeo de US$ 0,50 = ${Math.round(0.5 * r)} tokens` : ''; };
  $('#setTokenRate').oninput = hint; hint();
  $('#setLimits').querySelector('[name=all_models]').onchange = (e) => { $('#modelChecks').hidden = e.target.checked; };
}

async function saveSettings() {
  const box = $('#setLimits');
  const f = { all_models: box.querySelector('[name=all_models]'), querySelectorAll: (q) => box.querySelectorAll(q) };
  LIMIT_FIELDS.forEach(([k]) => { f[`lim_${k}`] = box.querySelector(`[name=lim_${k}]`); });
  const lim = readLimits(f);
  LIMIT_FIELDS.forEach(([k]) => { if (!(k in lim)) lim[k] = null; }); // nas configurações, vazio = sem limite
  await api('/api/admin/settings', { default_limits: lim, token_rate: +$('#setTokenRate').value || 100, default_lang: $('#setLang').value }, 'PUT');
  toast('Configurações salvas');
  loadMe();
}

// ---------------- Branding ----------------

const BR = { data: null, draft: null };

async function loadBranding() {
  BR.data = await api('/api/admin/branding');
  BR.draft = { app_name: BR.data.app_name, tagline: BR.data.tagline, theme: BR.data.theme, accent: BR.data.accent,
    accent2: BR.data.accent2, bg: BR.data.bg, style: BR.data.style };
  $('#brName').value = BR.draft.app_name || '';
  $('#brTagline').value = BR.draft.tagline || '';
  $('#brName').oninput = (e) => { BR.draft.app_name = e.target.value; previewBranding(); };
  $('#brTagline').oninput = (e) => { BR.draft.tagline = e.target.value; previewBranding(); };
  $('#brThemes').innerHTML = Object.entries(BR.data.themes).map(([k, t]) => `<button class="br-theme" data-theme="${k}" style="--a:${t.accent};--b:${t.accent2};--bg:${t.bg}">
    <span class="sw"><i></i><i></i><i></i></span><b>${esc(t.label)}</b></button>`).join('');
  document.querySelectorAll('[data-theme]').forEach((b) => { b.onclick = () => { BR.draft.theme = b.dataset.theme; BR.draft.accent = BR.draft.accent2 = BR.draft.bg = null; previewBranding(); }; });
  [['brAccent', 'accent'], ['brAccent2', 'accent2'], ['brBg', 'bg']].forEach(([id, k]) => {
    $(`#${id}`).oninput = (e) => { BR.draft[k] = e.target.value; previewBranding(); };
  });
  document.querySelectorAll('[data-clear]').forEach((b) => { b.onclick = (e) => { e.preventDefault(); BR.draft[{ brAccent: 'accent', brAccent2: 'accent2', brBg: 'bg' }[b.dataset.clear]] = null; previewBranding(); }; });
  document.querySelectorAll('[data-style]').forEach((b) => { b.onclick = () => { BR.draft.style = b.dataset.style; previewBranding(); }; });
  $('#brSave').onclick = saveBranding;
  $('#brLogoFile').onchange = async (e) => {
    const fd = new FormData();
    fd.append('file', e.target.files[0]);
    const r = await fetch('/api/admin/branding/logo', { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok) return toast(d.detail || 'Falha no upload');
    BR.data = d;
    toast('Logo atualizado');
    previewBranding();
    applyBranding(d);
  };
  $('#brLogoDel').onclick = async () => { BR.data = await api('/api/admin/branding/logo', undefined, 'DELETE'); previewBranding(); applyBranding(BR.data); };
  previewBranding();
}

function draftColors() {
  const t = BR.data.themes[BR.draft.theme] || BR.data.themes.emerald;
  return { accent: BR.draft.accent || t.accent, accent2: BR.draft.accent2 || t.accent2, bg: BR.draft.bg || t.bg };
}

function previewBranding() {
  const c = draftColors();
  $('#brAccent').value = c.accent; $('#brAccent2').value = c.accent2; $('#brBg').value = c.bg;
  document.querySelectorAll('[data-theme]').forEach((b) => b.classList.toggle('active', b.dataset.theme === BR.draft.theme && !BR.draft.accent && !BR.draft.bg));
  document.querySelectorAll('[data-style]').forEach((b) => b.classList.toggle('active', b.dataset.style === BR.draft.style));
  $('#brLogoBox').innerHTML = BR.data.logo_url ? `<img src="${BR.data.logo_url}" alt="">` : ic('logo');
  applyBranding({ colors: c, style: BR.draft.style, logo_url: BR.data.logo_url });
  $('#brPreview').innerHTML = `
    <div class="brp-top"><span class="brp-logo">${BR.data.logo_url ? `<img src="${BR.data.logo_url}" alt="">` : ic('logo')}</span><b>${esc(BR.draft.app_name || 'Studio')}</b>
      <span class="tab active">${ic('video')} Video Studio</span><span class="tab">${ic('user')} Personagens</span></div>
    <div class="brp-body">
      <div class="brp-node"><div class="brp-head"><span class="cv-kicon">${ic('video')}</span><span><small>GERAR VÍDEO</small><b>Plano 1</b></span></div>
        <div class="brp-media"></div><div class="brp-btns"><button class="btn ghost">${ic('sparkles')} Prompt</button><button class="btn primary">${ic('play')} Gerar vídeo</button></div></div>
      <div class="brp-login"><b>${esc(BR.draft.app_name || 'Studio')}</b><small>${esc(BR.draft.tagline || '')}</small><input placeholder="e-mail"><button class="btn primary">Entrar</button></div>
    </div>`;
}

async function saveBranding() {
  BR.data = await api('/api/admin/branding', BR.draft, 'PUT');
  applyBranding(BR.data);
  document.querySelectorAll('[data-app-name]').forEach((el) => { el.textContent = BR.data.app_name; });
  toast('Branding salvo para todos os usuários');
}

// ---------------- Prompts ----------------

async function loadPrompts() {
  const list = await api('/api/admin/prompts');
  $('#promptList').innerHTML = list.map((p) => `
    <div class="pr-item" data-key="${p.key}">
      <div class="pr-head"><div><span class="tag">${esc(p.node)}</span> <b>${esc(p.label)}</b> ${p.custom ? '<span class="tag ok">editado</span>' : '<span class="tag">padrão</span>'}</div>
        <div class="row-actions"><button class="btn small ghost" data-reset="${p.key}" ${p.custom ? '' : 'disabled'}>${ic('undo')} Restaurar padrão</button><button class="btn small primary" data-savep="${p.key}">${ic('check')} Salvar</button></div></div>
      <p class="note">${esc(p.help)}${p.vars.length ? ` · variáveis: ${p.vars.map((v) => `<code>{${v}}</code>`).join(' ')}` : ''}</p>
      <textarea rows="${Math.min(16, Math.max(5, Math.ceil(p.text.length / 120)))}" spellcheck="false">${esc(p.text)}</textarea>
    </div>`).join('');
  document.querySelectorAll('[data-savep]').forEach((b) => { b.onclick = async () => {
    const box = b.closest('.pr-item');
    await api(`/api/admin/prompts/${b.dataset.savep}`, { text: box.querySelector('textarea').value }, 'PUT');
    toast('Prompt salvo');
    loadPrompts();
  }; });
  document.querySelectorAll('[data-reset]').forEach((b) => { b.onclick = async () => {
    if (!confirm('Voltar este prompt ao texto padrão?')) return;
    await api(`/api/admin/prompts/${b.dataset.reset}`, { text: '' }, 'PUT');
    toast('Prompt restaurado');
    loadPrompts();
  }; });
}

// ---------------- Grupos ----------------

const G = { data: null };

async function ensureGroups() {
  G.data = await api('/api/admin/groups');
  return G.data;
}

const permLabel = (k) => G.data?.permissions.find((p) => p.key === k)?.label || k;
const groupTag = (g) => `<span class="tag gtag" style="--g:${esc(g.color)}" title="${g.source === 'microsoft' ? 'Atribuído pela Microsoft' : 'Atribuído no painel'}">${g.source === 'microsoft' ? ic('cloud') : ''}${esc(g.name)}</span>`;

function limitSummary(lim, budgetTok) {
  const parts = lim.budget_usd != null ? [`Verba US$ ${lim.budget_usd}${lim.budget_split ? ' dividida' : '/pessoa'} = ${Number(budgetTok || 0).toLocaleString(LOC)} tokens/pessoa`] : [];
  parts.push(...LIMIT_FIELDS.filter(([k]) => k in lim && !(k === 'tokens_month' && lim.budget_usd != null)).map(([k, label]) => `${label.replace(' por mês', '/mês')}: ${lim[k] == null ? '∞' : lim[k]}`));
  if ('models' in lim) parts.push(lim.models == null ? 'todos os modelos' : `${lim.models.length} modelo(s)`);
  return parts.length ? parts.map((x) => t(x)).join(' · ') : 'limites padrão'; // cada parte traduz sozinha
}

async function loadGroups() {
  await ensureGroups();
  if (!A.users.length) A.users = await api('/api/admin/users');
  if (!A.models.length) A.models = await api('/api/admin/models');
  $('#btnNewGroup').onclick = () => groupDialog(null);
  $('#groupsList').innerHTML = G.data.groups.length ? G.data.groups.map((g) => {
    const ms = g.members.filter((m) => m.source === 'microsoft').length;
    const perms = Object.entries(g.permissions).filter(([k, v]) => v && !k.startsWith('node_')).map(([k]) => `<span class="tag ${k === 'admin' ? 'admin' : 'ok'}">${esc(permLabel(k))}</span>`).join('') || '<span class="tag off">nenhuma permissão</span>';
    return `<div class="gcard" style="--g:${esc(g.color)}">
      <div class="gcard-head"><span class="gdot"></span><b>${esc(g.name)}</b>
        <small>${g.members.length} membro(s)${ms ? ` · ${ms} via Microsoft` : ''}</small>
        <span class="row-actions"><button class="btn small ghost" data-gedit="${g.id}">Editar</button><button class="btn small ghost" data-gdel="${g.id}" title="Excluir grupo" aria-label="Excluir grupo">${ic('trash')}</button></span></div>
      ${g.description ? `<p class="note">${esc(g.description)}</p>` : ''}
      <div class="gperms">${perms}</div>
      ${(() => { const off = G.data.nodes.filter((n) => g.permissions[n.key] === false); return off.length ? `<div class="gmeta">Nodes bloqueados: ${off.map((n) => `<span class="tag off">${esc(n.label)}</span>`).join('')}</div>` : '<div class="gmeta">todos os nodes liberados</div>'; })()}
      <div class="gmeta">${esc(limitSummary(g.limits, g.budget_tokens))}</div>
      <div class="gmeta">${g.rules.length ? `${ic('cloud')} Automático: ${g.rules.map((r) => `<span class="tag mono">${esc(r.type === 'domain' ? '@' + r.value : r.value)}</span>`).join(' ')}` : '<span class="muted">sem atribuição automática</span>'}</div>
    </div>`;
  }).join('') : '<p class="note">Nenhum grupo ainda. Crie um, por exemplo "Equipe criativa" (gera imagem e vídeo) e "Revisores" (só vê o Video Studio).</p>';
  document.querySelectorAll('[data-gedit]').forEach((b) => { b.onclick = () => groupDialog(G.data.groups.find((g) => g.id === +b.dataset.gedit)); });
  document.querySelectorAll('[data-gdel]').forEach((b) => {
    b.onclick = async () => {
      const g = G.data.groups.find((x) => x.id === +b.dataset.gdel);
      if (!confirm(`Excluir o grupo "${g.name}"?\nOs ${g.members.length} membro(s) saem dele (as contas continuam).`)) return;
      await api(`/api/admin/groups/${g.id}`, undefined, 'DELETE').then(() => toast('Grupo excluído')).catch((e) => toast(e.message));
      A.users = [];
      loadGroups();
    };
  });
}

function groupModelsBlock(lim) {
  const mode = !('models' in lim) ? 'inherit' : lim.models == null ? 'all' : 'pick';
  return `<label>Modelos liberados<select name="models_mode">
      <option value="inherit" ${mode === 'inherit' ? 'selected' : ''}>Padrão das contas</option>
      <option value="all" ${mode === 'all' ? 'selected' : ''}>Todos os modelos habilitados</option>
      <option value="pick" ${mode === 'pick' ? 'selected' : ''}>Só os escolhidos</option></select></label>
    <div class="checks" id="gModelChecks" ${mode === 'pick' ? '' : 'hidden'}>${A.models.filter((m) => ['image', 'video'].includes(m.kind)).map((m) =>
      `<label><input type="checkbox" name="gmodel" value="${m.id}" ${mode === 'pick' && lim.models.includes(m.id) ? 'checked' : ''}> ${esc(m.label)}</label>`).join('')}</div>`;
}

function rulesBlock(g) {
  if (!g) return '<p class="note">Salve o grupo para adicionar regras de atribuição automática.</p>';
  return `<div class="rules">${g.rules.map((r) => `<div class="rule"><span class="tag">${esc(G.data.rule_types[r.type] || r.type)}</span><code>${esc(r.value)}</code><button type="button" class="btn small ghost" data-rdel="${r.id}" title="Remover regra" aria-label="Remover regra">${ic('x')}</button></div>`).join('') || '<p class="note">Nenhuma regra.</p>'}</div>
    <div class="rule-add"><select id="gRuleType">${Object.entries(G.data.rule_types).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('')}</select>
      <input id="gRuleValue" placeholder="ex.: empresa.com"><button type="button" class="btn small" id="gRuleAdd">${ic('plus')} Adicionar</button></div>`;
}

function bindRules(f, g) {
  if (!g) return;
  const refresh = (ng) => { Object.assign(g, ng); f.querySelector('#gRulesBox').innerHTML = rulesBlock(g); bindRules(f, g); };
  const ph = { domain: 'ex.: empresa.com', entra_group: 'Object ID do grupo no Entra', app_role: 'ex.: Studio.Creator', tenant: 'ID do diretório' };
  const type = f.querySelector('#gRuleType');
  type.onchange = () => { f.querySelector('#gRuleValue').placeholder = ph[type.value]; };
  f.querySelector('#gRuleAdd').onclick = async () => {
    const value = f.querySelector('#gRuleValue').value.trim();
    if (!value) return;
    try { refresh(await api(`/api/admin/groups/${g.id}/rules`, { type: type.value, value })); } catch (e) { toast(e.message); }
  };
  f.querySelectorAll('[data-rdel]').forEach((b) => {
    b.onclick = async () => {
      await api(`/api/admin/rules/${b.dataset.rdel}`, undefined, 'DELETE');
      refresh({ rules: g.rules.filter((r) => r.id !== +b.dataset.rdel) });
    };
  });
}

async function groupDialog(g) {
  if (!G.data) await ensureGroups();
  if (!A.users.length) A.users = await api('/api/admin/users');
  if (!A.models.length) A.models = await api('/api/admin/models');
  const settings = await api('/api/admin/settings');
  const perms = g ? g.permissions : { ws_video: true, ws_character: true, ws_product: true, gen_image: true, gen_video: true, ai_assist: true, upload: true, delete_files: true };
  const manual = new Set((g?.members || []).filter((m) => m.source === 'manual').map((m) => m.user_id));
  const msMembers = (g?.members || []).filter((m) => m.source === 'microsoft');
  const f = openDialog(`<h3>${g ? 'Editar grupo' : 'Novo grupo'}</h3>
    <div class="limits-grid">
      <label>Nome<input name="gname" required maxlength="60" value="${esc(g?.name || '')}"></label>
      <label>Cor<input name="gcolor" type="color" value="${esc(g?.color || '#10b981')}"></label>
    </div>
    <label>Descrição<input name="gdesc" maxlength="300" value="${esc(g?.description || '')}" placeholder="Para que serve este grupo"></label>
    <div class="section-label"><span>Permissões base</span></div>
    <div class="perm-grid">${G.data.permissions.map((p) => `<label class="perm ${p.key === 'admin' ? 'danger' : ''}"><input type="checkbox" name="perm_${p.key}" ${perms[p.key] ? 'checked' : ''}><span><b>${esc(p.label)}</b><small>${esc(p.help)}</small></span></label>`).join('')}</div>
    <div class="section-label"><span>Nodes liberados</span><span class="hint"><a href="#" data-nodes="all">todos</a> · <a href="#" data-nodes="none">nenhum</a></span></div>
    <div class="perm-grid nodes">${G.data.nodes.map((p) => `<label class="perm"><input type="checkbox" name="perm_${p.key}" ${perms[p.key] !== false ? 'checked' : ''}><span><b>${esc(p.label)}</b><small>${esc({ video: 'Video Studio', character: 'Personagens', product: 'Produtos', todas: 'todas as abas', 'character/product': 'Personagens e Produtos' }[p.area] || p.area)}</small></span></label>`).join('')}</div>
    <div class="section-label"><span>Verba de tokens</span><span class="hint">1 US$ = ${Number(settings.token_rate || 100).toLocaleString(LOC)} tokens</span></div>
    <div class="budget-box">
      <label>Verba por mês (US$)<input name="budget_usd" inputmode="decimal" value="${g?.limits?.budget_usd ?? ''}" placeholder="vazio = limite padrão"></label>
      <label>Como dividir<select name="budget_split"><option value="0">cada pessoa recebe esse valor</option><option value="1" ${g?.limits?.budget_split ? 'selected' : ''}>dividir entre os membros</option></select></label>
      <div class="budget-out" id="budgetOut"></div>
    </div>
    ${groupModelsBlock(g?.limits || {})}
    <div class="section-label"><span>Membros</span><span class="hint">${msMembers.length ? `${msMembers.length} entraram pela regra da Microsoft` : 'marque as contas'}</span></div>
    <div class="checks members">${A.users.map((u) => {
      const viaMs = msMembers.some((m) => m.user_id === u.id);
      return `<label title="${viaMs ? 'Também está no grupo pela regra da Microsoft' : ''}"><input type="checkbox" name="member" value="${u.id}" ${manual.has(u.id) ? 'checked' : ''}> ${esc(u.name || u.email)} <small class="mono">${esc(u.email)}</small>${viaMs ? ` <span class="tag">${ic('cloud')} auto</span>` : ''}</label>`;
    }).join('')}</div>
    <div class="section-label"><span>Atribuição automática (login Microsoft)</span></div>
    <p class="note">A cada login com Microsoft, quem bater em qualquer regra entra neste grupo, e sai se deixar de bater. Membros marcados à mão não são afetados.</p>
    <div id="gRulesBox">${rulesBlock(g)}</div>
    <div class="dlg-actions"><button class="btn ghost" value="cancel">Cancelar</button><button class="btn primary" value="ok">${g ? 'Salvar' : 'Criar grupo'}</button></div>`,
  async (form) => {
    const lim = {};
    const bud = form.budget_usd.value.trim().replace(',', '.');
    if (bud !== '') { lim.budget_usd = Math.max(0, +bud || 0); lim.budget_split = form.budget_split.value === '1'; delete lim.tokens_month; }
    const mode = form.models_mode.value;
    if (mode === 'all') lim.models = null;
    if (mode === 'pick') lim.models = [...form.querySelectorAll('[name=gmodel]:checked')].map((c) => +c.value);
    const body = { name: form.gname.value, description: form.gdesc.value, color: form.gcolor.value, limits: lim,
      permissions: Object.fromEntries([...G.data.permissions, ...G.data.nodes].map((p) => [p.key, form[`perm_${p.key}`].checked])),
      member_ids: [...form.querySelectorAll('[name=member]:checked')].map((c) => +c.value) };
    await api(g ? `/api/admin/groups/${g.id}` : '/api/admin/groups', body, g ? 'PUT' : 'POST');
    toast(g ? 'Grupo salvo' : 'Grupo criado');
    A.users = [];
    loadGroups();
  });
  f.querySelectorAll('[data-nodes]').forEach((a) => { a.onclick = (e) => { e.preventDefault(); f.querySelectorAll('.perm-grid.nodes input').forEach((c) => { c.checked = a.dataset.nodes === 'all'; }); }; });
  f.querySelector('[name=models_mode]').onchange = (e) => { f.querySelector('#gModelChecks').hidden = e.target.value !== 'pick'; };
  bindRules(f, g);
  // prévia: verba em US$ -> tokens por pessoa (atualiza ao digitar e ao marcar membros)
  const rate = +settings.token_rate || 100;
  const budget = () => {
    const usd = +f.budget_usd.value.trim().replace(',', '.');
    const has = f.budget_usd.value.trim() !== '' && !Number.isNaN(usd);
    if (!has) { $('#budgetOut').innerHTML = '<span class="muted">Vazio = membros usam o limite padrão de tokens. Digite um valor e o estúdio calcula quantos tokens cada pessoa pode gastar.</span>'; return; }
    const n = f.querySelectorAll('[name=member]:checked').length + msMembers.filter((m) => !f.querySelector(`[name=member][value="${m.user_id}"]`)?.checked).length;
    const split = f.budget_split.value === '1';
    const per = Math.floor((usd * rate) / (split ? Math.max(1, n) : 1));
    const usdFmt = (v) => `US$ ${v.toLocaleString(LOC, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    const perUsd = usd / (split ? Math.max(1, n) : 1);
    $('#budgetOut').innerHTML = `= <b>${per.toLocaleString(LOC)} tokens</b> <span class="usd">(${usdFmt(perUsd)})</span> por pessoa/mês${split ? ` <small>(US$ ${usd.toLocaleString(LOC)} ÷ ${Math.max(1, n)} membro(s))</small>` : n ? ` <small>· ${n} membro(s), total ${(per * n).toLocaleString(LOC)} tokens · ${usdFmt(usd * n)}</small>` : ''}`;
  };
  f.budget_usd.addEventListener('input', budget);
  f.budget_split.addEventListener('change', budget);
  f.querySelectorAll('[name=member]').forEach((c) => c.addEventListener('change', budget));
  budget();
}

// ---------------- Login Microsoft ----------------

async function loadSso() {
  const [c] = await Promise.all([api('/api/admin/microsoft'), ensureGroups()]);
  const chk = (k, label, help) => `<label class="perm"><input type="checkbox" name="${k}" ${c[k] ? 'checked' : ''}><span><b>${label}</b>${help ? `<small>${help}</small>` : ''}</span></label>`;
  $('#ssoPanel').innerHTML = `<form id="ssoForm">
    <div class="section-label"><span>Login com Microsoft (Entra ID)</span><span class="tag ${c.ready ? 'ok' : c.enabled ? 'err' : 'off'}">${c.ready ? 'ativo' : c.enabled ? 'incompleto' : 'desligado'}</span></div>
    ${c.missing.length ? `<div class="sso-missing">${ic('alert')}<span><b>${c.enabled ? 'O botão aparece no login, mas ainda não funciona.' : 'Para ligar o login com Microsoft'}</b> Falta preencher: ${c.missing.map((m) => `<b>${m}</b>`).join(', ')}. Eles vêm do registro do aplicativo no Azure (passo a passo abaixo).</span></div>` : ''}
    <details class="howto" ${c.client_id ? '' : 'open'}><summary>Como configurar no Azure (5 min)</summary><ol>
      <li data-i18n-html>Portal Azure → <b>Microsoft Entra ID</b> → <b>Registros de aplicativo</b> → <b>Novo registro</b>.</li>
      <li data-i18n-html>Tipo de conta: "somente este diretório" (recomendado). Em <b>URI de redirecionamento</b> escolha <b>Web</b> e cole o endereço abaixo.</li>
      <li data-i18n-html>Copie o <b>ID do aplicativo (cliente)</b> e o <b>ID do diretório (tenant)</b> para cá.</li>
      <li data-i18n-html><b>Certificados e segredos</b> → Novo segredo do cliente → copie o <b>Valor</b> para "Client secret".</li>
      <li data-i18n-html>Para usar grupos do Entra nas regras: <b>Configuração de token</b> → Adicionar declaração de grupos → "Grupos de segurança" (ID). Ou crie <b>App roles</b> e atribua em "Aplicativos empresariais".</li>
    </ol></details>
    <label>URI de redirecionamento (cole no Azure)<span class="copyrow"><input readonly value="${esc(c.redirect_uri)}" id="ssoRedirect"><button type="button" class="btn small ghost" id="ssoCopy">${ic('copy')} Copiar</button></span></label>
    <label>Endereço público do estúdio<input name="public_url" value="${esc(c.public_url)}" placeholder="vazio = PUBLIC_URL do servidor (ex.: https://studio.empresa.com)"></label>
    <div class="limits-grid">
      <label>Tenant (ID do diretório)<input name="tenant" value="${esc(c.tenant)}" placeholder="GUID do diretório ou organizations"></label>
      <label>Client ID (ID do aplicativo)<input name="client_id" value="${esc(c.client_id)}" placeholder="00000000-0000-0000-0000-000000000000"></label>
    </div>
    <label>Client secret <small>${c.has_secret ? `salvo cifrado: <span class="mono">${esc(c.secret_hint)}</span> · vazio = manter` : 'salvo cifrado, nunca volta para o navegador'}</small><input name="client_secret" type="password" autocomplete="new-password" placeholder="${c.has_secret ? '•••••••• (manter)' : 'Valor do segredo'}"></label>
    <label>Texto do botão<input name="button_label" value="${esc(c.button_label)}"></label>
    <div class="section-label"><span>Quem pode entrar</span></div>
    ${chk('enabled', 'Mostrar "Entrar com Microsoft" no login', '')}
    ${chk('allow_signup', 'Criar a conta automaticamente no 1º login', 'Desligado: só entra quem já tem conta aqui (vinculada pelo e-mail).')}
    <label>Domínios de e-mail permitidos <small>vazio = qualquer um do tenant</small><input name="allowed_domains" value="${esc((c.allowed_domains || []).join(', '))}" placeholder="empresa.com, empresa.com.br"></label>
    <div class="section-label"><span>Grupos</span></div>
    <label>Grupo para quem não bater em nenhuma regra<select name="default_group_id"><option value="">Nenhum (sem grupo = estúdio todo liberado)</option>${G.data.groups.map((g) => `<option value="${g.id}" ${c.default_group_id === g.id ? 'selected' : ''}>${esc(g.name)}</option>`).join('')}</select></label>
    ${chk('require_match', 'Bloquear quem não cair em nenhum grupo', 'Sem regra que bata, sem grupo padrão e sem grupo manual: o login é recusado.')}
    ${chk('graph_groups', 'Ler os grupos pelo Microsoft Graph', 'Para quem está em muitos grupos (mais de 200) ou se o token não traz grupos. Pede a permissão GroupMember.Read.All (precisa de consentimento do admin no Azure).')}
    <p class="note">As regras ficam em cada grupo (aba Grupos): domínio do e-mail, ID de grupo do Entra, app role ou tenant. Por segurança, contas que já existem só são vinculadas pelo e-mail quando o Tenant é um ID fixo.</p>
    <div class="dlg-actions"><button class="btn primary" type="submit">${ic('check')} Salvar</button>${c.has_secret ? '<button class="btn ghost" type="button" id="ssoClear">Apagar secret</button>' : ''}</div>
  </form>`;
  $('#ssoCopy').onclick = () => { navigator.clipboard?.writeText($('#ssoRedirect').value); toast('Copiado'); };
  const f = $('#ssoForm');
  const send = async (extra = {}) => {
    const body = { public_url: f.public_url.value, tenant: f.tenant.value, client_id: f.client_id.value, button_label: f.button_label.value,
      allowed_domains: f.allowed_domains.value, default_group_id: f.default_group_id.value || null,
      enabled: f.enabled.checked, allow_signup: f.allow_signup.checked, require_match: f.require_match.checked, graph_groups: f.graph_groups.checked, ...extra };
    if (f.client_secret.value) body.client_secret = f.client_secret.value;
    const r = await api('/api/admin/microsoft', body, 'PUT');
    toast(r.enabled && !r.ready ? `Salvo. Falta ${r.missing.join(', ')} para o login funcionar` : 'Login Microsoft salvo');
    loadSso();
  };
  f.onsubmit = (e) => { e.preventDefault(); send().catch((err) => toast(err.message)); };
  $('#ssoClear')?.addEventListener('click', () => { if (confirm('Apagar o client secret salvo? O login Microsoft para de funcionar.')) send({ clear_secret: true }).catch((err) => toast(err.message)); });
}
