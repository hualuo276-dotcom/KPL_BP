"""SQLite 存储层。

表结构刻意保持扁平和可追溯：每张表都能对上 kplow 的一个接口，
`picks` / `bans` 分开存，方便后面把「官方 pick 数据」和
「模拟器自己记录的 ban」拼成 presence（出场率 = pick + ban）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);

CREATE TABLE IF NOT EXISTS heroes (
    hero_id    INTEGER PRIMARY KEY,
    cname      TEXT NOT NULL,
    title      TEXT,
    id_name    TEXT,
    hero_type  INTEGER,
    roles      TEXT,
    lane       INTEGER,            -- 官方站位编号：1对抗路 2中路 3发育路 4打野 5游走
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS seasons (
    seasonid TEXT PRIMARY KEY,
    name     TEXT,
    year     INTEGER,
    start_ts INTEGER,
    end_ts   INTEGER,
    is_cur   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS teams (
    team_id  TEXT,
    seasonid TEXT,
    name     TEXT,
    logo     TEXT,
    PRIMARY KEY (team_id, seasonid)
);

CREATE TABLE IF NOT EXISTS players (
    playerid  TEXT,
    seasonid  TEXT,
    team_id   TEXT,
    name      TEXT,
    position  INTEGER,
    avatar    TEXT,
    PRIMARY KEY (playerid, seasonid)
);

CREATE TABLE IF NOT EXISTS matches (
    schedule_id TEXT PRIMARY KEY,
    seasonid    TEXT,
    stage_id    TEXT,
    stage_name  TEXT,
    bo_total    INTEGER,
    start_ts    INTEGER,
    status      INTEGER,
    format      TEXT,
    title       TEXT,
    team_a      TEXT,
    team_b      TEXT,
    team_a_name TEXT,
    team_b_name TEXT,
    score_a     INTEGER,
    score_b     INTEGER,
    source      TEXT DEFAULT 'official'   -- official / sim
);

CREATE TABLE IF NOT EXISTS games (
    schedule_id   TEXT,
    round         INTEGER,
    win_team_id   TEXT,
    win_team_name TEXT,
    PRIMARY KEY (schedule_id, round)
);

CREATE TABLE IF NOT EXISTS picks (
    schedule_id TEXT,
    round       INTEGER,
    team_id     TEXT,
    playerid    TEXT,
    hero_id     INTEGER,
    position    INTEGER,
    PRIMARY KEY (schedule_id, round, hero_id)
);

CREATE TABLE IF NOT EXISTS bans (
    schedule_id TEXT,
    round       INTEGER,
    team_id     TEXT,
    side        TEXT,
    hero_id     INTEGER,
    source      TEXT DEFAULT 'manual',
    PRIMARY KEY (schedule_id, round, hero_id)
);

CREATE TABLE IF NOT EXISTS hero_rank (
    seasonid           TEXT,
    hero_id            INTEGER,
    match_count        INTEGER,
    win_cr             REAL,
    position           INTEGER,
    best_player        TEXT,
    best_player_win_cr REAL,
    PRIMARY KEY (seasonid, hero_id)
);

-- 同位置对位：hero_a 在对面的同分路是 hero_b 时，a 方的战绩 ----------------
CREATE TABLE IF NOT EXISTS matchups (
    seasonid TEXT,
    hero_a   INTEGER,
    hero_b   INTEGER,
    games    INTEGER,
    wins_a   INTEGER,
    PRIMARY KEY (seasonid, hero_a, hero_b)
);

-- 官方真实 BP 顺序（smoba /battle/open 的 bp_list）-------------------------
-- camp: 1=蓝方 2=红方；is_pick: 1=pick 0=ban
CREATE TABLE IF NOT EXISTS bp_order (
    schedule_id TEXT,
    round       INTEGER,
    seq         INTEGER,
    camp        INTEGER,
    is_pick     INTEGER,
    hero_id     INTEGER,
    position    INTEGER,
    PRIMARY KEY (schedule_id, round, seq)
);

-- 真实 BP 里 camp1/camp2 到底是哪两支队伍（否则复盘会把蓝红摆反）
CREATE TABLE IF NOT EXISTS bp_sides (
    schedule_id TEXT,
    round       INTEGER,
    blue_name   TEXT,
    red_name    TEXT,
    PRIMARY KEY (schedule_id, round)
);

CREATE INDEX IF NOT EXISTS idx_picks_hero   ON picks(hero_id);
CREATE INDEX IF NOT EXISTS idx_picks_team   ON picks(team_id);
CREATE INDEX IF NOT EXISTS idx_games_match  ON games(schedule_id);
CREATE INDEX IF NOT EXISTS idx_matches_seas ON matches(seasonid);

-- 官方对局里的英雄出场 / 胜场 ------------------------------------------------
DROP VIEW IF EXISTS v_pick_stat;
CREATE VIEW v_pick_stat AS
SELECT m.seasonid                AS seasonid,
       m.source                  AS source,
       p.hero_id                 AS hero_id,
       COUNT(*)                  AS picks,
       SUM(CASE WHEN p.team_id = g.win_team_id THEN 1 ELSE 0 END) AS wins
FROM picks p
JOIN matches m ON m.schedule_id = p.schedule_id
LEFT JOIN games g ON g.schedule_id = p.schedule_id AND g.round = p.round
GROUP BY m.seasonid, m.source, p.hero_id;

-- ban 统计（官方 + 手工 + 模拟器）-------------------------------------------
DROP VIEW IF EXISTS v_ban_stat;
CREATE VIEW v_ban_stat AS
SELECT m.seasonid AS seasonid,
       m.source   AS source,
       b.hero_id  AS hero_id,
       COUNT(*)   AS bans
FROM bans b
JOIN matches m ON m.schedule_id = b.schedule_id
GROUP BY m.seasonid, m.source, b.hero_id;

-- 英雄综合（按赛季参数化查询，见 hero_meta()）-------------------------------

-- 选手英雄池 ----------------------------------------------------------------
DROP VIEW IF EXISTS v_player_hero;
CREATE VIEW v_player_hero AS
SELECT p.playerid                     AS playerid,
       p.team_id                      AS team_id,
       p.hero_id                      AS hero_id,
       COUNT(*)                       AS games,
       SUM(CASE WHEN p.team_id = g.win_team_id THEN 1 ELSE 0 END) AS wins
FROM picks p
JOIN matches m ON m.schedule_id = p.schedule_id AND m.source = 'official'
LEFT JOIN games g ON g.schedule_id = p.schedule_id AND g.round = p.round
GROUP BY p.playerid, p.team_id, p.hero_id;
"""


