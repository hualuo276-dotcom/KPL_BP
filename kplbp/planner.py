"""全局 BP 前瞻（一层搜索）。

单局贪心只回答「这一手谁强」；全局 BP 真正的问题是
「现在拿它，会不会把后面几手该拿的抢没了」。

做法：对每个候选 X，假设我拿 X，然后双方按剩余出手顺序轮流拿，
各自都拿「当前最强且补得上分路」的那个，最后算我方阵容总强度。
不是 Minimax（没有对抗性搜索），只是 **1 层前瞻 + 双方贪心**——
成本低、可解释，对 BP 够用。

参考：同类项目（Andndre/hok-draft-engine）用的是 Iterative Deepening Minimax
（深度 1~4、宽度 8、1.5s 预算，跑在 Web Worker 里）。这里没到那个量级，
但把「前瞻比贪心好」这个结论拿过来了。
"""
from __future__ import annotations

from . import engine

LANE_NEED_BONUS = 0.35
MAX_SIMULATED = 30  # 只对分最高的 N 个候选做前瞻，其余按 0 处理，省算力


def _value(card: dict, strength: float, missing: set[int]) -> float:
    lane = card.get("lane") or 0
    return strength + (LANE_NEED_BONUS if lane in missing else 0.0)


def _take_best(order: list[tuple[int, float]], taken: set[int]) -> int | None:
    for hid, _v in order:
        if hid not in taken:
            return hid
    return None


def lookahead(
    state: dict,
    cards: list[dict],
    side: str,
    strength: dict[int, float],
    top_ids: list[int] | None = None,
) -> dict[int, float]:
    """返回 {hero_id: 0~1 的前瞻价值}。不做前瞻时返回空 dict。"""
    if not state["config"].get("global_bp"):
        return {}

    g = engine.current_game(state)
    all_steps = engine.steps(state)
    remain = all_steps[state["step"] + 1 :]
    pick_steps = [s for s, a in remain if a == "pick"]
    if not pick_steps:
        return {}

    by_id = {c["hero_id"]: c for c in cards}
    taken: set[int] = set(state["locked"])
    taken.update(g["bans"]["B"] + g["bans"]["R"] + g["picks"]["B"] + g["picks"]["R"])

    order = sorted(
        ((hid, strength.get(hid, 0.0)) for hid in by_id if hid not in taken),
        key=lambda x: -x[1],
    )

    lanes_of = {hid: (by_id[hid].get("lane") or 0) for hid in by_id if hid in by_id}
    my_lanes = {lanes_of.get(h, 0) for h in g["picks"][side]}
    my_missing = {ln for ln in (1, 2, 3, 4, 5) if ln not in my_lanes}

    cand = top_ids if top_ids is not None else [hid for hid, _ in order]

    out: dict[int, float] = {}
    for hid in cand:
        if hid in taken:
            continue
        sim_taken = set(taken)
        sim_taken.add(hid)
        my_total = _value(by_id[hid], strength.get(hid, 0.0), my_missing) if hid in by_id else 0.0

        for s in pick_steps:
            pick = _take_best(order, sim_taken)
            if pick is None:
                break
            sim_taken.add(pick)
            if s == side:
                my_total += _value(by_id[pick], strength.get(pick, 0.0), my_missing)
        out[hid] = my_total

    if not out:
        return {}
    lo, hi = min(out.values()), max(out.values())
    if hi - lo < 1e-9:
        return {k: 0.5 for k in out}
    return {k: (v - lo) / (hi - lo) for k, v in out.items()}
