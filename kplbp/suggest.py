"""BP 推荐。

输入来自真实数据：
  * 英雄强度 = 本赛季出场次数 + 胜率
  * 对手威胁 = 对手战队英雄池里该英雄的场次与胜率（v_player_hero 聚合）
  * 我方熟练 = 我方战队该英雄的场次与胜率
  * 阵容缺口 = 已选英雄的分路覆盖
  * 对位克制 = 同分路 1v1 胜负（counter.py）
  * 全局前瞻 = 模拟剩余出手（planner.py）

强度用出场次数 + 胜率，不用 presence（出场 + 被 ban）。presence 含 ban 次数，
会让被 ban 多的英雄排名靠前，形成循环：被 ban 多 → presence 高 → 更推荐 ban → 被 ban 更多。

所有分数落在 0~1，方便前端画条，也方便调整权重。
"""
from __future__ import annotations

from . import counter as counter_mod
from . import engine, planner

LANE_NAME = {1: "对抗路", 2: "中路", 3: "发育路", 4: "打野", 5: "游走"}

# 各项权重加起来必须 = 1，否则分数会跑出 0~1
W_BAN = {"strong": 0.45, "threat": 0.35, "counter": 0.20}
W_PICK = {"strong": 0.35, "mastery": 0.22, "need": 0.18, "counter": 0.12, "plan": 0.13}

NEUTRAL = counter_mod.NEUTRAL


def _norm(pairs: list[tuple[int, float]]) -> dict[int, float]:
    """min-max 归一到 0~1。全相等时给 0.5。"""
    if not pairs:
        return {}
    vals = [v for _, v in pairs]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return {k: 0.5 for k, _ in pairs}
    return {k: (v - lo) / (hi - lo) for k, v in pairs}


def _strength(cards: list[dict]) -> dict[int, float]:
    """英雄强度：真实出场次数 + 胜率。

    故意**不用 presence** —— presence 含 ban 次数，会让「被 ban 最多的英雄」
    永远显得最强，形成自我强化。出场次数才是「别人愿意选它」的证据。
    """
    picks = _norm([(c["hero_id"], c.get("picks") or 0) for c in cards])
    wr_raw = []
    for c in cards:
        wr = c.get("pick_wr") if c.get("pick_wr") is not None else c.get("rank_wr")
        wr_raw.append((c["hero_id"], float(wr or 0.0)))
    wr = _norm(wr_raw)
    return {c["hero_id"]: 0.5 * picks.get(c["hero_id"], 0) + 0.5 * wr.get(c["hero_id"], 0) for c in cards}


def _pool_signal(pool: dict[int, dict], cards: list[dict]) -> dict[int, float]:
    """战队英雄池 → 0~1 威胁/熟练度。场次和胜率各占一半。"""
    if not pool:
        return {}
    games = _norm([(h, p["games"]) for h, p in pool.items()])
    wr = _norm([(h, (p.get("wr") or 0.0)) for h, p in pool.items()])
    return {h: 0.6 * games.get(h, 0) + 0.4 * wr.get(h, 0) for h in pool}


