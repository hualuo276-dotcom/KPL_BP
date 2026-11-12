"""数据源层。

两个来源，都已实测可用（2026-11）：

1. 英雄库   https://pvp.qq.com/web201605/js/herolist.json
            133 条，GBK 编码，字段 ename / cname / title / hero_type / roles

2. KPL 官方 kplow 接口
            https://kplshop-op.timi-esports.qq.com/kplow/<endpoint>
            POST, application/x-www-form-urlencoded
            返回 **不是 JSON**，是 PHP 风格的扁平 querystring：
                data.list%5B0%5D.hero_id=136&data.list%5B0%5D.hero_name=...
            键本身被 urlencode 过，所以解析顺序必须是：
                先 parse_qsl（解出 data.list[0].hero_id）→ 再按 [n] 建树
"""
from __future__ import annotations

import json
import random
import re
import ssl
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_IDX_RE = re.compile(r"\[([^\]]*)\]")

HERO_LIST_URL = "https://pvp.qq.com/web201605/js/herolist.json"
KPL_BASE = "https://kplshop-op.timi-esports.qq.com/kplow"

# 第二套官方接口：赛事数据中心（pvp.qq.com/matchdata）自己用的那套。
# 它比 kplow 多给两样关键东西：**ban 位**和**完整 BP 顺序**。
SMOBA_BASE = "https://prod.comp.smoba.qq.com/leaguesite"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class SourceError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# 传输
# --------------------------------------------------------------------------- #
def _http(
    url: str,
    data: dict | None = None,
    timeout: int = 30,
    retries: int = 3,
    headers: dict | None = None,
) -> bytes:
    hdrs = {
        "User-Agent": UA,
        "Referer": "https://kpl.qq.com/",
        "Origin": "https://kpl.qq.com",
    }
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"

    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - 网络层统一重试
            last = e
            time.sleep(0.8 * (attempt + 1) + random.random() * 0.4)
    raise SourceError(f"{url} 请求失败：{last}")


def kpl_call(endpoint: str, **params: Any) -> str:
    """调用 kplow 接口，返回原始文本。endpoint 不带前导斜杠。"""
    raw = _http(f"{KPL_BASE}/{endpoint.lstrip('/')}", params or {})
    return raw.decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# 解析：扁平 querystring → 嵌套 dict / list
# --------------------------------------------------------------------------- #
def _tokenize(key: str) -> list[str]:
    """'data.list[0].hero_id' → ['data', 'list', '0', 'hero_id']"""
    out: list[str] = []
    for chunk in key.split("."):
        base, _, rest = chunk.partition("[")
        if base:
            out.append(base)
        for idx in _IDX_RE.findall("[" + rest if rest else ""):
            out.append(idx)
    return [t for t in out if t != ""]


def _insert(root: dict, tokens: list[str], value: Any) -> None:
    cur: Any = root
    for i, tok in enumerate(tokens[:-1]):
        nxt = tokens[i + 1]
        want_list = nxt.isdigit()
        if isinstance(cur, list):
            idx = int(tok) if tok.isdigit() else 0
            while len(cur) <= idx:
                cur.append(None)
            if cur[idx] is None:
                cur[idx] = [] if want_list else {}
            cur = cur[idx]
        else:
            if tok not in cur or cur[tok] is None:
                cur[tok] = [] if want_list else {}
            cur = cur[tok]

    last = tokens[-1]
    if isinstance(cur, list):
        idx = int(last) if last.isdigit() else 0
        while len(cur) <= idx:
            cur.append(None)
        cur[idx] = value
    else:
        cur[last] = value


def parse_kv(text: str) -> dict:
    """把 kplow 的 php-querystring 解析成嵌套结构。"""
    root: dict = {}
    for key, value in urllib.parse.parse_qsl(text, keep_blank_values=True):
        tokens = _tokenize(key)
        if tokens:
            _insert(root, tokens, value)
    return root


def _clean(node: Any) -> Any:
    """去掉稀疏数组里的 None。"""
    if isinstance(node, list):
        return [_clean(x) for x in node if x is not None]
    if isinstance(node, dict):
        return {k: _clean(v) for k, v in node.items() if v is not None}
    return node


def kpl_json(endpoint: str, **params: Any) -> dict:
    text = kpl_call(endpoint, **params)
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    return json.loads(json.dumps(_clean(parse_kv(text)), ensure_ascii=False))


# --------------------------------------------------------------------------- #
# 第二套接口：prod.comp.smoba.qq.com（带 ban / BP 顺序）
# --------------------------------------------------------------------------- #
def smoba_json(path: str, **params: Any) -> dict:
    """smoba 接口返回 {message, code, request_id, results|data}。

    注意两种信封都出现过：列表接口用 `results`，battle 详情用 `data`。
    """
    raw = _http(f"{SMOBA_BASE}{path}", params or None, headers={"Referer": "https://pvp.qq.com/matchdata/"})
    body = json.loads(raw.decode("utf-8", "replace"))
    if body.get("code") not in (200, 0, None):
        raise SourceError(f"{path} 返回 code={body.get('code')} {body.get('message')}")
    node = body.get("results")
    if node is None:
        node = body.get("data")
    return node if node is not None else body


def fetch_smoba_leagues() -> list[dict]:
    return smoba_json("/leagues/open") or []


def fetch_smoba_matches(league_id: str) -> list[dict]:
    return smoba_json("/matches/open", league_id=league_id) or []


