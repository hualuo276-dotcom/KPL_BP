"""手工导入官方 ban（CSV）。

官方 BP 顺序现在能直连抓到了，这个脚本是**兜底**：
某些赛季 / 某些场次 smoba 没给 bp_list，或者你想补录一场没有数据的对局。

CSV 表头（顺序不限，大小写不限）：

    schedule_id,round,side,hero_id,hero_name
    KPL2026S3M1W1D1,1,B,136,武则天
    KPL2026S3M1W1D1,1,R,175,钟馗

* `side`        B / R（蓝/红），留空也行
* `hero_id`     英雄 ename；不填则用 `hero_name` 反查
* `round`       第几小局，从 1 开始

用法：
    python scripts/import_bans.py --template > bans.csv     # 出个模板
    python scripts/import_bans.py bans.csv --dry-run        # 先看会写什么
    python scripts/import_bans.py bans.csv                  # 真写
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from kplbp import store

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "kpl.db")

TEMPLATE = """schedule_id,round,side,hero_id,hero_name
KPL2026S3M1W1D1,1,B,136,武则天
KPL2026S3M1W1D1,1,R,175,钟馗
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="导入手工 ban 记录")
    ap.add_argument("csv", nargs="?", help="CSV 文件路径")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--dry-run", action="store_true", help="只校验，不写库")
    ap.add_argument("--template", action="store_true", help="输出模板")
    args = ap.parse_args()

    if args.template:
        sys.stdout.write(TEMPLATE)
        return 0
    if not args.csv:
        ap.error("需要 CSV 路径（或用 --template 出模板）")

    conn = store.connect(args.db)
    matches = {r["schedule_id"] for r in conn.execute("SELECT schedule_id FROM matches")}
    by_id = {r["hero_id"]: r["cname"] for r in conn.execute("SELECT hero_id, cname FROM heroes")}
    by_name = {r["cname"]: r["hero_id"] for r in conn.execute("SELECT hero_id, cname FROM heroes")}

    ok, bad = [], []
    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, 2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            sid = row.get("schedule_id", "")
            if sid not in matches:
                bad.append((i, f"没有这场：{sid}"))
                continue
            try:
                rnd = int(row.get("round") or 0)
            except ValueError:
                bad.append((i, f"round 不是数字：{row.get('round')}"))
                continue
            if rnd <= 0:
                bad.append((i, "round 必须 >= 1"))
                continue

            hid = row.get("hero_id")
            try:
                hid = int(hid) if hid else None
            except ValueError:
                hid = None
            if not hid:
                hid = by_name.get(row.get("hero_name", ""))
            if not hid or hid not in by_id:
                bad.append((i, f"认不出英雄：{row.get('hero_id') or row.get('hero_name')}"))
                continue

            side = (row.get("side") or "").upper()[:1]
            if side not in ("B", "R"):
                side = ""
            ok.append((sid, rnd, side, hid))

    print(f"可写入 {len(ok)} 条，问题 {len(bad)} 条")
    for i, why in bad[:20]:
        print(f"  第 {i} 行：{why}")

    if args.dry_run or not ok:
        for sid, rnd, side, hid in ok[:10]:
            print(f"  {sid} 第{rnd}局 {side or '-'} ban {by_id[hid]}")
        return 0

    conn.executemany(
        """INSERT OR REPLACE INTO bans(schedule_id,round,team_id,side,hero_id,source)
           VALUES(?,?,?,?,?,'manual')""",
        [(sid, rnd, "", side, hid) for sid, rnd, side, hid in ok],
    )
    conn.commit()
    print(f"已写入 {len(ok)} 条（source='manual'）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
