// Efeitos do canvas, tudo em pixel:
//   · nó entra "materializando" (cresce em passos + faíscas)
//   · nó sai encolhendo e soltando estrelinhas de IA
//   · ligações são cordas com gravidade (verlet): penduram, balançam quando o nó é arrastado e assentam

const PX_COLORS = ['#a855f7', '#22d3ee', '#facc15', '#f472b6', '#ffffff', '#10b981'];
const reduceMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

// Estrela de 4 pontas em pixel (grade 7×7).
function pixelStar(color, size) {
  const px = [[3, 0], [3, 1], [2, 2], [3, 2], [4, 2], [0, 3], [1, 3], [2, 3], [3, 3], [4, 3], [5, 3], [6, 3], [2, 4], [3, 4], [4, 4], [3, 5], [3, 6]];
  return `<svg width="${size}" height="${size}" viewBox="0 0 7 7" shape-rendering="crispEdges">${px.map(([x, y]) => `<rect x="${x}" y="${y}" width="1" height="1" fill="${x === 3 && y === 3 ? '#fff' : color}"/>`).join('')}</svg>`;
}

// Explosão de estrelinhas em coordenadas do mundo do canvas.
function sparkleBurst(x, y, { count = 14, spread = 160, colors = PX_COLORS, big = false } = {}) {
  if (reduceMotion()) return;
  const world = $('#cvWorld');
  for (let i = 0; i < count; i++) {
    const s = document.createElement('div');
    s.className = 'px-spark';
    const ang = Math.random() * Math.PI * 2, dist = spread * (0.35 + Math.random() * 0.65);
    const size = (big ? 14 : 7) * (1 + Math.floor(Math.random() * 3));
    s.style.left = `${x}px`;
    s.style.top = `${y}px`;
    s.style.setProperty('--dx', `${Math.round(Math.cos(ang) * dist)}px`);
    s.style.setProperty('--dy', `${Math.round(Math.sin(ang) * dist - 30)}px`);
    s.style.setProperty('--d', `${380 + Math.random() * 420}ms`);
    s.style.setProperty('--delay', `${Math.random() * 120}ms`);
    s.innerHTML = pixelStar(colors[i % colors.length], size);
    world.appendChild(s);
    s.addEventListener('animationend', () => s.remove());
  }
}

function nodeCenter(n, el) {
  const w = el?.offsetWidth || 340, h = el?.offsetHeight || 300;
  return { x: n.x + w / 2, y: n.y + Math.min(h, 400) / 2 };
}

// ---------------- Entrada / saída de nós ----------------

const fx = { spawn: new Map() }; // id -> atraso (ms)

function markSpawn(id, delay = 0) { fx.spawn.set(id, delay); }

// Chamado ao criar o elemento do nó.
function applySpawn(el, n) {
  if (!fx.spawn.has(n.id) || reduceMotion()) return;
  const delay = fx.spawn.get(n.id);
  fx.spawn.delete(n.id);
  el.style.animationDelay = `${delay}ms`;
  el.classList.add('px-spawn');
  el.addEventListener('animationend', () => { el.classList.remove('px-spawn'); el.style.animationDelay = ''; }, { once: true });
  setTimeout(() => {
    const c = nodeCenter(n, el);
    sparkleBurst(c.x, c.y - 40, { count: 9, spread: 120, colors: ['#10b981', '#22d3ee', '#ffffff', '#facc15'] });
  }, delay + 60);
}

// Fecha o nó com estrelinhas e só então tira do grafo.
function animateRemove(n, done) {
  const el = document.querySelector(`.cv-node[data-id="${n.id}"]`);
  if (!el || reduceMotion()) return done();
  const c = nodeCenter(n, el);
  el.classList.add('px-despawn');
  el.style.pointerEvents = 'none';
  sparkleBurst(c.x, c.y, { count: 18, spread: 200, big: true });
  ropes.detach(n.id);
  setTimeout(done, 340);
}

// ---------------- Cordas com gravidade ----------------

const ROPE = { segs: 16, gravity: 0.6, damping: 0.94, iters: 12, slack: 1.1, extra: 26 };

