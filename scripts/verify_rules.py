"""逐手比对 + 全局 BP 抽查（干净版）。"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from kplbp import rules, store

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "kpl.db")


def main():
    conn = store.connect(DB)

    rows = conn.execute(
        """SELECT o.schedule_id, o.round, m.seasonid
           FROM bp_order o JOIN matches m ON m.schedule_id = o.schedule_id
           ORDER BY o.schedule_id, o.round, o.seq"""
    ).fetchall()

    games = collections.OrderedDict()
    for r in rows:
        key = (r["schedule_id"], r["round"], r["seasonid"])
        games.setdefault(key, [])

    # 重新按 (schedule, round) 组装
    grouped = collections.defaultdict(list)
    for r in conn.execute("SELECT * FROM bp_order ORDER BY schedule_id, round, seq"):
        grouped[(r["schedule_id"], r["round"])].append(r)

    season_of = {
        r["schedule_id"]: r["seasonid"]
        for r in conn.execute("SELECT schedule_id, seasonid FROM matches")
    }

    bad_by_season = collections.Counter()
    good = 0
    for (sid, rnd), acts in grouped.items():
        shape = tuple(("B" if a["camp"] == 1 else "R", "pick" if a["is_pick"] else "ban") for a in acts)
        if len(shape) == len(rules.KPL_10BAN) and shape == tuple(rules.KPL_10BAN):
            good += 1
        elif len(shape) == 20:
            bad_by_season[season_of.get(sid, "?")] += 1

    print(f"20 手且与规则表完全一致：{good}")
    print("20 手但不一致的赛季分布：", dict(bad_by_season))

    # ---- 全局 BP 抽查（每场单独统计）----
    total_pick_repeat = 0
    total_ban_repeat = 0
    matches = 0
    for sid in conn.execute("SELECT DISTINCT schedule_id FROM bp_order"):
        sid = sid["schedule_id"]
        matches += 1
        rounds = [r["round"] for r in conn.execute(
            "SELECT DISTINCT round FROM bp_order WHERE schedule_id=? ORDER BY round", (sid,))]
        seen_pick, seen_ban = set(), set()
        for rnd in rounds:
            for a in store.load_bp_order(conn, sid, rnd):
                bucket = seen_pick if a["is_pick"] else seen_ban
                if a["hero_id"] in bucket:
                    if a["is_pick"]:
                        total_pick_repeat += 1
                    else:
                        total_ban_repeat += 1
                bucket.add(a["hero_id"])

    print(f"\n抽查 {matches} 场：选人跨局重复 {total_pick_repeat} 次，ban 跨局重复 {total_ban_repeat} 次")
    print("  → pick 重复 ≈ 0 说明全局 BP 锁 pick；ban 有重复说明 ban 不进全局池")


if __name__ == "__main__":
    main()
