"""BP 状态机。

纯逻辑、无 IO：状态本身就是一个可 JSON 序列化的 dict，
方便存盘、回放、和前端来回传。
"""
from __future__ import annotations

from typing import Any

from . import rules


class BpError(ValueError):
    pass


SIDE_NAME = {"B": "蓝方", "R": "红方"}


def new_state(bo: int = 5, blue_team: str = "", red_team: str = "", config: dict | None = None) -> dict:
    cfg = {**rules.DEFAULT_CONFIG, **(config or {})}
    return {
        "bo": max(1, int(bo)),
        "blue_team": blue_team,
        "red_team": red_team,
        "config": cfg,
        "game": 1,
        "step": 0,
        "locked": [],
        "games": [_empty_game(1, bo, cfg)],
        "log": [],
    }


def _empty_game(game: int, bo: int, cfg: dict) -> dict:
    return {
        "round": game,
        "blind": rules.is_blind(game, bo, cfg),
        "bans": {"B": [], "R": []},
        "picks": {"B": [], "R": []},
        "winner": None,
        "steps": len(rules.steps_for(game, bo, cfg)),
    }


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def steps(state: dict) -> list[tuple[str, str]]:
    """当前小局的出手顺序。

    复盘模式（drill）会塞 `custom_steps`，直接照真实对局的顺序走，
    这样巅峰对决那种 10 手盲选也能原样复现，不用为它另写一套引擎。
    """
    custom = state.get("custom_steps")
    if custom:
        return [(a["side"], a["action"]) for a in custom]
    return rules.steps_for(state["game"], state["bo"], state["config"])


def current_step(state: dict) -> tuple[str, str] | None:
    st = steps(state)
    if state["step"] >= len(st):
        return None
    return st[state["step"]]


def current_game(state: dict) -> dict:
    return state["games"][state["game"] - 1]


def hero_state(state: dict, hero_id: int) -> str:
    """available / picked / banned / locked

    巅峰对决（盲选局）不受全局 BP 限制：之前用过的英雄可以再选，
    所以盲选局里跳过 locked 判断。
    """
    g = current_game(state)
    if not g["blind"] and hero_id in set(state["locked"]):
        return "locked"
    if hero_id in g["picks"]["B"] or hero_id in g["picks"]["R"]:
        return "picked"
    if hero_id in g["bans"]["B"] or hero_id in g["bans"]["R"]:
        return "banned"
    return "available"


def game_finished(state: dict) -> bool:
    return state["step"] >= len(steps(state))


def match_finished(state: dict) -> bool:
    need = state["bo"] // 2 + 1
    wins_b = sum(1 for g in state["games"] if g["winner"] == "B")
    wins_r = sum(1 for g in state["games"] if g["winner"] == "R")
    return wins_b >= need or wins_r >= need


def score(state: dict) -> tuple[int, int]:
    wins_b = sum(1 for g in state["games"] if g["winner"] == "B")
    wins_r = sum(1 for g in state["games"] if g["winner"] == "R")
    return wins_b, wins_r


# --------------------------------------------------------------------------- #
# 动作
# --------------------------------------------------------------------------- #
def act(state: dict, hero_id: int, side: str | None = None) -> dict:
    """落一手 ban / pick。side 传 None 时按当前步自动判定。"""
    if match_finished(state):
        raise BpError("比赛已结束")
    if game_finished(state):
        raise BpError("本小局 BP 已结束，请先记录胜方")

    hero_id = int(hero_id)
    side_now, action = current_step(state)  # type: ignore[misc]
    if side is not None and side != side_now:
        raise BpError(f"现在轮到{SIDE_NAME[side_now]}（{side_now}）的 {action}")

    st = hero_state(state, hero_id)
    if st != "available":
        raise BpError(f"英雄 {hero_id} 当前状态为 {st}，不可选")

    g = current_game(state)
    bucket = g["picks"] if action == "pick" else g["bans"]
    bucket[side_now].append(hero_id)
    state["log"].append(
        {"game": state["game"], "step": state["step"] + 1, "side": side_now, "action": action, "hero_id": hero_id}
    )
    state["step"] += 1

    if game_finished(state):
        _finalize_game(state, g)
    return state


def _finalize_game(state: dict, g: dict) -> None:
    cfg = state["config"]
    if not cfg.get("global_bp", True):
        return
    if g["blind"]:
        return  # 巅峰对决不进全局池
    locked = set(state["locked"])
    locked.update(g["picks"]["B"])
    locked.update(g["picks"]["R"])
    if cfg.get("lock_bans"):
        locked.update(g["bans"]["B"])
        locked.update(g["bans"]["R"])
    state["locked"] = sorted(locked)


def set_winner(state: dict, side: str) -> dict:
    """记录本小局胜方；够 BO 条件则结束比赛，否则开下一局。"""
    if not game_finished(state):
        left = len(steps(state)) - state["step"]
        raise BpError(f"本小局还有 {left} 手没走完")
    if side not in ("B", "R"):
        raise BpError("胜方只能是 B 或 R")
    g = current_game(state)
    if g["winner"]:
        raise BpError("本小局已记录胜方")
    g["winner"] = side

    if match_finished(state):
        return state

    state["game"] += 1
    state["step"] = 0
    state["games"].append(_empty_game(state["game"], state["bo"], state["config"]))
    return state


def undo(state: dict) -> dict:
    """撤销上一手。已记录胜方的小局不能撤。"""
    if not state["log"]:
        raise BpError("没有可撤销的操作")
    last = state["log"][-1]
    g = state["games"][last["game"] - 1]
    if g["winner"]:
        raise BpError("该小局已记录胜方，无法撤销")
    if last["game"] != state["game"]:
        raise BpError("只能撤销当前小局的操作")

    bucket = g["picks"] if last["action"] == "pick" else g["bans"]
    bucket[last["side"]].remove(last["hero_id"])
    state["log"].pop()
    state["step"] -= 1
    # 撤销后本局又变成未完成，重新计算全局池
    _recompute_locked(state)
    return state


def _recompute_locked(state: dict) -> None:
    cfg = state["config"]
    locked: set[int] = set()
    if not cfg.get("global_bp", True):
        state["locked"] = []
        return
    for g in state["games"]:
        if g["winner"] is None or g["blind"]:
            continue
        locked.update(g["picks"]["B"])
        locked.update(g["picks"]["R"])
        if cfg.get("lock_bans"):
            locked.update(g["bans"]["B"])
            locked.update(g["bans"]["R"])
    state["locked"] = sorted(locked)


# --------------------------------------------------------------------------- #
# 视图
# --------------------------------------------------------------------------- #
def summary(state: dict) -> dict:
    cur = current_step(state)
    wins_b, wins_r = score(state)
    st = steps(state)
    return {
        "bo": state["bo"],
        "game": state["game"],
        "step": state["step"],
        "total_steps": len(st),
        "blind": current_game(state)["blind"],
        "step_table": [{"side": s, "action": a} for s, a in st],
        "turn": None
        if cur is None
        else {"side": cur[0], "side_name": SIDE_NAME[cur[0]], "action": cur[1]},
        "score": {"B": wins_b, "R": wins_r},
        "blue_team": state["blue_team"],
        "red_team": state["red_team"],
        "game_finished": game_finished(state),
        "match_finished": match_finished(state),
        "locked_count": len(state["locked"]),
        "games": state["games"],
    }
