'use strict';

const LANES = [
  { id: 1, name: '对抗路' },
  { id: 2, name: '中路' },
  { id: 3, name: '发育路' },
  { id: 4, name: '打野' },
  { id: 5, name: '游走' },
];

let view = null;              // 服务端返回的完整视图
let heroMap = new Map();      // hero_id -> hero
let laneFilter = 0;           // 0 = 全部
let searchText = '';
let meta = null;

const $ = (id) => document.getElementById(id);

// --------------------------------------------------------------------- API
async function api(path, body) {
  const opt = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {};
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({ ok: false, error: '响应不是 JSON' }));
  if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function flash(msg, isErr) {
  const b = $('banner');
  b.textContent = msg;
  b.classList.remove('hidden');
  b.style.color = isErr ? '#ff9a9d' : '';
  b.style.borderColor = isErr ? 'rgba(255,90,95,.4)' : '';
  clearTimeout(flash._t);
  flash._t = setTimeout(() => b.classList.add('hidden'), 3600);
}

// ---------------------------------------------------------------- meta/init
async function loadMeta() {
  meta = await api('/api/meta');
  $('seasonTag').textContent = meta.season || '无数据';
  $('seasonSel').innerHTML = meta.seasons
    .map(
      (s) =>
        `<option value="${s.seasonid}" ${s.seasonid === meta.season ? 'selected' : ''}>${
          s.name || s.seasonid
        }</option>`
    )
    .join('');
  const opts = meta.teams
    .map((t) => `<option value="${t.team_id}">${t.name}</option>`)
    .join('');
  $('blueSel').innerHTML = opts;
  $('redSel').innerHTML = opts;
  if (meta.teams.length > 1) $('redSel').selectedIndex = 1;

  await loadMatches('official');
  await loadBanBoard();
}

async function startDrill(scheduleId, round) {
  try {
    const v = await api('/api/drill/start', { schedule_id: scheduleId, round });
    view = v;
    heroMap = new Map(v.heroes.map((h) => [h.hero_id, h]));
    localStorage.setItem('kplbp.sid', v.id);
    render();
    flash(`复盘开始：真实第 ${round} 局，共 ${v.drill.total} 手`);
  } catch (e) {
    flash(e.message, true);
  }
}

function exitDrill() {
  localStorage.removeItem('kplbp.sid');
  view = null;
  $('drillBar').classList.add('hidden');
  $('turnText').textContent = '未开始';
  $('stepText').textContent = '';
  ['blueBans', 'bluePicks', 'redBans', 'redPicks', 'blueCover', 'redCover',
   'timeline', 'historyList', 'suggestList'].forEach((id) => {
    const el = $(id);
    if (el) el.innerHTML = '';
  });
  $('heroGrid').innerHTML =
    '<span class="hint">选好双方点「新建比赛」，或在「对局数据」里点某局的「复盘」</span>';
}

function renderDrillBar() {
  const bar = $('drillBar');
  if (!view || view.kind !== 'drill' || !view.drill) {
    bar.classList.add('hidden');
    return;
  }
  const d = view.drill;
  const s = view.summary;
  bar.classList.remove('hidden');
  const turnTxt = s.match_finished || !s.turn
    ? '已走完'
    : `轮到 ${s.turn.side === 'B' ? '蓝方' : '红方'} ${s.turn.action === 'ban' ? 'BAN' : 'PICK'}`;
  bar.innerHTML = `<span>复盘 · ${view.team_names.B} vs ${view.team_names.R} · 第 ${d.round} 局</span>
    <span>命中 <b>${d.hits}</b> / ${d.cursor}（共 ${d.total} 手）</span>
    <span>${turnTxt}</span>
    <button id="btnDrillExit">退出复盘</button>`;
  $('btnDrillExit').onclick = exitDrill;
}

let realSource = 'official';


async function changeSeason(sid) {
  try {
    await api('/api/season', { season: sid });
    await loadMeta();
    if (view) {
      try {
        view = await api(`/api/bp?id=${view.id}`);
        render();
      } catch {
        view = null;
      }
    }
    flash(`已切换到 ${sid}`);
  } catch (e) {
    flash(e.message, true);
  }
}

async function loadMatches(src) {
  realSource = src;
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('on', t.dataset.src === src));
  const extra = src === 'official' ? '&only_played=1' : '';
  const rows = await api(`/api/matches?limit=40&source=${src}${extra}`);
  renderMatchList(rows.matches);
  $('matchDetail').innerHTML =
    src === 'sim' ? '<span class="hint">点左边任意一场，看那局的每小局 ban / pick</span>' : '';
}