def connect(path: str, check_same_thread: bool = True) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """老库补列。CREATE TABLE IF NOT EXISTS 不会改已存在的表。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(matches)")}
    if "source" not in cols:
        conn.execute("ALTER TABLE matches ADD COLUMN source TEXT DEFAULT 'official'")
        conn.commit()


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #
def upsert_heroes(conn: sqlite3.Connection, heroes: Iterable[dict]) -> int:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    rows = [
        (
            h["hero_id"],
            h["cname"],
            h.get("title", ""),
            h.get("id_name", ""),
            h.get("hero_type", 0),
            h.get("roles", ""),
            None,
            now,
        )
        for h in heroes
    ]
    conn.executemany(
        """INSERT INTO heroes(hero_id,cname,title,id_name,hero_type,roles,lane,updated_at)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(hero_id) DO UPDATE SET
             cname=excluded.cname, title=excluded.title, id_name=excluded.id_name,
             hero_type=excluded.hero_type, roles=excluded.roles,
             lane=COALESCE(heroes.lane, excluded.lane), updated_at=excluded.updated_at""",
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_seasons(conn: sqlite3.Connection, seasons: Iterable[dict]) -> int:
    rows = [
        (s["seasonid"], s.get("name", ""), s.get("year", 0), s.get("start_ts", 0), s.get("end_ts", 0), s.get("is_cur", 0))
        for s in seasons
    ]
    conn.executemany(
        """INSERT INTO seasons(seasonid,name,year,start_ts,end_ts,is_cur) VALUES(?,?,?,?,?,?)
           ON CONFLICT(seasonid) DO UPDATE SET
             name=excluded.name, year=excluded.year, start_ts=excluded.start_ts,
             end_ts=excluded.end_ts, is_cur=excluded.is_cur""",
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_teams(conn: sqlite3.Connection, seasonid: str, teams: Iterable[dict]) -> int:
    team_rows, player_rows = [], []
    for t in teams:
        team_rows.append((t["team_id"], seasonid, t.get("name", ""), t.get("logo", "")))
        for p in t.get("players", []):
            player_rows.append(
                (p.get("playerid", ""), seasonid, t["team_id"], p.get("name", ""), p.get("position", 0), p.get("avatar", ""))
            )
    conn.executemany(
        """INSERT INTO teams(team_id,seasonid,name,logo) VALUES(?,?,?,?)
           ON CONFLICT(team_id,seasonid) DO UPDATE SET name=excluded.name, logo=excluded.logo""",
        team_rows,
    )
    conn.executemany(
        """INSERT INTO players(playerid,seasonid,team_id,name,position,avatar) VALUES(?,?,?,?,?,?)
           ON CONFLICT(playerid,seasonid) DO UPDATE SET
             team_id=excluded.team_id, name=excluded.name,
             position=excluded.position, avatar=excluded.avatar""",
        player_rows,
    )
    conn.commit()
    return len(team_rows)


def upsert_matches(conn: sqlite3.Connection, seasonid: str, rows: Iterable[dict]) -> int:
    out = []
    for r in rows:
        out.append(
            (
                r.get("scheduleid", ""),
                seasonid,
                r.get("stageid", ""),
                r.get("stage_name", ""),
                int(float(r.get("bo_total") or 0)),
                int(float(r.get("start_timestamp") or 0)),
                int(float(r.get("schedule_status") or 0)),
                r.get("competition_format", ""),
                r.get("title", ""),
                r.get("team_a_id", "") or r.get("team1_id", ""),
                r.get("team_b_id", "") or r.get("team2_id", ""),
                r.get("team_a_name", ""),
                r.get("team_b_name", ""),
                int(float(r.get("team_a_score") or 0)),
                int(float(r.get("team_b_score") or 0)),
            )
        )
    conn.executemany(
        """INSERT INTO matches(schedule_id,seasonid,stage_id,stage_name,bo_total,start_ts,
                               status,format,title,team_a,team_b,team_a_name,team_b_name,score_a,score_b)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(schedule_id) DO UPDATE SET
             seasonid=excluded.seasonid, stage_id=excluded.stage_id, stage_name=excluded.stage_name,
             bo_total=excluded.bo_total, start_ts=excluded.start_ts, status=excluded.status,
             format=excluded.format, title=excluded.title, team_a=excluded.team_a,
             team_b=excluded.team_b, team_a_name=excluded.team_a_name,
             team_b_name=excluded.team_b_name,
             score_a=excluded.score_a, score_b=excluded.score_b""",
        out,
    )
    conn.commit()
    return len(out)


def replace_match_detail(conn: sqlite3.Connection, schedule_id: str, detail: dict) -> int:
    """写入单场所有小局的 pick / 胜负。先删后插，保证可重跑。"""
    data = detail.get("data") or {}
    rounds = data.get("round_details") or []
    if not rounds:
        return 0

    # team_id 归属：detail 自带的 players 最准，其次查全局 players 表
    player_team = {
        str(p.get("playerid", "")): p.get("team_id", "")
        for p in (data.get("players") or [])
        if isinstance(p, dict)
    }
    for r in conn.execute("SELECT playerid, team_id FROM players"):
        player_team.setdefault(r["playerid"], r["team_id"])

    game_rows, pick_rows = [], []
    for rd in rounds:
        rnd = int(float(rd.get("round") or 0))
        win_team = rd.get("win_team", "")
        game_rows.append((schedule_id, rnd, win_team, rd.get("win_team_name", "")))
        for p in rd.get("players") or []:
            pid = str(p.get("playerid", ""))
            pick_rows.append(
                (
                    schedule_id,
                    rnd,
                    player_team.get(pid, ""),
                    pid,
                    int(float(p.get("hero_id") or 0)),
                    int(float(p.get("position") or 0)),
                )
            )

    conn.execute("DELETE FROM games WHERE schedule_id=?", (schedule_id,))
    conn.execute("DELETE FROM picks WHERE schedule_id=?", (schedule_id,))
    conn.executemany(
        "INSERT OR REPLACE INTO games(schedule_id,round,win_team_id,win_team_name) VALUES(?,?,?,?)", game_rows
    )
    conn.executemany(
        """INSERT OR REPLACE INTO picks(schedule_id,round,team_id,playerid,hero_id,position)
           VALUES(?,?,?,?,?,?)""",
        [r for r in pick_rows if r[4] > 0],
    )
    conn.commit()
    return len(game_rows)


def replace_hero_rank(conn: sqlite3.Connection, seasonid: str, rows: Iterable[dict]) -> int:
    out = [
        (
            seasonid,
            r["hero_id"],
            r.get("match_count", 0),
            r.get("win_cr", 0.0),
            r.get("position", 0),
            r.get("best_player", ""),
            r.get("best_player_win_cr", 0.0),
        )
        for r in rows
    ]
    conn.execute("DELETE FROM hero_rank WHERE seasonid=?", (seasonid,))
    conn.executemany(
        """INSERT OR REPLACE INTO hero_rank(seasonid,hero_id,match_count,win_cr,position,best_player,best_player_win_cr)
           VALUES(?,?,?,?,?,?,?)""",
        out,
    )
    conn.commit()
    return len(out)


def recompute_lanes(conn: sqlite3.Connection) -> int:
    """按官方对局里各英雄实际站位，算主分路。

    round_details[].players[].position 的口径是官方内部顺序：
        1 对抗路 · 2 中路 · 3 发育路 · 4 打野 · 5 游走
    （注意和国标 1对抗/2打野/3中路/4发育/5游走 不一样，别照直觉写）
    多局投票取出现最多的那个位置。
    """
    rows = conn.execute(
        """SELECT hero_id, position, COUNT(*) c FROM picks
           WHERE position BETWEEN 1 AND 5
           GROUP BY hero_id, position ORDER BY hero_id, c DESC"""
    ).fetchall()
    best: dict[int, tuple[int, int]] = {}
    for r in rows:
        hid, pos, c = r["hero_id"], r["position"], r["c"]
        if hid not in best or c > best[hid][1]:
            best[hid] = (pos, c)

    # 没有对局数据的英雄，按 hero_type 粗兜底：1战士 2法师 3坦克 4刺客 5射手 6辅助
    type_lane = {1: 1, 2: 2, 3: 5, 4: 4, 5: 3, 6: 5}
    for r in conn.execute("SELECT hero_id, hero_type FROM heroes WHERE lane IS NULL").fetchall():
        if r["hero_id"] in best:
            continue
        conn.execute(
            "UPDATE heroes SET lane=? WHERE hero_id=?",
            (type_lane.get(r["hero_type"] or 0, 1), r["hero_id"]),
        )

    conn.executemany("UPDATE heroes SET lane=? WHERE hero_id=?", [(p, h) for h, (p, _) in best.items()])
    conn.commit()
    return len(best)


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (key, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


# --------------------------------------------------------------------------- #
# 读：给 BP 引擎和前端用的聚合
# --------------------------------------------------------------------------- #
HERO_META_SQL = """
SELECT h.hero_id,
       h.cname,
       h.title,
       h.hero_type,
       h.lane,
       COALESCE(p.picks, 0)  AS picks,
       COALESCE(p.wins, 0)   AS wins,
       COALESCE(b.bans, 0)   AS bans,
       COALESCE(p.picks, 0) + COALESCE(b.bans, 0) AS presence,
       CASE WHEN COALESCE(p.picks, 0) > 0
            THEN ROUND(1.0 * p.wins / p.picks, 4) ELSE NULL END AS pick_wr,
       r.match_count  AS rank_matches,
       r.win_cr       AS rank_wr,
       r.best_player  AS best_player,
       r.best_player_win_cr AS best_player_wr