def suggest(
    state: dict,
    cards: list[dict],
    own_pool: dict[int, dict] | None = None,
    opp_pool: dict[int, dict] | None = None,
    limit: int = 10,
    matchup_index: dict[tuple[int, int], float] | None = None,
) -> list[dict]:
    """给当前这一手（ban 或 pick）打分排序。"""
    step = engine.current_step(state)
    if step is None:
        return []
    side, action = step
    other = "R" if side == "B" else "B"

    by_id = {c["hero_id"]: c for c in cards}
    strength = _strength(cards)
    own = _pool_signal(own_pool or {}, cards)
    opp = _pool_signal(opp_pool or {}, cards)
    idx = matchup_index or {}

    g = engine.current_game(state)
    my_picks = g["picks"][side]
    enemy_picks = g["picks"][other]

    have_lanes = {by_id[h]["lane"] for h in my_picks if h in by_id}
    missing = {ln for ln in (1, 2, 3, 4, 5) if ln not in have_lanes}

    # ban 阶段看「谁会克我方已选」，pick 阶段看「谁能克对面已选」
    against = my_picks if action == "ban" else enemy_picks

    out: list[dict] = []
    raw: list[dict] = []
    for hid, c in by_id.items():
        if engine.hero_state(state, hid) != "available":
            continue

        lane = c.get("lane") or 0
        cw, cn = counter_mod.evaluate(hid, against, idx) if idx and against else (None, 0)
        raw.append(
            {
                "hid": hid,
                "c": c,
                "lane": lane,
                "s_strong": strength.get(hid, 0.0),
                "s_own": own.get(hid, 0.0),
                "s_opp": opp.get(hid, 0.0),
                "s_need": 1.0 if lane in missing else (0.45 if missing else 0.15),
                "cw": cw,
                "cn": cn,
            }
        )

    # 对位信号：用收缩后的胜率，再按样本量给置信度。
    # 不用 min-max 归一 —— 那会把「2 局全胜」拉到接近 1，比不收缩还危险。
    for x in raw:
        if x["cw"] is None:
            x["s_counter"] = NEUTRAL
        else:
            conf = min(1.0, x["cn"] / 20.0)          # 20 局对位算满信
            x["s_counter"] = max(
                0.0, min(1.0, NEUTRAL + (x["cw"] - NEUTRAL) * 1.5 * conf)
            )

    # 前瞻：只对分最高的前 N 个候选做模拟，其余给 0（它们本来就排在后面）
    plan_vals: dict[int, float] = {}
    if action == "pick" and raw:
        prelim = sorted(
            raw,
            key=lambda x: -(
                W_PICK["strong"] * x["s_strong"]
                + W_PICK["mastery"] * x["s_own"]
                + W_PICK["need"] * x["s_need"]
                + W_PICK["counter"] * x["s_counter"]
            ),
        )[: planner.MAX_SIMULATED]
        plan_vals = planner.lookahead(
            state, cards, side, strength, top_ids=[x["hid"] for x in prelim]
        )

    # 第一轮 ban 时我方还没选人，对位信号整个用不上；
    # 这时还留 0.20 权重在常数 0.5 上，会把所有候选压成一个窄带（70~54 那种）。
    # 把这份权重让给强度与威胁，区分度才回来。
    w_ban = dict(W_BAN)
    if action == "ban":
        if not against:
            w_ban["strong"] += W_BAN["counter"] * 0.6
            w_ban["threat"] += W_BAN["counter"] * 0.4
            w_ban["counter"] = 0.0
        elif not any(x["cw"] is not None for x in raw):
            w_ban["strong"] += W_BAN["counter"] * 0.6
            w_ban["threat"] += W_BAN["counter"] * 0.4
            w_ban["counter"] = 0.0

    for x in raw:
        hid, c = x["hid"], x["c"]
        s_strong, s_own, s_opp = x["s_strong"], x["s_own"], x["s_opp"]
        s_need, s_counter, cn = x["s_need"], x["s_counter"], x["cn"]
        s_plan = plan_vals.get(hid, 0.0) if plan_vals else NEUTRAL
        lane = x["lane"]

        reasons: list[str] = []
        if action == "ban":
            score = (
                w_ban["strong"] * s_strong
                + w_ban["threat"] * s_opp
                + w_ban["counter"] * s_counter
            )
            # 对面**在场上**的选手到底用不用这个英雄——数据来自 opp_pool，
            # 不是 hero_rank 里那个全联盟榜单（那个跟本场对手没关系）
            node = (opp_pool or {}).get(hid)
            if node and node.get("players") and node.get("games", 0) >= 3:
                top = node["players"][0]
                short = str(top.get("name", "")).split(".")[-1]
                games_n = int(node["games"])
                tag = f"对面 {short} {games_n} 场"
                wr_pool = node.get("wr")
                # 3~4 场就报胜率纯属噪音，够 5 场再带百分比
                if wr_pool is not None and games_n >= 5:
                    tag += f" {wr_pool * 100:.0f}%"
                reasons.append(tag)
            elif s_opp > 0.6:
                reasons.append("对手体系常用")
            if cn >= 8 and s_counter >= 0.58:
                reasons.append(f"克我方已选（{cn:.0f} 局对位）")
        else:
            score = (
                W_PICK["strong"] * s_strong
                + W_PICK["mastery"] * s_own
                + W_PICK["need"] * s_need
                + W_PICK["counter"] * s_counter
                + W_PICK["plan"] * s_plan
            )
            remaining = 133 - len(state["locked"]) - sum(len(v) for v in g["bans"].values())
            if lane in missing:
                reasons.append(f"补 {LANE_NAME.get(lane, '?')}")
            if s_own > 0.6:
                node = (own_pool or {}).get(hid)
                short = ""
                if node and node.get("players"):
                    short = " " + str(node["players"][0].get("name", "")).split(".")[-1]
                reasons.append(f"我方常用{short}")
            if cn >= 8 and s_counter >= 0.58:
                reasons.append(f"克对面已选（{cn:.0f} 局对位）")
            if state["config"].get("global_bp") and s_plan >= 0.85 and remaining <= 60:
                reasons.append("先手最划算")

        # 出场和被 ban 分开写清楚：出场才是「有人愿意选」，被 ban 是「有人怕它」，
        # 两者混成一个 presence 数字会误导（原来那版就把 193 = 57+136 叫成「出场」）
        bits = []
        picks_n = c.get("picks") or 0
        bans_n = c.get("bans") or 0
        if picks_n:
            bits.append(f"出场 {picks_n}")
        if bans_n >= 20:
            bits.append(f"被 ban {bans_n}")
        if bits:
            reasons.append(" · ".join(bits))

        wr = c.get("pick_wr") if c.get("pick_wr") is not None else c.get("rank_wr")
        if wr and float(wr) >= 0.55:
            reasons.append(f"胜率 {float(wr) * 100:.0f}%")

        out.append(
            {
                "hero_id": hid,
                "cname": c["cname"],
                "title": c.get("title") or "",
                "lane": lane,
                "lane_name": LANE_NAME.get(lane, ""),
                "score": round(min(1.0, max(0.0, score)), 4),
                "detail": {
                    "strength": round(s_strong, 3),
                    "own": round(s_own, 3),
                    "opp": round(s_opp, 3),
                    "need": round(s_need, 3),
                    "counter": round(s_counter, 3),
                    "counter_raw": None if x["cw"] is None else round(x["cw"], 3),
                    "counter_samples": cn,
                    "plan": round(s_plan, 3),
                    "presence": c.get("presence") or 0,
                    "pick_wr": c.get("pick_wr"),
                    "rank_wr": c.get("rank_wr"),
                },
                "reasons": reasons[:3],
            }
        )

    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def lane_coverage(state: dict, cards: list[dict], side: str) -> dict:
    """我方已选英雄的分路覆盖情况，前端画柱子用。"""
    by_id = {c["hero_id"]: c for c in cards}
    g = engine.current_game(state)
    picked = [by_id[h] for h in g["picks"][side] if h in by_id]
    covered = {}
    for h in picked:
        covered.setdefault(h.get("lane") or 0, []).append(h["cname"])
    return {
        "covered": [{"lane": k, "lane_name": LANE_NAME.get(k, ""), "heroes": v} for k, v in sorted(covered.items())],
        "missing": [
            {"lane": ln, "lane_name": LANE_NAME[ln]} for ln in (1, 2, 3, 4, 5) if ln not in covered
        ],
    }
