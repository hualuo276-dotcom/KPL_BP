"""BP 规则。

**必须先从真实数据里核对，别照记忆写。** 下面这张 20 手表是从官方
`prod.comp.smoba.qq.com/leaguesite/battle/open` 的 `bp_list` 逐手数出来的
（`camp` 1=蓝 2=红，`is_ban_or_pick` 1=pick 0=ban）：

    1 蓝 ban   2 红 ban   3 蓝 ban   4 红 ban
    5 蓝 pick  6 红 pick  7 红 pick  8 蓝 pick  9 蓝 pick  10 红 pick
    11 红 ban  12 蓝 ban  13 红 ban  14 蓝 ban  15 红 ban  16 蓝 ban
    17 红 pick 18 蓝 pick 19 蓝 pick 20 红 pick

合计：蓝 5 ban / 5 pick，红 5 ban / 5 pick，**共 20 手**。

⚠ 之前按记忆写的 8 ban / 18 手（第二轮 ban 只有 4 个）是错的。
`scripts/verify_rules.py` 会拿库里的 `bp_order` 去逐手比对，别再凭印象改。

老赛季（2020 前后）是 8 ban，所以这里留了两张表，用 `ban_slots` 切。
"""
from __future__ import annotations

# (side, action)  side: "B" = 蓝方, "R" = 红方
KPL_10BAN: list[tuple[str, str]] = [
    # 第一轮 ban（4）
    ("B", "ban"),
    ("R", "ban"),
    ("B", "ban"),
    ("R", "ban"),
    # 第一轮 pick：蓝1 红2 蓝2 红1（6）
    ("B", "pick"),
    ("R", "pick"),
    ("R", "pick"),
    ("B", "pick"),
    ("B", "pick"),
    ("R", "pick"),
    # 第二轮 ban：红先，共 6（红蓝交替各 3）
    ("R", "ban"),
    ("B", "ban"),
    ("R", "ban"),
    ("B", "ban"),
    ("R", "ban"),
    ("B", "ban"),
    # 第二轮 pick：红1 蓝2 红1（4）
    ("R", "pick"),
    ("B", "pick"),
    ("B", "pick"),
    ("R", "pick"),
]

# 老赛制：8 ban / 18 手
KPL_8BAN: list[tuple[str, str]] = [
    ("B", "ban"),
    ("R", "ban"),
    ("B", "ban"),
    ("R", "ban"),
    ("B", "pick"),
    ("R", "pick"),
    ("R", "pick"),
    ("B", "pick"),
    ("B", "pick"),
    ("R", "pick"),
    ("R", "ban"),
    ("B", "ban"),
    ("R", "ban"),
    ("B", "ban"),
    ("R", "pick"),
    ("B", "pick"),
    ("B", "pick"),
    ("R", "pick"),
]

# 巅峰对决：盲选 5 手，无 ban
KPL_BLIND: list[tuple[str, str]] = [("B", "pick")] * 5 + [("R", "pick")] * 5

DEFAULT_CONFIG = {
    "ban_slots": 10,        # 每队 ban 数（现行 KPL = 5，老赛制 = 4）
    # ⚠ 默认关。2025–2026 六个赛季实测：BO5 有 483/492 场出现「同一英雄跨局重复」，
    # BO7 是 88/88。也就是说**现行 KPL 是每局独立 BP，不是全局 BP**。
    # 世冠 / 挑战者杯 / 自定义赛事要开的话，客户端传 config.global_bp = true。
    "global_bp": False,
    "lock_bans": False,     # 全局 BP 开启时，被 ban 的英雄是否也进池
    "blind_last": True,     # BO7 第七局走巅峰对决
    "second_ban_red_first": True,  # 第二轮 ban 红方先
}


def base_steps(config: dict | None = None) -> list[tuple[str, str]]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    return list(KPL_10BAN if int(cfg.get("ban_slots", 10)) >= 10 else KPL_8BAN)


def steps_for(game: int, bo: int, config: dict | None = None) -> list[tuple[str, str]]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    if cfg["blind_last"] and bo >= 7 and game == bo:
        return list(KPL_BLIND)
    steps = base_steps(cfg)
    if not cfg["second_ban_red_first"]:
        # 第二轮 ban 改蓝方先：把第二轮 ban 那一段的 B/R 对调
        for i, (side, action) in enumerate(steps):
            if action == "ban" and i > 4:
                steps[i] = ("R" if side == "B" else "B", action)
    return steps


def is_blind(game: int, bo: int, config: dict | None = None) -> bool:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    return bool(cfg["blind_last"] and bo >= 7 and game == bo)


def validate(steps: list[tuple[str, str]]) -> dict:
    """自检：两边 ban / pick 数必须相等，且 pick 各 5。"""
    stat = {"B": {"ban": 0, "pick": 0}, "R": {"ban": 0, "pick": 0}}
    for side, action in steps:
        stat[side][action] += 1
    ok = (
        stat["B"]["ban"] == stat["R"]["ban"]
        and stat["B"]["pick"] == 5
        and stat["R"]["pick"] == 5
    )
    return {"ok": ok, "stat": stat, "steps": len(steps)}