FROM heroes h
-- pick 只算官方数据：模拟对局不进胜率，免得把真实数据掺水
LEFT JOIN (
    SELECT hero_id, SUM(picks) AS picks, SUM(wins) AS wins
    FROM v_pick_stat
    WHERE seasonid = :season AND source = 'official'
    GROUP BY hero_id
) p ON p.hero_id = h.hero_id
-- ban 把所有来源都算上：官方本来就不给 ban，模拟/手工数据正是来补这个缺口的
LEFT JOIN (
    SELECT hero_id, SUM(bans) AS bans
    FROM v_ban_stat
    WHERE seasonid = :season
    GROUP BY hero_id
) b ON b.hero_id = h.hero_id
LEFT JOIN hero_rank r ON r.hero_id = h.hero_id AND r.seasonid = :season
ORDER BY h.hero_id
"""


def hero_meta(conn: sqlite3.Connection, seasonid: str) -> list[dict]:
    return [dict(r) for r in conn.execute(HERO_META_SQL, {"season": seasonid})]


def team_players(conn: sqlite3.Connection, seasonid: str, team_id: str) -> list[dict]:
    sql = "SELECT * FROM players WHERE seasonid=? AND team_id=? ORDER BY position"
    return [dict(r) for r in conn.execute(sql, (seasonid, team_id))]


def player_pool(conn: sqlite3.Connection, seasonid: str, team_id: str) -> dict[int, dict]:
    """某队所有选手的英雄池，按 hero_id 汇总：{hero_id: {games, wins, by:[选手]}}"""
    sql = """
    SELECT ph.playerid, ph.hero_id, ph.games, ph.wins, pl.name, pl.position
    FROM v_player_hero ph
    JOIN picks pk ON pk.playerid = ph.playerid AND pk.hero_id = ph.hero_id
    JOIN matches m ON m.schedule_id = pk.schedule_id
    LEFT JOIN players pl ON pl.playerid = ph.playerid AND pl.seasonid = m.seasonid
    WHERE ph.team_id = ? AND m.seasonid = ?
    GROUP BY ph.playerid, ph.hero_id
    """
    pool: dict[int, dict] = {}
    for r in conn.execute(sql, (team_id, seasonid)):
        hid = r["hero_id"]
        node = pool.setdefault(hid, {"hero_id": hid, "games": 0, "wins": 0, "players": []})
        node["games"] += r["games"] or 0
        node["wins"] += r["wins"] or 0
        node["players"].append(
            {
                "playerid": r["playerid"],
                "name": r["name"] or "",
                "position": r["position"] or 0,
                "games": r["games"] or 0,
                "wins": r["wins"] or 0,
            }
        )
    for node in pool.values():
        node["wr"] = round(node["wins"] / node["games"], 4) if node["games"] else None
        node["players"].sort(key=lambda x: -x["games"])
    return pool


# --------------------------------------------------------------------------- #
# 模拟对局入库：补上官方不给的 ban 数据
# --------------------------------------------------------------------------- #
def save_sim_match(
    conn: sqlite3.Connection,
    seasonid: str,
    state: dict,
    blue_name: str = "",
    red_name: str = "",
    schedule_id: str | None = None,
) -> str:
    """把一场模拟对局写进 matches / games / picks / bans，source='sim'。

    * pick 只用于回放，不参与官方胜率统计（HERO_META_SQL 里 pick 只取 official）
    * ban 参与统计 —— 官方接口本来就不给 ban，这里就是那个缺口
    * 传 schedule_id 复用旧编号（重新保存时链接不会断）
    返回写入的 schedule_id。"""
    sid = schedule_id or ("SIM" + time.strftime("%Y%m%d%H%M%S") + os.urandom(2).hex().upper())
    blue = state.get("blue_team") or "SIM_BLUE"
    red = state.get("red_team") or "SIM_RED"
    wins_b = sum(1 for g in state["games"] if g.get("winner") == "B")
    wins_r = sum(1 for g in state["games"] if g.get("winner") == "R")
    played = sum(1 for g in state["games"] if g.get("winner"))

    title = f"模拟 BO{state['bo']} {blue_name or blue} {wins_b}:{wins_r} {red_name or red}"

    conn.execute(
        """INSERT INTO matches(schedule_id,seasonid,stage_id,stage_name,bo_total,start_ts,
                               status,format,title,team_a,team_b,team_a_name,team_b_name,
                               score_a,score_b,source)
           VALUES(?,?,'sim','模拟对局',?,?,5,'SIM',?,?,?,?,?,?,?,'sim')""",
        (
            sid,
            seasonid,
            state["bo"],
            int(time.time()),
            title,
            blue,
            red,
            blue_name or blue,
            red_name or red,
            wins_b,
            wins_r,
        ),
    )

    game_rows, pick_rows, ban_rows = [], [], []
    for g in state["games"]:
        if not g.get("winner"):
            continue  # 只保存打完的小局
        rnd = g["round"]
        team_of = {"B": blue, "R": red}
        game_rows.append((sid, rnd, team_of[g["winner"]], blue_name if g["winner"] == "B" else red_name))
        for side in ("B", "R"):
            for hid in g["picks"][side]:
                pick_rows.append((sid, rnd, team_of[side], "", int(hid), 0))
            for hid in g["bans"][side]:
                ban_rows.append((sid, rnd, team_of[side], side, int(hid), "sim"))

    conn.executemany(
        "INSERT OR REPLACE INTO games(schedule_id,round,win_team_id,win_team_name) VALUES(?,?,?,?)",
        game_rows,
    )
    conn.executemany(
        """INSERT OR REPLACE INTO picks(schedule_id,round,team_id,playerid,hero_id,position)
           VALUES(?,?,?,?,?,?)""",
        pick_rows,
    )
    conn.executemany(
        """INSERT OR REPLACE INTO bans(schedule_id,round,team_id,side,hero_id,source)
           VALUES(?,?,?,?,?,?)""",
        ban_rows,
    )
    conn.commit()
    return sid


def delete_sim_match(conn: sqlite3.Connection, schedule_id: str) -> None:
    for table in ("bans", "picks", "games", "matches"):
        conn.execute(f"DELETE FROM {table} WHERE schedule_id=?", (schedule_id,))
    conn.commit()


def rebuild_matchups(conn: sqlite3.Connection) -> int:
    """重建同位置对位矩阵。

    只看官方对局：每小局里，蓝红双方 position 相同的两个英雄互为对位，
    a 方赢了就给 (a, b) 记一个 wins_a。position 必须相等——跨分路比较没有意义。
    """
    conn.execute("DELETE FROM matchups")
    conn.execute(
        """
        INSERT INTO matchups(seasonid, hero_a, hero_b, games, wins_a)
        SELECT m.seasonid,
               p1.hero_id,
               p2.hero_id,
               COUNT(*),
               SUM(CASE WHEN g.win_team_id = p1.team_id THEN 1 ELSE 0 END)
        FROM picks p1
        JOIN picks p2
          ON p2.schedule_id = p1.schedule_id
         AND p2.round       = p1.round
         AND p2.position    = p1.position
         AND p2.team_id    <> p1.team_id
        JOIN matches m ON m.schedule_id = p1.schedule_id AND m.source = 'official'
        JOIN games g   ON g.schedule_id = p1.schedule_id AND g.round = p1.round
        WHERE p1.position BETWEEN 1 AND 5
          AND p1.team_id <> '' AND p2.team_id <> ''
        GROUP BY m.seasonid, p1.hero_id, p2.hero_id
        """
    )
    conn.commit()
    return conn.execute("SELECT COUNT(*) c FROM matchups").fetchone()["c"]


def load_matchups(
    conn: sqlite3.Connection,
    min_games: float = 2.0,
    decay: float = 0.85,
    seasonid: str | None = None,
) -> dict[tuple[int, int], tuple[float, float]]:
    """跨赛季汇总对位战绩，**按赛季新旧衰减**。{(a, b): (加权场次, 加权胜场)}

    英雄平衡性每版本都在改，2024 年的克制关系放到 2026 未必成立。
    所以每个赛季的样本乘 `decay ** 赛季差`（赛季差 = 距目标赛季隔了几季）：

        最新赛季 1.00 · 上季 0.85 · 再上 0.72 · 0.61 · 0.52 · 0.44

    这样老样本不会消失，但会自己变轻；`min_games` 是对**加权后**的场次卡的，
    所以老数据要更多原始对局才够格。
    """
    seasons = [
        r["seasonid"]
        for r in conn.execute("SELECT seasonid FROM seasons ORDER BY start_ts DESC")
    ]
    order = {s: i for i, s in enumerate(seasons)}
    target = seasonid if seasonid in order else (seasons[0] if seasons else None)
    if target is None:
        return {}
    base = order[target]

    agg: dict[tuple[int, int], list[float]] = {}
    for r in conn.execute("SELECT seasonid, hero_a, hero_b, games, wins_a FROM matchups"):
        gap = order.get(r["seasonid"], base) - base
        if gap < 0:  # 比目标赛季更新的数据，不参与
            continue
        w = decay ** gap
        key = (r["hero_a"], r["hero_b"])
        node = agg.setdefault(key, [0.0, 0.0])
        node[0] += r["games"] * w
        node[1] += (r["wins_a"] or 0) * w

    return {k: (g, w) for k, (g, w) in agg.items() if g >= min_games}


def matchup_detail(conn: sqlite3.Connection, hero_id: int, limit: int = 8) -> dict:
    """某个英雄的对位明细：压制谁、被谁压制。

    注意表里 (X,Y) 和 (Y,X) 是**两行**（互为镜像）。所以必须按对手去重：
    先用 hero_a = X 的行（X 视角），缺的再用 hero_b = X 的行取反补上，
    否则同一个对手会在「压制」和「怕谁」里各出现一次。
    """
    seen: dict[int, dict] = {}

    for r in conn.execute(
        """SELECT hero_b AS other, SUM(games) g, SUM(wins_a) w
           FROM matchups WHERE hero_a = ?
           GROUP BY hero_b HAVING SUM(games) >= 3""",
        (hero_id,),
    ):
        seen[r["other"]] = {"hero_id": r["other"], "games": r["g"], "wr": round(r["w"] / r["g"], 3)}

    for r in conn.execute(
        """SELECT hero_a AS other, SUM(games) g, SUM(wins_a) w
           FROM matchups WHERE hero_b = ?
           GROUP BY hero_a HAVING SUM(games) >= 3""",
        (hero_id,),
    ):
        if r["other"] in seen:
            continue  # 正方向已经有了
        seen[r["other"]] = {"hero_id": r["other"], "games": r["g"], "wr": round(1 - r["w"] / r["g"], 3)}

    beats = [v for v in seen.values() if v["wr"] >= 0.5]
    loses = [v for v in seen.values() if v["wr"] < 0.5]
    beats.sort(key=lambda x: (-x["wr"], -x["games"]))
    loses.sort(key=lambda x: (x["wr"], -x["games"]))
    return {"beats": beats[:limit], "loses": loses[:limit]}


def list_sim_matches(conn: sqlite3.Connection, seasonid: str, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        """SELECT m.schedule_id, m.title, m.team_a_name, m.team_b_name,
                  m.score_a, m.score_b, m.bo_total, m.start_ts,
                  (SELECT COUNT(*) FROM bans b WHERE b.schedule_id = m.schedule_id) AS ban_count,
                  (SELECT COUNT(*) FROM games g WHERE g.schedule_id = m.schedule_id) AS rounds
           FROM matches m
           WHERE m.seasonid = ? AND m.source = 'sim'
           ORDER BY m.start_ts DESC LIMIT ?""",
        (seasonid, limit),
    )
    return [dict(r) for r in rows]


def ban_leaderboard(conn: sqlite3.Connection, seasonid: str, limit: int = 20) -> list[dict]:
    """ban 榜：只统计真有 ban 记录的数据。"""
    rows = conn.execute(
        """SELECT h.hero_id, h.cname, h.lane, SUM(b.bans) AS bans,
                  GROUP_CONCAT(DISTINCT b.source) AS sources
           FROM v_ban_stat b
           JOIN heroes h ON h.hero_id = b.hero_id
           WHERE b.seasonid = ?
           GROUP BY h.hero_id ORDER BY bans DESC LIMIT ?""",
        (seasonid, limit),
    )
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# 官方真实 BP 顺序（含 ban）
# --------------------------------------------------------------------------- #
def replace_bp_order(
    conn: sqlite3.Connection,
    schedule_id: str,
    rnd: int,
    actions: list[dict],
    blue_name: str = "",
    red_name: str = "",
) -> int:
    """写入一局的真实 BP 顺序，并把 ban 同步进 `bans`（source='official'）。

    actions: [{seq, camp, is_pick, hero_id, position}]
    camp 1 = 蓝方、2 = 红方；is_pick 1 = 选人、0 = 禁用
    """
    conn.execute("DELETE FROM bp_order WHERE schedule_id=? AND round=?", (schedule_id, rnd))
    conn.execute("DELETE FROM bans WHERE schedule_id=? AND round=? AND source='official'", (schedule_id, rnd))

    order_rows = [
        (schedule_id, rnd, a["seq"], a["camp"], a["is_pick"], a["hero_id"], a.get("position", 0))
        for a in actions
    ]
    ban_rows = [
        (schedule_id, rnd, "", "B" if a["camp"] == 1 else "R", a["hero_id"], "official")
        for a in actions
        if a["is_pick"] == 0
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO bp_order(schedule_id,round,seq,camp,is_pick,hero_id,position)
           VALUES(?,?,?,?,?,?,?)""",
        order_rows,
    )
    conn.executemany(
        """INSERT OR REPLACE INTO bans(schedule_id,round,team_id,side,hero_id,source)
           VALUES(?,?,?,?,?,?)""",
        ban_rows,
    )
    if blue_name or red_name:
        conn.execute(
            "INSERT OR REPLACE INTO bp_sides(schedule_id,round,blue_name,red_name) VALUES(?,?,?,?)",
            (schedule_id, rnd, blue_name, red_name),
        )
    conn.commit()
    return len(order_rows)


