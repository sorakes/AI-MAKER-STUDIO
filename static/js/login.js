    const ls = document.getElementById('lang');
    ls.innerHTML = Object.entries(I18N.LANGS).map(([k, v]) => `<option value="${k}" ${k === I18N.lang ? 'selected' : ''}>${v}</option>`).join('');
    ls.onchange = () => I18N.setLang(ls.value);
    fetch('/api/public').then((r) => r.json()).then((d) => {
      if (d.default_lang) I18N.setDefault(d.default_lang);
      document.getElementById('appName').textContent = d.app_name;
      document.title = `${t('Entrar')} · ${d.app_name}`;
      const b = d.branding || {};
      const root = document.documentElement.style;
      if (b.colors?.accent) root.setProperty('--accent', b.colors.accent);
      if (b.colors?.bg) root.setProperty('--bg', b.colors.bg);
      if (b.tagline) document.querySelector('p.sub').textContent = b.tagline;
      if (b.logo_url) document.querySelector('.logo > span').innerHTML = `<img src="${b.logo_url}" alt="" data-st="height:28px;max-width:120px;object-fit:contain;image-rendering:auto">`;
      document.body.classList.toggle('pixel', b.style !== 'smooth');
      if (d.microsoft?.enabled) { document.body.classList.add('has-ms'); document.getElementById('msLabel').textContent = d.microsoft.label; }
    }).catch(() => {});
    const qErr = new URLSearchParams(location.search).get('error');
    if (qErr) { document.getElementById('err').textContent = t(qErr); history.replaceState(null, '', '/login'); }
    const f = document.getElementById('form');
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = document.getElementById('btn');
      btn.disabled = true;
      document.getElementById('err').textContent = '';
      try {
        const r = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: document.getElementById('email').value, password: document.getElementById('password').value }) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || t('Falha no login'));
        location.href = '/';
      } catch (err) {
        document.getElementById('err').textContent = err.message;
        btn.disabled = false;
      }
    });
