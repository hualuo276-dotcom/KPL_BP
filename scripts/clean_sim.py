"""清掉所有模拟对局（source='sim'），官方数据不受影响。

用法：
    python scripts/clean_sim.py            # 只看会删什么
    python scripts/clean_sim.py --yes      # 真删
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from kplbp import store

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "kpl.db")


def main() -> int:
    conn = store.connect(DB)
    rows = conn.execute(
        "SELECT schedule_id, title FROM matches WHERE source='sim' ORDER BY start_ts DESC"
    ).fetchall()
    if not rows:
        print("没有模拟对局，什么都不用做。")
        return 0

    for r in rows:
        n_bans = conn.execute("SELECT COUNT(*) c FROM bans WHERE schedule_id=?", (r["schedule_id"],)).fetchone()["c"]
        print(f"  {r['schedule_id']}  {r['title']}  ({n_bans} ban)")

    if "--yes" not in sys.argv:
        print(f"\n共 {len(rows)} 场。加 --yes 才会真删。")
        return 0

    for r in rows:
        store.delete_sim_match(conn, r["schedule_id"])
    print(f"\n已删除 {len(rows)} 场模拟对局。官方数据未动：")
    stat = conn.execute(
        """SELECT (SELECT COUNT(*) FROM matches WHERE source='official') m,
                  (SELECT COUNT(*) FROM picks) p,
                  (SELECT COUNT(*) FROM games) g,
                  (SELECT COUNT(*) FROM bans) b"""
    ).fetchone()
    print(f"  官方比赛 {stat['m']} · 选人 {stat['p']} · 小局 {stat['g']} · ban {stat['b']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