def bp_sides(conn: sqlite3.Connection, schedule_id: str, rnd: int) -> tuple[str, str]:
    row = conn.execute(
        "SELECT blue_name, red_name FROM bp_sides WHERE schedule_id=? AND round=?", (schedule_id, rnd)
    ).fetchone()
    return (row["blue_name"], row["red_name"]) if row else ("", "")


def backfill_bp_sides(conn: sqlite3.Connection) -> int:
    """离线补 bp_sides：用 bp_order 里 camp1/camp2 的选人集合，
    去和 picks 表里各战队的选人集合对，对上就知道 camp1 是哪支队。

    不用再联网——两边数据都在库里，直接集合比对。
    """
    names: dict[str, str] = {}
    for r in conn.execute("SELECT team_id, name FROM teams"):
        names[r["team_id"]] = r["name"]
    for r in conn.execute(
        "SELECT team_a, team_a_name, team_b, team_b_name FROM matches WHERE source='official'"
    ):
        if r["team_a"]:
            names.setdefault(r["team_a"], r["team_a_name"] or r["team_a"])
        if r["team_b"]:
            names.setdefault(r["team_b"], r["team_b_name"] or r["team_b"])

    done = 0
    for r in conn.execute("SELECT DISTINCT schedule_id, round FROM bp_order").fetchall():
        sid, rnd = r["schedule_id"], r["round"]
        bp: dict[int, set[int]] = {1: set(), 2: set()}
        for a in conn.execute(
            "SELECT camp, hero_id FROM bp_order WHERE schedule_id=? AND round=? AND is_pick=1", (sid, rnd)
        ):
            bp[a["camp"]].add(a["hero_id"])
        if not bp[1] or not bp[2]:
            continue

        teams: dict[str, set[int]] = {}
        for p in conn.execute(
            "SELECT team_id, hero_id FROM picks WHERE schedule_id=? AND round=?", (sid, rnd)
        ):
            teams.setdefault(p["team_id"], set()).add(p["hero_id"])

        b1 = next((t for t, h in teams.items() if h == bp[1]), None)
        b2 = next((t for t, h in teams.items() if h == bp[2]), None)
        if b1 and b2:
            conn.execute(
                "INSERT OR REPLACE INTO bp_sides(schedule_id,round,blue_name,red_name) VALUES(?,?,?,?)",
                (sid, rnd, names.get(b1, b1), names.get(b2, b2)),
            )
            done += 1
    conn.commit()
    return done