const ropes = {
  map: new Map(), // id -> {pts:[{x,y,px,py}], color, el}
  running: false,
  calm: 0,

  // Corda nova: nasce reta entre as pontas e cai com a gravidade.
  make(a, b) {
    const pts = [];
    for (let i = 0; i <= ROPE.segs; i++) {
      const u = i / ROPE.segs;
      const x = a.x + (b.x - a.x) * u, y = a.y + (b.y - a.y) * u;
      pts.push({ x, y, px: x, py: y - 2 });
    }
    return { pts };
  },

  // Nó sendo removido: as pontas soltas caem.
  detach(nodeId) {
    for (const e of cv.edges.filter((x) => x.from === nodeId || x.to === nodeId)) {
      const r = this.map.get(e.id);
      if (r) { r.loose = e.from === nodeId ? 'a' : 'b'; r.fall = 0; }
    }
    this.wake();
  },

  wake() {
    this.calm = 0;
    if (this.running) return;
    this.running = true;
    requestAnimationFrame(() => this.tick());
  },

  endpoints(e) {
    const a = byId(e.from), b = byId(e.to);
    if (!a || !b) return null;
    return [portPos(a, 'out'), portPos(b, 'in', e.port)];
  },

  step(r, A, B) {
    const p = r.pts;
    const n = p.length - 1;
    const dist = Math.hypot(B.x - A.x, B.y - A.y);
    const rest = (dist * ROPE.slack + ROPE.extra) / ROPE.segs;
    let motion = 0;
    for (let i = 0; i <= n; i++) {
      const q = p[i];
      const pinned = (i === 0 && r.loose !== 'a') || (i === n && r.loose !== 'b');
      if (pinned) continue;
      const vx = (q.x - q.px) * ROPE.damping, vy = (q.y - q.py) * ROPE.damping;
      q.px = q.x; q.py = q.y;
      q.x += vx; q.y += vy + ROPE.gravity;
      motion += Math.abs(vx) + Math.abs(vy);
    }
    if (r.loose !== 'a') { p[0].x = A.x; p[0].y = A.y; }
    if (r.loose !== 'b') { p[n].x = B.x; p[n].y = B.y; }
    for (let k = 0; k < ROPE.iters; k++) {
      for (let i = 0; i < n; i++) {
        const a = p[i], b = p[i + 1];
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.hypot(dx, dy) || 0.0001;
        const diff = (d - rest) / d / 2;
        const aPin = i === 0 && r.loose !== 'a', bPin = i + 1 === n && r.loose !== 'b';
        if (!aPin) { a.x += dx * diff * (bPin ? 2 : 1); a.y += dy * diff * (bPin ? 2 : 1); }
        if (!bPin) { b.x -= dx * diff * (aPin ? 2 : 1); b.y -= dy * diff * (aPin ? 2 : 1); }
      }
      if (r.loose !== 'a') { p[0].x = A.x; p[0].y = A.y; }
      if (r.loose !== 'b') { p[n].x = B.x; p[n].y = B.y; }
    }
    // movimento real do quadro (depois das restrições): em repouso fica ~0
    motion = 0;
    p.forEach((q, i) => {
      const pinned = (i === 0 && r.loose !== 'a') || (i === n && r.loose !== 'b');
      if (pinned) { q.px = q.x; q.py = q.y; } else motion += Math.abs(q.x - q.px) + Math.abs(q.y - q.py);
    });
    return motion;
  },

  // Desenha como polilinha "pixelada" (crispEdges, pontos arredondados para a grade de 2px).
  pathOf(r) {
    const g = (v) => Math.round(v / 2) * 2;
    return r.pts.map((q, i) => `${i ? 'L' : 'M'}${g(q.x)},${g(q.y)}`).join(' ');
  },

  colorOf(e) {
    const from = byId(e.from), to = byId(e.to);
    if (from?.type === 'cast') return '#fb923c';
    if (e.port === 'ref') return '#a78bfa';
    if (to?.type === 'transition') return '#f59e0b';
    if (to?.type === 'montage') return '#fbbf24';
    return '#10b981';
  },

  // Recria os <path> quando o conjunto de ligações muda; mantém a física das que continuam.
  sync() {
    const svg = $('#cvEdges');
    const ids = new Set(cv.edges.map((e) => e.id));
    for (const [id, r] of this.map) if (!ids.has(id) && id !== 'temp' && !r.loose) { r.el?.remove(); this.map.delete(id); }
    for (const e of cv.edges) {
      let r = this.map.get(e.id);
      const ends = this.endpoints(e);
      if (!ends) continue;
      if (!r) {
        r = this.make(...ends);
        this.map.set(e.id, r);
        r.el = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        r.el.dataset.id = e.id;
        r.el.innerHTML = '<title>Clique: inserir transição ou remover</title>';
        r.el.addEventListener('click', (ev) => edgeMenu(cv.edges.find((x) => x.id === e.id), ev));
        svg.appendChild(r.el);
      }
      r.el.style.stroke = this.colorOf(e);
    }
    // ligação sendo puxada com o mouse
    let t = this.map.get('temp');
    if (cv.link) {
      if (!t) {
        t = this.make(cv.link.a, cv.link.b);
        t.el = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        t.el.classList.add('temp');
        svg.appendChild(t.el);
        this.map.set('temp', t);
      }
    } else if (t) { t.el.remove(); this.map.delete('temp'); }
    this.wake();
  },

  tick() {
    let motion = 0;
    for (const [id, r] of this.map) {
      let ends;
      if (id === 'temp') ends = cv.link ? [cv.link.a, cv.link.b] : null;
      else {
        const e = cv.edges.find((x) => x.id === id);
        ends = e ? this.endpoints(e) : r.lastEnds;
      }
      if (!ends) continue;
      r.lastEnds = ends;
      motion += this.step(r, ends[0], ends[1]);
      r.el.setAttribute('d', this.pathOf(r));
      // pontas soltas que já caíram longe somem
      if (r.loose && ++r.fall > 40) { r.el.remove(); this.map.delete(id); }
    }
    this.calm = motion < 0.15 * Math.max(1, this.map.size) ? this.calm + 1 : 0;
    if (this.calm > 45) { this.running = false; return; }
    requestAnimationFrame(() => this.tick());
  },

  reset() {
    for (const r of this.map.values()) r.el?.remove();
    this.map.clear();
  },
};