async function loadBanBoard() {
  const d = await api('/api/bans');
  const max = Math.max(1, ...d.bans.map((b) => b.bans));
  $('banBoard').innerHTML = d.bans.length
    ? d.bans
        .map(
          (b) => `<div class="ban-item">
          <span>${b.cname}</span>
          <span class="bn">${b.bans}</span>
          <span class="ban-bar"><i style="width:${(b.bans / max) * 100}%"></i></span>
        </div>`
        )
        .join('')
    : '<span class="hint">还没有 ban 数据 —— 打完一局点「保存本局」，这张榜就活了</span>';
}

async function saveGame() {
  if (!view) return flash('先新建一场比赛', true);
  try {
    const v = await api(`/api/bp/${view.id}/save`, {});
    view = v;
    render();
    flash(`已写入 ${v.saved}，ban 已进榜`);
    await loadBanBoard();
    await loadMatches('sim');
  } catch (e) {
    flash(e.message, true);
  }
}

async function newMatch() {
  const bo = Number($('boSel').value);
  const blue = $('blueSel').value;
  const red = $('redSel').value;
  if (blue && red && blue === red) return flash('蓝红双方不能是同一支队', true);
  try {
    const v = await api('/api/bp/new', {
      bo,
      blue,
      red,
      config: { global_bp: $('globalBp').checked },
    });
    view = v;
    localStorage.setItem('kplbp.sid', v.id);
    heroMap = new Map(v.heroes.map((h) => [h.hero_id, h]));
    render();
    flash(`新比赛：BO${bo} · ${v.team_names.B} vs ${v.team_names.R}`);
  } catch (e) {
    flash(e.message, true);
  }
}

async function restore() {
  const sid = localStorage.getItem('kplbp.sid');
  if (!sid) return false;
  try {
    const v = await api(`/api/bp?id=${sid}`);
    view = v;
    heroMap = new Map(v.heroes.map((h) => [h.hero_id, h]));
    render();
    return true;
  } catch {
    return false;
  }
}

// ----------------------------------------------------------------- actions
async function act(heroId) {
  if (!view) return flash('先新建一场比赛', true);
  try {
    const v = await api(`/api/bp/${view.id}/act`, { hero_id: heroId });
    view = v;
    render();
  } catch (e) {
    flash(e.message, true);
  }
}

async function undo() {
  if (!view) return;
  try {
    view = await api(`/api/bp/${view.id}/undo`, {});
    render();
  } catch (e) {
    flash(e.message, true);
  }
}

async function winner(side) {
  if (!view) return;
  try {
    view = await api(`/api/bp/${view.id}/winner`, { side });
    render();
    const s = view.summary;
    if (s.match_finished) {
      const champ = s.score.B > s.score.R ? view.team_names.B : view.team_names.R;
      flash(`比赛结束 ${s.score.B} : ${s.score.R}，${champ} 获胜`);
    }
  } catch (e) {
    flash(e.message, true);
  }
}

// ------------------------------------------------------------------ render
function heroIcon(id) {
  return `https://game.gtimg.cn/images/yxzj/img201606/heroimg/${id}/${id}.jpg`;
}

function render() {
  if (!view) return;
  const s = view.summary;

  $('blueName').textContent = view.team_names.B || '蓝方';
  $('redName').textContent = view.team_names.R || '红方';
  $('blueScore').textContent = s.score.B;
  $('redScore').textContent = s.score.R;

  // 当前轮次
  if (s.match_finished) {
    $('turnText').textContent = '比赛已结束';
    $('stepText').textContent = `${s.score.B} : ${s.score.R}`;
  } else if (s.turn) {
    const who = s.turn.side === 'B' ? '蓝方' : '红方';
    const what = s.turn.action === 'ban' ? 'BAN' : 'PICK';
    $('turnText').innerHTML =
      `<span style="color:${s.turn.side === 'B' ? 'var(--blue)' : 'var(--red)'}">${who}</span> 第 ${s.step + 1}/${s.total_steps} 手 · ${what}` +
      (s.blind ? ' <span style="color:var(--gold)">（巅峰对决·盲选）</span>' : '');
    $('stepText').textContent = `第 ${s.game} 小局 · 全局已锁 ${s.locked_count} 个英雄`;
  } else {
    $('turnText').textContent = `第 ${s.game} 小局 BP 完成，请记录胜方`;
    $('stepText').textContent = `${s.score.B} : ${s.score.R}`;
  }

  renderTimeline(s);
  renderSide('B', s);
  renderSide('R', s);
  renderGrid();
  renderDrillBar();
  renderSuggest();
  renderHistory(s);
}