def load_bp_order(conn: sqlite3.Connection, schedule_id: str, rnd: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM bp_order WHERE schedule_id=? AND round=? ORDER BY seq", (schedule_id, rnd)
    )
    return [dict(r) for r in rows]


def matches_with_bp(conn: sqlite3.Connection, seasonid: str, limit: int = 40) -> list[dict]:
    """有真实 BP 顺序的对局（复盘模式用）。"""
    rows = conn.execute(
        """SELECT m.schedule_id, m.stage_name, m.start_ts, m.bo_total,
                  m.team_a_name, m.team_b_name, m.score_a, m.score_b,
                  COUNT(DISTINCT o.round) AS rounds,
                  (SELECT COUNT(*) FROM bp_order x WHERE x.schedule_id = m.schedule_id) AS actions
           FROM matches m
           JOIN bp_order o ON o.schedule_id = m.schedule_id
           WHERE m.seasonid = ? AND m.source = 'official'
           GROUP BY m.schedule_id
           ORDER BY m.start_ts DESC LIMIT ?""",
        (seasonid, limit),
    )
    return [dict(r) for r in rows]


def bp_coverage(conn: sqlite3.Connection) -> dict:
    total_games = conn.execute("SELECT COUNT(*) c FROM games").fetchone()["c"]
    with_bp = conn.execute("SELECT COUNT(DISTINCT schedule_id || '#' || round) c FROM bp_order").fetchone()["c"]
    bans = conn.execute("SELECT COUNT(*) c FROM bans WHERE source='official'").fetchone()["c"]
    return {"games": total_games, "with_bp": with_bp, "official_bans": bans}

