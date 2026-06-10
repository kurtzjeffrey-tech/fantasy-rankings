(function () {
  'use strict';

  const POSITIONS = ['RB', 'WR', 'QB', 'TE', 'K', 'DST'];
  const cache = {};

  let currentPos = 'RB';
  let currentData = [];
  let currentSort = 'rank';
  let searchQuery = '';

  // ── Elements ──────────────────────────────────────────
  const tableBody    = document.getElementById('tableBody');
  const loadingState = document.getElementById('loadingState');
  const errorState   = document.getElementById('errorState');
  const noResults    = document.getElementById('noResults');
  const lastUpdated  = document.getElementById('lastUpdated');
  const searchInput  = document.getElementById('searchInput');
  const sortSelect   = document.getElementById('sortSelect');
  const newsDrawer   = document.getElementById('newsDrawer');
  const drawerPanel  = document.getElementById('drawerPanel');
  const drawerClose  = document.getElementById('drawerClose');
  const drawerBackdrop = document.getElementById('drawerBackdrop');
  const drawerHeadshot = document.getElementById('drawerHeadshot');
  const drawerPlayerName = document.getElementById('drawerPlayerName');
  const drawerPlayerSub  = document.getElementById('drawerPlayerSub');
  const drawerStats  = document.getElementById('drawerStats');
  const drawerNews   = document.getElementById('drawerNews');

  // ── Fetch helpers ─────────────────────────────────────

  async function loadMeta() {
    try {
      const r = await fetch('data/meta.json');
      if (!r.ok) return;
      const meta = await r.json();
      if (meta.last_updated) {
        const d = new Date(meta.last_updated);
        lastUpdated.textContent = d.toLocaleString('en-US', {
          month: 'short', day: 'numeric',
          hour: 'numeric', minute: '2-digit', timeZoneName: 'short'
        });
      }
    } catch (_) {}
  }

  async function loadPosition(pos) {
    if (cache[pos]) return cache[pos];
    const r = await fetch(`data/${pos.toLowerCase()}.json`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    cache[pos] = data;
    return data;
  }

  // ── Render ────────────────────────────────────────────

  function applyFiltersAndSort(data) {
    let rows = data.slice();

    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      rows = rows.filter(p =>
        p.name.toLowerCase().includes(q) ||
        (p.team || '').toLowerCase().includes(q)
      );
    }

    if (currentSort === 'adp') {
      rows.sort((a, b) => {
        const av = parseFloat(a.adp?.overall) || 999;
        const bv = parseFloat(b.adp?.overall) || 999;
        return av - bv;
      });
    } else if (currentSort === 'name') {
      rows.sort((a, b) => a.name.localeCompare(b.name));
    } else {
      rows.sort((a, b) => (a.rank || 999) - (b.rank || 999));
    }

    return rows;
  }

  function fmtDelta(val) {
    if (val == null || val === '' || isNaN(val)) return null;
    const n = parseFloat(val);
    if (Math.abs(n) < 0.5) return null;
    return n;
  }

  function injuryClass(status) {
    if (!status) return null;
    const s = status.toLowerCase();
    if (s.includes('out'))         return 'out';
    if (s.includes('doubtful'))    return 'doubtful';
    if (s.includes('questionable')) return 'questionable';
    if (s.includes('ir') || s.includes('injured reserve')) return 'ir';
    if (s.includes('pup'))         return 'pup';
    if (s.includes('limited'))     return 'limited';
    return null;
  }

  function fmtTime(ts) {
    if (!ts) return '';
    try {
      const d = new Date(ts);
      return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    } catch (_) { return ts; }
  }

  function buildRow(player, rank) {
    const tr = document.createElement('tr');

    const rankTop = rank <= 5;
    const rc = player.rank_change;
    const trendHTML = (rc != null && rc !== 0)
      ? `<span class="trend-${rc > 0 ? 'up' : 'down'}">${rc > 0 ? '▲' : '▼'}${Math.abs(rc)}</span>`
      : '';
    const inj = injuryClass(player.injury_status);
    const adpVal = player.adp?.overall ? parseFloat(player.adp.overall).toFixed(1) : '—';
    const ecrAvg = player.ecr?.avg ? parseFloat(player.ecr.avg).toFixed(1) : '—';
    const ecr_vs_adp = fmtDelta(player.ecr?.ecr_vs_adp);
    const newsCount = (player.news || []).length;

    let deltaHTML = '<span class="delta neu">—</span>';
    if (ecr_vs_adp !== null) {
      const cls = ecr_vs_adp > 0 ? 'pos' : 'neg';
      const sign = ecr_vs_adp > 0 ? '+' : '';
      deltaHTML = `<span class="delta ${cls}">${sign}${ecr_vs_adp.toFixed(1)}</span>`;
    }

    let injHTML = '';
    if (inj) {
      injHTML = `<span class="injury-badge ${inj}">${player.injury_status}</span>`;
    }

    tr.innerHTML = `
      <td class="col-rank"><div class="rank-cell-inner"><span class="rank-num${rankTop ? ' rank-top' : ''}">${rank}</span>${trendHTML}</div></td>
      <td class="col-player">
        <div class="player-cell">
          <img class="player-headshot" src="${player.headshot_url || ''}" alt="" loading="lazy" />
          <div class="player-info">
            <div class="player-name">${escHtml(player.name)}</div>
            <div class="player-meta">
              <span class="player-team">${escHtml(player.team || 'FA')}</span>
              ${injHTML}
            </div>
          </div>
        </div>
      </td>
      <td class="col-bye"><span class="bye-num">${player.bye || '—'}</span></td>
      <td class="col-ecr"><span class="ecr-val">${ecrAvg}</span></td>
      <td class="col-adp"><span class="adp-val">${adpVal}</span></td>
      <td class="col-adp-sites">${deltaHTML}</td>
      <td class="col-news">
        <button class="news-btn" data-player-idx="${rank - 1}" aria-label="View news for ${escHtml(player.name)}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 10h16M4 14h10"/></svg>
          <span class="news-count">${newsCount}</span>
        </button>
      </td>
    `;

    // Headshot fallback
    const img = tr.querySelector('.player-headshot');
    img.addEventListener('error', () => img.classList.add('error'));

    return tr;
  }

  function render(data) {
    const rows = applyFiltersAndSort(data);

    tableBody.innerHTML = '';
    noResults.classList.toggle('hidden', rows.length > 0);

    const fragment = document.createDocumentFragment();
    rows.forEach((player, i) => {
      fragment.appendChild(buildRow(player, i + 1));
    });
    tableBody.appendChild(fragment);

    // Wire news buttons (using event delegation on tbody for performance)
    tableBody.addEventListener('click', onTableClick, { once: false });
  }

  let delegated = false;
  function setupDelegation() {
    if (delegated) return;
    delegated = true;
    tableBody.addEventListener('click', onTableClick);
  }

  function onTableClick(e) {
    const btn = e.target.closest('.news-btn');
    if (!btn) return;
    const idx = parseInt(btn.dataset.playerIdx, 10);
    const rows = applyFiltersAndSort(currentData);
    if (rows[idx]) openDrawer(rows[idx]);
  }

  // ── Drawer ────────────────────────────────────────────

  function openDrawer(player) {
    drawerHeadshot.src = player.headshot_url || '';
    drawerHeadshot.alt = player.name;
    drawerPlayerName.textContent = player.name;
    drawerPlayerSub.textContent = `${player.team || 'FA'} · ${player.position}${player.bye ? ' · Bye ' + player.bye : ''}`;

    // Stats strip
    const adpVal = player.adp?.overall ? parseFloat(player.adp.overall).toFixed(1) : '—';
    const ecrVal = player.ecr?.avg ? parseFloat(player.ecr.avg).toFixed(1) : '—';
    drawerStats.innerHTML = `
      <div class="stat-cell"><div class="stat-label">Rank</div><div class="stat-value rank-val">#${player.rank || '—'}</div></div>
      <div class="stat-cell"><div class="stat-label">ECR avg</div><div class="stat-value">${ecrVal}</div></div>
      <div class="stat-cell"><div class="stat-label">ADP</div><div class="stat-value">${adpVal}</div></div>
    `;

    // ADP breakdown row
    const adp = player.adp || {};
    const adpSites = [
      ['ESPN', adp.espn], ['Yahoo', adp.yahoo], ['NFL', adp.nfl], ['CBS', adp.cbs], ['Sleeper', adp.sleeper]
    ].filter(([, v]) => v && v !== '—');

    if (adpSites.length) {
      const siteGrid = adpSites.map(([site, val]) => `
        <div class="stat-cell">
          <div class="stat-label">${site}</div>
          <div class="stat-value" style="font-size:15px">${parseFloat(val).toFixed(1)}</div>
        </div>`).join('');
      drawerStats.innerHTML += siteGrid;
      drawerStats.style.gridTemplateColumns = `repeat(${3 + adpSites.length}, 1fr)`;
    } else {
      drawerStats.style.gridTemplateColumns = 'repeat(3, 1fr)';
    }

    // News
    const news = player.news || [];
    if (!news.length) {
      drawerNews.innerHTML = '<p class="drawer-no-news">No recent news for this player.</p>';
    } else {
      drawerNews.innerHTML = news.map(item => {
        const url = item.url || '#';
        const headline = item.headline || item.title || 'News';
        const desc = item.description || item.story || item.blurb || '';
        const ts = fmtTime(item.timestamp || item.published);
        return `
          <div class="news-item">
            <div class="news-headline">
              <span class="news-source-tag">${item.source || 'news'}</span>
              ${url && url !== '#' ? `<a href="${escHtml(url)}" target="_blank" rel="noopener">${escHtml(headline)}</a>` : escHtml(headline)}
            </div>
            ${desc ? `<p class="news-description">${escHtml(desc)}</p>` : ''}
            ${ts ? `<p class="news-time">${ts}</p>` : ''}
          </div>`;
      }).join('');
    }

    newsDrawer.classList.add('open');
    newsDrawer.setAttribute('aria-hidden', 'false');
    drawerClose.focus();
  }

  function closeDrawer() {
    newsDrawer.classList.remove('open');
    newsDrawer.setAttribute('aria-hidden', 'true');
  }

  drawerClose.addEventListener('click', closeDrawer);
  drawerBackdrop.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeDrawer();
  });

  // ── Position switching ────────────────────────────────

  async function switchPosition(pos) {
    currentPos = pos;
    searchInput.value = '';
    searchQuery = '';

    document.querySelectorAll('.pos-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.pos === pos);
    });

    loadingState.classList.remove('hidden');
    errorState.classList.add('hidden');
    noResults.classList.add('hidden');
    tableBody.innerHTML = '';

    try {
      const data = await loadPosition(pos);
      currentData = data;
      loadingState.classList.add('hidden');
      setupDelegation();
      render(data);
    } catch (err) {
      loadingState.classList.add('hidden');
      errorState.classList.remove('hidden');
      console.error('Failed to load', pos, err);
    }
  }

  // ── Event listeners ───────────────────────────────────

  document.querySelectorAll('.pos-tab').forEach(tab => {
    tab.addEventListener('click', () => switchPosition(tab.dataset.pos));
  });

  searchInput.addEventListener('input', () => {
    searchQuery = searchInput.value.trim();
    render(currentData);
  });

  sortSelect.addEventListener('change', () => {
    currentSort = sortSelect.value;
    render(currentData);
  });

  // ── Utilities ─────────────────────────────────────────

  function escHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ── Init ──────────────────────────────────────────────

  loadMeta();
  switchPosition(currentPos);
})();