function renderTimeline(s) {
  if (!s.step_table) {
    $('timeline').innerHTML = '';
    return;
  }
  const el = $('timeline');
  el.innerHTML = s.step_table
    .map((a, i) => {
      const cls = [a.side === 'B' ? 'blue' : 'red'];
      if (i < s.step) cls.push('done');
      if (i === s.step) cls.push('now');
      return `<div class="tl ${cls.join(' ')}" title="${a.side === 'B' ? '蓝' : '红'} ${a.action}">${a.action === 'ban' ? 'B' : 'P'}</div>`;
    })
    .join('');
}

function renderSide(side, s) {
  const g = s.games[s.game - 1];
  if (!g) return;
  const prefix = side === 'B' ? 'blue' : 'red';
  const bans = g.bans[side].map((id) => chipFor(id));
  const picks = g.picks[side].map((id) => chipFor(id));
  $(prefix + 'Bans').innerHTML = bans.join('') || '<span class="hint">—</span>';
  $(prefix + 'Picks').innerHTML = picks.join('') || '<span class="hint">—</span>';

  const cov = view.coverage[side];
  $(prefix + 'Cover').innerHTML = LANES.map((ln) => {
    const hit = cov.covered.find((c) => c.lane === ln.id);
    return `<span class="lane ${hit ? 'hit' : ''}" title="${hit ? hit.heroes.join('/') : '缺'}">${ln.name}</span>`;
  }).join('');
}

function chipFor(id) {
  const h = heroMap.get(id);
  const name = h ? h.cname : id;
  return `<div class="chip"><img src="${heroIcon(id)}" onerror="this.style.display='none'">${name}</div>`;
}

function renderGrid() {
  if (!view) {
    $('heroGrid').innerHTML = '';
    return;
  }
  const suggestTop = new Set(view.suggest.slice(0, 3).map((x) => x.hero_id));
  const q = searchText.trim().toLowerCase();

  const list = view.heroes.filter((h) => {
    if (laneFilter && h.lane !== laneFilter) return false;
    if (!q) return true;
    return h.cname.toLowerCase().includes(q) || String(h.hero_id) === q;
  });

  $('heroGrid').innerHTML = list
    .map((h) => {
      const wr = h.pick_wr != null ? Math.round(h.pick_wr * 100) + '%' : h.rank_wr != null ? Math.round(h.rank_wr * 100) + '%' : '';
      const star = suggestTop.has(h.hero_id) ? ' suggest1' : '';
      return `<div class="hero ${h.status}${star}" data-id="${h.hero_id}" title="${h.cname} · ${h.title || ''}">
        <div class="avat"><img src="${heroIcon(h.hero_id)}" loading="lazy" onerror="this.remove()"></div>
        <div class="fallback">${h.cname.slice(0, 1)}</div>
        ${wr ? `<div class="wr">${wr}</div>` : ''}
        <div class="name">${h.cname}</div>
      </div>`;
    })
    .join('');
}

function renderSuggest() {
  if (view.kind === 'drill' && view.drill) {
    renderDrillPanel();
    return;
  }
  const s = view.summary;
  const hint = $('suggestHint');
  if (!s.turn) {
    hint.textContent = '本小局 BP 结束';
    $('suggestList').innerHTML = '<span class="hint">记录胜方后进入下一小局</span>';
    return;
  }
  hint.textContent = s.turn.action === 'ban' ? '建议禁用的英雄' : '建议选用的英雄';
  $('suggestList').innerHTML = view.suggest
    .map(
      (x) => `<div class="sg" data-id="${x.hero_id}">
      <div class="sg-top">
        <span class="sg-name">${x.cname}</span>
        <span class="sg-score">${(x.score * 100).toFixed(0)}</span>
      </div>
      <div class="sg-bar"><i style="width:${Math.max(4, x.score * 100)}%"></i></div>
      <div class="sg-why">${x.reasons.map((r) => `<span>${r}</span>`).join('') || '<span>数据不足</span>'}</div>
    </div>`
    )
    .join('');
}

