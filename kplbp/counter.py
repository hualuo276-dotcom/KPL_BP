"""对位克制。

数据来自真实对局：每小局里蓝红双方同分路的两个英雄互为对位，
按胜负各计一次，跨赛季汇总。

不用硬门槛的原因：133 个英雄 × 5 个分路，可能的 pair 有几千个，
而样本只有一万多场对位，绝大多数 pair 只有 2~8 场。
卡「至少 6 场」会丢掉大半数据；直接信「3 场 3:0」又会被巧合带偏。

所以用贝叶斯收缩：

    wr' = (w + k·0.5) / (g + k)

同样全胜，3 场得 0.67，30 场得 0.94。样本越少越靠近 0.5。
k 越大越保守，默认 6。
"""
from __future__ import annotations

DEFAULT_K = 6.0
NEUTRAL = 0.5


def shrunk(games: int, wins: int, k: float = DEFAULT_K, prior: float = NEUTRAL) -> float:
    """拉向 0.5 的对位胜率。"""
    if games <= 0:
        return prior
    return (wins + k * prior) / (games + k)


def evaluate(
    cand: int,
    against: list[int],
    index: dict[tuple[int, int], tuple[int, int]],
    k: float = DEFAULT_K,
) -> tuple[float | None, int]:
    """cand 面对 against 里每个英雄的对位胜率（已收缩）均值。

    返回 (均值, 用到的对局总数)。一组都取不到时返回 (None, 0)。
    """
    vals: list[float] = []
    total = 0
    for h in against:
        pair = index.get((cand, h))
        if not pair:
            continue
        g, w = pair
        vals.append(shrunk(g, w, k))
        total += g
    if not vals:
        return None, 0
    return sum(vals) / len(vals), total


def worst_against(cand: int, index: dict[tuple[int, int], tuple[int, int]], k: float = DEFAULT_K):
    """cand 最怕谁：收缩后把它打得最惨的那个。"""
    pairs = [
        (b, shrunk(g, w, k), g)
        for (a, b), (g, w) in index.items()
        if a == cand
    ]
    if not pairs:
        return None
    return min(pairs, key=lambda x: x[1])


def best_against(cand: int, index: dict[tuple[int, int], tuple[int, int]], k: float = DEFAULT_K):
    """cand 最克制谁。"""
    pairs = [
        (b, shrunk(g, w, k), g)
        for (a, b), (g, w) in index.items()
        if a == cand
    ]
    if not pairs:
        return None
    return max(pairs, key=lambda x: x[1])


def coverage(index: dict[tuple[int, int], tuple[int, int]], hero_ids: list[int]) -> float:
    """这批英雄里，有多少比例存在可用的对位数据（用来判断推荐可不可信）。"""
    if not hero_ids:
        return 0.0
    present: set[int] = set()
    for a, b in index:
        present.add(a)
        present.add(b)
    hit = sum(1 for h in hero_ids if h in present)
    return hit / len(hero_ids)
