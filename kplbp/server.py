"""本地 Web 服务：BP 模拟器 API + 静态页面。

只用标准库，不装任何包：
    python -m kplbp.server                 # http://127.0.0.1:8770
    python -m kplbp.server --port 9000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import engine, store, suggest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
DATA_DIR = os.path.join(ROOT, "data")
SESSION_DIR = os.path.join(DATA_DIR, "sessions")
DB_PATH = os.path.join(DATA_DIR, "kpl.db")

_lock = threading.RLock()
_sessions: dict[str, dict] = {}
_conn = None


def conn():
    """单连接 + 全局串行：本地单人工具，简单可靠比并发重要。"""
    global _conn
    if _conn is None:
        _conn = store.connect(DB_PATH, check_same_thread=False)
    return _conn


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
def _session_path(sid: str) -> str:
    return os.path.join(SESSION_DIR, f"{sid}.json")


def save_session(sid: str, state: dict) -> None:
    os.makedirs(SESSION_DIR, exist_ok=True)
    with open(_session_path(sid), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def load_session(sid: str) -> dict | None:
    if sid in _sessions:
        return _sessions[sid]
    p = _session_path(sid)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            state = json.load(f)
        _sessions[sid] = state
        return state
    return None


def new_session(bo: int, blue: str, red: str, config: dict | None) -> tuple[str, dict]:
    sid = uuid.uuid4().hex[:12]
    state = engine.new_state(bo=bo, blue_team=blue, red_team=red, config=config)
    _sessions[sid] = state
    save_session(sid, state)
    return sid, state


# --------------------------------------------------------------------------- #
# 视图拼装
# --------------------------------------------------------------------------- #
def season_id() -> str:
    return store.get_meta(conn(), "seasonid", "") or ""


def _record_drill(state: dict) -> None:
    """复盘模式：把用户这一手和真实那一手对一下。"""
    d = state.get("drill")
    if not d or not state["log"]:
        return
    i = len(d["results"])
    if i >= len(d["truth"]):
        return
    truth = d["truth"][i]
    last = state["log"][-1]
    d["results"].append(
        {
            "seq": i + 1,
            "mine": last["hero_id"],
            "truth": truth["hero_id"],
            "hit": last["hero_id"] == truth["hero_id"],
        }
    )


def _drill_view(state: dict, names: dict[int, str]) -> dict | None:
    d = state.get("drill")
    if not d:
        return None
    res = d.get("results", [])
    last = res[-1] if res else None
    return {
        "schedule_id": d["schedule_id"],
        "round": d["round"],
        "cursor": len(res),
        "total": len(d["truth"]),
        "hits": sum(1 for r in res if r["hit"]),
        "results": [
            {
                "seq": r["seq"],
                "hit": r["hit"],
                "mine": names.get(r["mine"], str(r["mine"])),
                "truth": names.get(r["truth"], str(r["truth"])),
            }
            for r in res
        ],
        "last": None
        if not last
        else {
            "hit": last["hit"],
            "mine": names.get(last["mine"], str(last["mine"])),
            "truth": names.get(last["truth"], str(last["truth"])),
            "next_side": ("B" if _truth_side(d, len(res)) == "B" else "R")
            if len(res) < len(d["truth"])
            else None,
            "next_action": _truth_action(d, len(res)),
        },
    }


def _truth_side(d: dict, i: int) -> str:
    return d["truth"][i]["side"] if i < len(d["truth"]) else ""


def _truth_action(d: dict, i: int) -> str:
    return d["truth"][i]["action"] if i < len(d["truth"]) else ""


def view_state(sid: str, state: dict) -> dict:
    season = season_id()
    cards = store.hero_meta(conn(), season)
    by_id = {c["hero_id"]: c for c in cards}
    matchup_index = store.load_matchups(conn())

    heroes = []
    for c in cards:
        hid = c["hero_id"]
        heroes.append(
            {
                "hero_id": hid,
                "cname": c["cname"],
                "title": c.get("title") or "",
                "lane": c.get("lane") or 0,
                "presence": c.get("presence") or 0,
                "pick_wr": c.get("pick_wr"),
                "rank_wr": c.get("rank_wr"),
                "status": engine.hero_state(state, hid),
            }
        )

    step = engine.current_step(state)
    picks: list[dict] = []
    if step is not None:
        side = step[0]
        own_team = state["blue_team"] if side == "B" else state["red_team"]
        opp_team = state["red_team"] if side == "B" else state["blue_team"]
        step_ = step[1]
        if step_ == "ban":
            own_pool = store.player_pool(conn(), season, own_team) if own_team else {}
            opp_pool = store.player_pool(conn(), season, opp_team) if opp_team else {}
        else:
            own_pool = store.player_pool(conn(), season, own_team) if own_team else {}
            opp_pool = {}
        picks = suggest.suggest(
            state, cards, own_pool=own_pool, opp_pool=opp_pool, limit=12, matchup_index=matchup_index
        )

    coverage = {
        "B": suggest.lane_coverage(state, cards, "B"),
        "R": suggest.lane_coverage(state, cards, "R"),
    }

    return {
        "id": sid,
        "season": season,
        "summary": engine.summary(state),
        "heroes": heroes,
        "suggest": picks,
        "coverage": coverage,
        "team_names": {"B": _team_name(state["blue_team"]), "R": _team_name(state["red_team"])},
        "saved": state.get("saved_schedule_id"),
        "kind": state.get("kind", "sim"),
        "drill": _drill_view(state, {h["hero_id"]: h["cname"] for h in heroes}),
    }


_name_cache: dict[str, str] = {}


def _team_name(team_id: str) -> str:
    if not team_id:
        return "未选择"
    if team_id in _name_cache:
        return _name_cache[team_id]
    row = conn().execute("SELECT name FROM teams WHERE team_id=? LIMIT 1", (team_id,)).fetchone()
    name = row["name"] if row else team_id
    _name_cache[team_id] = name
    return name


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "kplbp/0.1"

    def log_message(self, fmt, *args):  # 静音默认日志
        pass

    # --- helpers ---------------------------------------------------------- #
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, msg, code=400):
        self._json({"ok": False, "error": msg}, code)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _static(self, path: str):
        rel = path.lstrip("/")
        if not rel or rel.endswith("/"):
            rel = "index.html"
        full = os.path.abspath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.abspath(WEB_DIR)) or not os.path.isfile(full):
            self._err("not found", 404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".svg": "image/svg+xml",
        }.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # --- routes ----------------------------------------------------------- #
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = dict(urllib.parse.parse_qsl(parsed.query))
        try:
            if not path.startswith("/api/"):
                return self._static(path)
            with _lock:
                return self._api_get(path, q)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._err(f"{type(e).__name__}: {e}", 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            with _lock:
                return self._api_post(parsed.path, self._body())
        except engine.BpError as e:
            return self._err(str(e), 409)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._err(f"{type(e).__name__}: {e}", 500)

    def _api_get(self, path: str, q: dict):
        season = q.get("season") or season_id()

        if path == "/api/meta":
            seasons = [dict(r) for r in conn().execute("SELECT * FROM seasons ORDER BY start_ts DESC")]
            teams = [
                dict(r)
                for r in conn().execute(
                    "SELECT * FROM teams WHERE seasonid=? ORDER BY team_id", (season,)
                )
            ]
            return self._json({"ok": True, "season": season, "seasons": seasons, "teams": teams})

        if path == "/api/heroes":
            return self._json({"ok": True, "season": season, "heroes": store.hero_meta(conn(), season)})

        if path == "/api/matches":
            only_played = q.get("only_played") in ("1", "true", "yes")
            source = q.get("source") or "official"
            sql = """
                SELECT m.schedule_id, m.stage_name, m.bo_total, m.start_ts, m.status,
                       m.team_a, m.team_b, m.team_a_name, m.team_b_name,
                       m.score_a, m.score_b, m.format, m.source,
                       (SELECT COUNT(*) FROM games g WHERE g.schedule_id = m.schedule_id) AS rounds,
                       (SELECT COUNT(*) FROM bans b WHERE b.schedule_id = m.schedule_id) AS bans
                FROM matches m
                WHERE m.seasonid = :season AND m.source = :source
            """
            if only_played:
                sql += " AND EXISTS (SELECT 1 FROM games g WHERE g.schedule_id = m.schedule_id)"
            sql += " ORDER BY m.start_ts DESC LIMIT :lim"
            rows = conn().execute(sql, {"season": season, "source": source, "lim": int(q.get("limit", 40))})
            return self._json({"ok": True, "matches": [dict(r) for r in rows]})

        if path == "/api/bans":
            return self._json({"ok": True, "season": season, "bans": store.ban_leaderboard(conn(), season, 30)})

        if path == "/api/drill/list":
            return self._json(
                {"ok": True, "season": season, "matches": store.matches_with_bp(conn(), season, int(q.get("limit", 40)))}
            )

        if path == "/api/matchups":
            idx = store.load_matchups(conn(), int(q.get("min_games", 6)))
            return self._json({"ok": True, "pairs": len(idx), "min_games": int(q.get("min_games", 6))})

        if path.startswith("/api/hero/"):
            try:
                hid = int(path.rsplit("/", 1)[-1])
            except ValueError:
                return self._err("英雄 id 不对", 400)
            names = {r["hero_id"]: r["cname"] for r in conn().execute("SELECT hero_id, cname FROM heroes")}
            card = next((c for c in store.hero_meta(conn(), season) if c["hero_id"] == hid), None)
            if not card:
                return self._err("英雄不存在", 404)
            mu = store.matchup_detail(conn(), hid, 8)
            for group in ("beats", "loses"):
                for it in mu[group]:
                    it["cname"] = names.get(it["hero_id"], str(it["hero_id"]))
            owners = [
                dict(r) | {"wr": round((r["wins"] or 0) / r["games"], 3) if r["games"] else None}
                for r in conn().execute(
                    """SELECT ph.playerid, SUM(ph.games) games, SUM(ph.wins) wins,
                              MAX(pl.name) name, MAX(pl.position) position, MAX(pl.team_id) team_id
                       FROM v_player_hero ph
                       LEFT JOIN (
                           SELECT playerid, MAX(name) name, MAX(position) position, MAX(team_id) team_id
                           FROM players GROUP BY playerid
                       ) pl ON pl.playerid = ph.playerid
                       WHERE ph.hero_id = ?
                       GROUP BY ph.playerid
                       ORDER BY games DESC LIMIT 5""",
                    (hid,),
                )
            ]
            return self._json({"ok": True, "hero": card, "matchups": mu, "players": owners})

        if path.startswith("/api/match/"):
            sid_m = path.rsplit("/", 1)[-1]
            m = conn().execute("SELECT * FROM matches WHERE schedule_id=?", (sid_m,)).fetchone()
            if not m:
                return self._err("比赛不存在", 404)
            names = {r["hero_id"]: r["cname"] for r in conn().execute("SELECT hero_id, cname FROM heroes")}
            rounds = []
            for g in conn().execute(
                "SELECT * FROM games WHERE schedule_id=? ORDER BY round", (sid_m,)
            ):
                picks = [
                    dict(p) | {"hero_name": names.get(p["hero_id"], str(p["hero_id"]))}
                    for p in conn().execute(
                        "SELECT * FROM picks WHERE schedule_id=? AND round=? ORDER BY team_id, position",
                        (sid_m, g["round"]),
                    )
                ]
                bans = [
                    dict(b) | {"hero_name": names.get(b["hero_id"], str(b["hero_id"]))}
                    for b in conn().execute(
                        "SELECT * FROM bans WHERE schedule_id=? AND round=? ORDER BY side, hero_id",
                        (sid_m, g["round"]),
                    )
                ]
                rounds.append(
                    {
                        "round": g["round"],
                        "win_team": g["win_team_id"],
                        "win_team_name": g["win_team_name"],
                        "bans": bans,
                        "picks": picks,
                    }
                )
            return self._json({"ok": True, "match": dict(m), "rounds": rounds})

        if path == "/api/bp":
            sid = q.get("id", "")
            state = load_session(sid)
            if not state:
                return self._err("会话不存在", 404)
            return self._json({"ok": True, **view_state(sid, state)})

        if path == "/api/sessions":
            items = []
            if os.path.isdir(SESSION_DIR):
                for fn in sorted(os.listdir(SESSION_DIR), reverse=True)[:20]:
                    if fn.endswith(".json"):
                        items.append(fn[:-5])
            return self._json({"ok": True, "sessions": items})

        return self._err("未知接口", 404)

    def _api_post(self, path: str, body: dict):
        if path == "/api/season":
            s = str(body.get("season") or "")
            if not s:
                return self._err("缺 season 参数", 400)
            row = conn().execute("SELECT seasonid FROM seasons WHERE seasonid=?", (s,)).fetchone()
            if not row:
                return self._err(f"没有赛季 {s}", 404)
            store.set_meta(conn(), "seasonid", s)
            _name_cache.clear()
            return self._json({"ok": True, "season": s})

        if path == "/api/drill/start":
            sid_m = str(body.get("schedule_id") or "")
            rnd = int(body.get("round") or 1)
            if not sid_m:
                return self._err("缺 schedule_id", 400)
            actions = store.load_bp_order(conn(), sid_m, rnd)
            if not actions:
                return self._err("这一局没有真实 BP 记录", 404)
            m = conn().execute("SELECT * FROM matches WHERE schedule_id=?", (sid_m,)).fetchone()
            blue_name, red_name = store.bp_sides(conn(), sid_m, rnd)
            state = engine.new_state(
                bo=1,
                blue_team=blue_name or ((m["team_a"] if m else "") or ""),
                red_team=red_name or ((m["team_b"] if m else "") or ""),
            )
            truth = [
                {
                    "side": "B" if a["camp"] == 1 else "R",
                    "action": "pick" if a["is_pick"] else "ban",
                    "hero_id": a["hero_id"],
                    "position": a["position"],
                }
                for a in actions
            ]
            state["custom_steps"] = truth
            g0 = state["games"][0]
            g0["blind"] = not any(t["action"] == "ban" for t in truth)
            g0["steps"] = len(truth)
            state["kind"] = "drill"
            state["drill"] = {"schedule_id": sid_m, "round": rnd, "truth": truth, "results": []}
            sid = uuid.uuid4().hex[:12]
            _sessions[sid] = state
            save_session(sid, state)
            return self._json({"ok": True, **view_state(sid, state)})

        if path == "/api/bp/new":
            bo = int(body.get("bo") or 5)
            sid, state = new_session(bo, body.get("blue") or "", body.get("red") or "", body.get("config"))
            return self._json({"ok": True, **view_state(sid, state)})

        parts = [p for p in path.split("/") if p]
        # /api/bp/<id>/<action>
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "bp":
            sid, action = parts[2], parts[3]
            state = load_session(sid)
            if not state:
                return self._err("会话不存在", 404)
            with _lock:
                if action == "act":
                    engine.act(state, int(body.get("hero_id") or 0))
                    _record_drill(state)
                elif action == "undo":
                    engine.undo(state)
                    d = state.get("drill")
                    if d and d.get("results"):
                        d["results"].pop()
                elif action == "winner":
                    engine.set_winner(state, str(body.get("side") or ""))
                elif action == "save":
                    if not any(g.get("winner") for g in state["games"]):
                        return self._err("还没有打完的小局，没法保存", 409)
                    old = state.get("saved_schedule_id")
                    if old:
                        store.delete_sim_match(conn(), old)
                    saved = store.save_sim_match(
                        conn(),
                        season_id(),
                        state,
                        blue_name=_team_name(state["blue_team"]),
                        red_name=_team_name(state["red_team"]),
                        schedule_id=old,
                    )
                    state["saved_schedule_id"] = saved
                    save_session(sid, state)
                    return self._json({"ok": True, "saved": saved, **view_state(sid, state)})
                elif action == "reset":
                    cfg = state["config"]
                    state = engine.new_state(state["bo"], state["blue_team"], state["red_team"], cfg)
                    _sessions[sid] = state
                else:
                    return self._err("未知动作", 404)
                save_session(sid, state)
            return self._json({"ok": True, **view_state(sid, state)})

        return self._err("未知接口", 404)


class Server(ThreadingHTTPServer):
    # Windows 上 allow_reuse_address=1 会让「端口已被占用」也绑定成功，
    # 结果请求全被别的程序接走。关掉它，占用时直接抛 OSError。
    allow_reuse_address = False
    daemon_threads = True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KPL BP 模拟器本地服务")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args(argv)

    if not os.path.exists(DB_PATH):
        print(f"没有数据库 {DB_PATH}，先跑：python -m kplbp.ingest", file=sys.stderr)
        return 2

    try:
        srv = Server((args.host, args.port), Handler)
    except OSError as e:
        print(f"端口 {args.port} 用不了（{e}）。换个端口：--port 8781", file=sys.stderr)
        return 3

    print(f"KPL BP 模拟器： http://{args.host}:{args.port}/", flush=True)
    print(f"数据库：{DB_PATH}  赛季：{season_id()}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