function renderDrillPanel() {
  const d = view.drill;
  const s = view.summary;
  $('suggestHint').textContent = `职业队命中率 ${d.cursor ? Math.round((d.hits / d.cursor) * 100) : 0}%`;
  const rows = d.results
    .slice()
    .reverse()
    .map(
      (r) => `<div class="sg" style="cursor:default">
      <div class="sg-top">
        <span class="sg-name">#${r.seq} ${r.hit ? '<span style="color:var(--green)">✓ 对</span>' : '<span style="color:var(--red)">✗ 错</span>'}</span>
      </div>
      <div class="sg-why">
        <span>你：${r.mine}</span>
        <span>职业：${r.truth}</span>
      </div>
    </div>`
    )
    .join('');
  $('suggestList').innerHTML =
    rows || `<span class="hint">照真实顺序走第一手${s.turn ? `（${s.turn.side === 'B' ? '蓝' : '红'} ${s.turn.action}）` : ''}</span>`;
}

function renderHistory(s) {
  const rows = s.games
    .filter((g) => g.winner || g.bans.B.length || g.picks.B.length)
    .map((g) => {
      const w = g.winner === 'B' ? view.team_names.B : g.winner === 'R' ? view.team_names.R : '—';
      const fmt = (arr) => arr.map((id) => (heroMap.get(id) || {}).cname || id).join(' / ') || '—';
      return `<div class="hrow">
        <div class="r">第 ${g.round} 局${g.blind ? ' · 盲选' : ''}</div>
        <div class="b">蓝 ban: ${fmt(g.bans.B)}<br>蓝 pick: ${fmt(g.picks.B)}</div>
        <div class="rr">红 ban: ${fmt(g.bans.R)}<br>红 pick: ${fmt(g.picks.R)}</div>
        <div class="w">${g.winner ? '胜：' + w : ''}</div>
      </div>`;
    });
  $('historyList').innerHTML = rows.join('') || '<span class="hint">还没有完成的小局</span>';

  const canPick = s.game_finished && !s.match_finished;
  $('winBtns').innerHTML = canPick
    ? `<button data-win="B" class="primary">${view.team_names.B} 赢</button>
       <button data-win="R" class="primary">${view.team_names.R} 赢</button>`
    : '';
  $('btnSave').textContent = view.saved ? '重新保存（覆盖 ban 库）' : '保存本局 → 写入 ban 库';
}

// ------------------------------------------------------------- real matches
function renderMatchList(matches) {
  $('matchList').innerHTML =
    matches
      .map(
        (m) => `<div class="mrow" data-id="${m.schedule_id}">
        <span>${m.team_a_name || m.team_a} ${m.score_a}:${m.score_b} ${m.team_b_name || m.team_b}</span>
        <span class="hint">BO${m.bo_total} · ${m.rounds}局</span>
      </div>`
      )
      .join('') || '<span class="hint">没有已完成的比赛</span>';
}

async function openMatch(id) {
  document.querySelectorAll('.mrow').forEach((n) => n.classList.toggle('on', n.dataset.id === id));
  try {
    const d = await api(`/api/match/${id}`);
    const m = d.match;
    const html = [`<div class="hint" style="margin-bottom:6px">${m.stage_name} · ${m.format} · ${new Date(m.start_ts * 1000).toLocaleDateString('zh-CN')}</div>`];
    for (const r of d.rounds) {
      const blue = r.picks.filter((p) => p.team_id === m.team_a);
      const red = r.picks.filter((p) => p.team_id === m.team_b);
      const side = (arr, cls) =>
        `<div class="gline">${arr
          .map((p) => `<span class="hpill ${p.team_id === r.win_team ? 'win' : ''}">${p.hero_name}</span>`)
          .join('')}</div>`;
      html.push(`<div class="gbox">
        <h4>第 ${r.round} 局 · 胜方 ${r.win_team_name}
          <button class="mini" data-drill="${m.schedule_id}" data-round="${r.round}">复盘这局</button>
        </h4>
        <div style="font-size:11px;color:var(--muted)">${m.team_a_name}</div>
        ${side(blue)}
        <div style="font-size:11px;color:var(--muted);margin-top:4px">${m.team_b_name}</div>
        ${side(red)}
      </div>`);
    }
    $('matchDetail').innerHTML = html.join('');
  } catch (e) {
    flash(e.message, true);
  }
}