def fetch_smoba_battle(battle_id: str) -> dict:
    """单局详情。data.bp_list 是**按顺序**的 BP 动作：

        {"camp": 1|2, "is_ban_or_pick": 0|1, "hero_id": 135, "position": 3}

    实测语义：**is_ban_or_pick == 1 是 pick，0 是 ban**（用 battle_player_list
    的 10 个英雄去对，bp_list 里 flag=1 的正好就是它们）。
    """
    return smoba_json("/battle/open", battle_id=battle_id) or {}


def parse_bp_list(battle: dict) -> list[dict]:
    """把 bp_list 规范化成 [{seq, camp, is_pick, hero_id, position}]。"""
    out = []
    for i, a in enumerate(battle.get("bp_list") or [], 1):
        hid = _to_int(a.get("hero_id"))
        if not hid:
            continue
        out.append(
            {
                "seq": i,
                "camp": _to_int(a.get("camp")),
                "is_pick": 1 if _to_int(a.get("is_ban_or_pick")) == 1 else 0,
                "hero_id": hid,
                "position": _to_int(a.get("position")),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# 具体接口
# --------------------------------------------------------------------------- #
def fetch_heroes() -> list[dict]:
    """英雄库。返回 [{ename, cname, title, id_name, hero_type, roles}]"""
    raw = _http(HERO_LIST_URL)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gbk", "replace")
    data = json.loads(text)
    out = []
    for h in data:
        out.append(
            {
                "hero_id": int(h.get("ename") or 0),
                "cname": (h.get("cname") or "").strip(),
                "title": (h.get("title") or "").strip(),
                "id_name": (h.get("id_name") or "").strip(),
                "hero_type": int(h.get("hero_type") or 0),
                "roles": (h.get("roles") or "").strip(),
            }
        )
    return [h for h in out if h["hero_id"] and h["cname"]]


def fetch_seasons() -> list[dict]:
    d = kpl_json("getSeasonAndStageAndTeamList", seasonid="")
    seasons = d.get("data", {}).get("seasons", []) or []
    out = []
    for s in seasons:
        out.append(
            {
                "seasonid": s.get("seasonid", ""),
                "name": s.get("season_name", ""),
                "year": _to_int(s.get("season_year")),
                "start_ts": _to_int(s.get("start_time")),
                "end_ts": _to_int(s.get("end_time")),
                "is_cur": _to_int(s.get("is_cur_season")),
            }
        )
    return [s for s in out if s["seasonid"]]


def fetch_teams(seasonid: str) -> list[dict]:
    d = kpl_json("getTeamsList", seasonid=seasonid)
    rows = d.get("data") or []
    out = []
    for t in rows:
        if not isinstance(t, dict):
            continue
        team_id = t.get("team_id") or t.get("teamid") or ""
        if not team_id:
            continue
        players = []
        for p in t.get("player_list") or []:
            if not isinstance(p, dict):
                continue
            players.append(
                {
                    "playerid": str(p.get("playerid", "")),
                    "name": p.get("player_name", ""),
                    "position": _to_int(p.get("position")),
                    "avatar": p.get("avatar", ""),
                }
            )
        out.append(
            {
                "team_id": team_id,
                "name": t.get("team_name") or t.get("name") or team_id,
                "logo": t.get("img") or t.get("team_logo") or "",
                "players": players,
            }
        )
    return out


def fetch_schedule(seasonid: str, max_pages: int = 12) -> list[dict]:
    """赛程列表。pagesize 服务端会忽略，按 page 递增 + schedule_id 去重收敛。"""
    seen: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        d = kpl_json(
            "getScheduleList",
            seasonid=seasonid,
            stageid="",
            page=str(page),
            pagesize="50",
        )
        rows = d.get("data", {}).get("list") or []
        new = 0
        for r in rows:
            if not isinstance(r, dict):
                continue
            sid = r.get("scheduleid")
            if not sid or sid in seen:
                continue
            seen[sid] = r
            new += 1
        if new == 0:
            break
    return list(seen.values())


def fetch_schedule_detail(schedule_id: str, seasonid: str) -> dict:
    """单场详情。

    参数名必须是 scheduleid **并且带上 seasonid**：
      * scheduleid 不带 seasonid → {"result":10020003,"msg":"schedule not found"}
      * 用 schedule_id / id / matchid 等名字 → 服务端直接忽略，返回「最近一场」的明细
        （这个坑很隐蔽：内容看着有数据，但每场都一模一样）
    """
    return kpl_json("getScheduleDetail", scheduleid=schedule_id, seasonid=seasonid)


def fetch_hero_rank(seasonid: str) -> list[dict]:
    d = kpl_json("getHeroRankList", seasonid=seasonid)
    rows = d.get("data", {}).get("list") or []
    out = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("hero_id"):
            continue
        out.append(
            {
                "hero_id": _to_int(r.get("hero_id")),
                "match_count": _to_int(r.get("match_count")),
                "win_cr": _to_float(r.get("win_cr")),
                "position": _to_int(r.get("position")),
                "best_player": r.get("player_name", ""),
                "best_player_win_cr": _to_float(r.get("best_player_win_cr")),
            }
        )
    return out


def fetch_hero_combo(seasonid: str) -> list[dict]:
    """英雄组合数据（部分赛季为空，容错返回 []）。"""
    try:
        d = kpl_json("getSeasonHeroCombo", seasonid=seasonid)
    except SourceError:
        return []
    rows = d.get("data", {}).get("list") or d.get("data") or []
    out = []
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict):
                out.append(r)
    return out


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def new_uuid() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(16))