// ------------------------------------------------------------ hero modal
async function openHeroModal(id) {
  try {
    const d = await api(`/api/hero/${id}`);
    const h = d.hero;
    const pct = (v) => (v == null ? '—' : Math.round(v * 100) + '%');
    const wr = h.pick_wr != null ? h.pick_wr : h.rank_wr;

    const pills = (arr, cls) =>
      arr.length
        ? `<div class="crow">${arr
            .map((x) => `<span class="cpill ${cls}">${x.cname} ${(x.wr * 100).toFixed(0)}% (${x.games})</span>`)
            .join('')}</div>`
        : '<span class="hint">样本不足</span>';

    const players = d.players.length
      ? `<div class="crow">${d.players
          .map(
            (p) =>
              `<span class="cpill">${p.name || p.playerid} ${p.games}场 ${pct(p.wr)}</span>`
          )
          .join('')}</div>`
      : '<span class="hint">没有选手数据</span>';

    $('modalBody').innerHTML = `
      <div class="mh">
        <img src="${heroIcon(h.hero_id)}" onerror="this.style.display='none'">
        <div>
          <h2>${h.cname}</h2>
          <div class="t">${h.title || ''} · ${['', '对抗路', '中路', '发育路', '打野', '游走'][h.lane] || '—'}</div>
        </div>
      </div>
      <div class="stats">
        <div class="stat"><b>${h.picks}</b><span>出场</span></div>
        <div class="stat"><b>${h.bans}</b><span>被 ban</span></div>
        <div class="stat"><b>${pct(wr)}</b><span>胜率</span></div>
        <div class="stat"><b>${h.presence}</b><span>presence</span></div>
      </div>
      <div class="msec"><h3>压制（对位胜率 ≥50%）</h3>${pills(d.matchups.beats, 'good')}</div>
      <div class="msec"><h3>怕谁（对位胜率 &lt;50%）</h3>${pills(d.matchups.loses, 'bad')}</div>
      <div class="msec"><h3>最常用它的选手</h3>${players}</div>
    `;
    $('heroModal').classList.remove('hidden');
  } catch (e) {
    flash(e.message, true);
  }
}

// -------------------------------------------------------------------- bind
function bind() {
  $('btnNew').addEventListener('click', newMatch);
  $('btnUndo').addEventListener('click', undo);
  $('btnSave').addEventListener('click', saveGame);

  document.querySelectorAll('.tab').forEach((t) => {
    t.addEventListener('click', () => loadMatches(t.dataset.src).catch((e) => flash(e.message, true)));
  });

  $('laneTabs').innerHTML =
    `<span class="lane-tab on" data-lane="0">全部</span>` +
    LANES.map((l) => `<span class="lane-tab" data-lane="${l.id}">${l.name}</span>`).join('');

  $('laneTabs').addEventListener('click', (e) => {
    const t = e.target.closest('.lane-tab');
    if (!t) return;
    laneFilter = Number(t.dataset.lane);
    document.querySelectorAll('.lane-tab').forEach((n) => n.classList.toggle('on', n === t));
    renderGrid();
  });

  $('search').addEventListener('input', (e) => {
    searchText = e.target.value;
    renderGrid();
  });

  $('heroGrid').addEventListener('click', (e) => {
    const n = e.target.closest('.hero');
    if (!n) return;
    act(Number(n.dataset.id));
  });

  $('heroGrid').addEventListener('contextmenu', (e) => {
    const n = e.target.closest('.hero');
    if (!n) return;
    e.preventDefault();
    openHeroModal(Number(n.dataset.id));
  });

  $('seasonSel').addEventListener('change', (e) => changeSeason(e.target.value));

  $('modalClose').addEventListener('click', () => $('heroModal').classList.add('hidden'));
  $('heroModal').addEventListener('click', (e) => {
    if (e.target.id === 'heroModal') $('heroModal').classList.add('hidden');
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') $('heroModal').classList.add('hidden');
  });

  $('suggestList').addEventListener('click', (e) => {
    const n = e.target.closest('.sg');
    if (!n) return;
    act(Number(n.dataset.id));
  });

  $('winBtns').addEventListener('click', (e) => {
    const n = e.target.closest('[data-win]');
    if (!n) return;
    winner(n.dataset.win);
  });

  $('matchList').addEventListener('click', (e) => {
    const n = e.target.closest('.mrow');
    if (!n) return;
    openMatch(n.dataset.id);
  });

  $('matchDetail').addEventListener('click', (e) => {
    const b = e.target.closest('[data-drill]');
    if (!b) return;
    startDrill(b.dataset.drill, Number(b.dataset.round));
  });

  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'z') {
      e.preventDefault();
      undo();
    }
  });
}

(async function main() {
  bind();
  try {
    await loadMeta();
  } catch (e) {
    flash('加载数据失败：' + e.message + '（先跑 python -m kplbp.ingest）', true);
    return;
  }
  const ok = await restore();
  if (!ok) {
    $('heroGrid').innerHTML = '<span class="hint">先选双方战队，点「新建比赛」</span>';
  }
})();
