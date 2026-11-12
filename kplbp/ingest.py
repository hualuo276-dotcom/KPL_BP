"""把 KPL 官方数据抓进本地 SQLite。

用法：
    python -m kplbp.ingest                       # 当前赛季，全量
    python -m kplbp.ingest --seasons 6           # 最近 6 个赛季（推荐，样本才够算对位）
    python -m kplbp.ingest --season KPL2025S3    # 指定赛季
    python -m kplbp.ingest --no-detail           # 只抓赛程，不抓每局 BP
    python -m kplbp.ingest --detail-limit 10     # 每个赛季只抓最近 10 场的小局数据

所有请求都是幂等的：重复跑会覆盖更新，不会产生重复行。
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback

from . import sources as S
from . import store

DEFAULT_DB = "data/kpl.db"


def log(msg: str) -> None:
    print(msg, flush=True)


def ingest_season(conn, season: str, with_detail: bool, detail_limit: int, tag: str = "") -> dict:
    """抓一个赛季。返回统计。"""
    pre = f"{tag}" if tag else ""

    log(f"{pre}③ 战队与选手 kplow/getTeamsList …")
    teams = S.fetch_teams(season)
    store.upsert_teams(conn, season, teams)
    log(f"{pre}   {len(teams)} 支战队，{sum(len(t['players']) for t in teams)} 名选手")

    log(f"{pre}④ 赛程 kplow/getScheduleList …")
    rows = S.fetch_schedule(season)
    store.upsert_matches(conn, season, rows)
    log(f"{pre}   {len(rows)} 场比赛")

    log(f"{pre}⑤ 英雄数据榜 kplow/getHeroRankList …")
    rank = S.fetch_hero_rank(season)
    store.replace_hero_rank(conn, season, rank)
    log(f"{pre}   {len(rank)} 个英雄有赛季数据")

    ok = fail = 0
    if with_detail:
        todo = [r for r in rows if int(float(r.get("schedule_status") or 0)) == 4]
        todo.sort(key=lambda r: int(float(r.get("start_timestamp") or 0)), reverse=True)
        if detail_limit > 0:
            todo = todo[:detail_limit]
        log(f"{pre}⑥ 单局 BP 明细 kplow/getScheduleDetail …共 {len(todo)} 场")
        for i, r in enumerate(todo, 1):
            sid = r.get("scheduleid", "")
            if not sid:
                continue
            try:
                detail = S.fetch_schedule_detail(sid, season)
                if detail.get("data"):
                    store.replace_match_detail(conn, sid, detail)
                    ok += 1
                else:
                    fail += 1
            except Exception as e:  # noqa: BLE001 - 单场失败不中断整批
                fail += 1
                log(f"{pre}   ! {sid} {e}")
            if i % 20 == 0:
                log(f"{pre}   {i}/{len(todo)} …（成功 {ok} / 失败 {fail}）")
    return {"season": season, "matches": len(rows), "ok": ok, "fail": fail}


def build_match_index(conn) -> dict:
    """(日期, {队名, 队名}) → schedule_id，用来把 smoba 场次对到本地行。"""
    idx = {}
    for r in conn.execute(
        "SELECT schedule_id, team_a_name, team_b_name, start_ts FROM matches WHERE source='official'"
    ):
        d = time.strftime("%Y-%m-%d", time.localtime(r["start_ts"] or 0))
        idx[(d, frozenset([r["team_a_name"] or "", r["team_b_name"] or ""]))] = r["schedule_id"]
    return idx


def _smoba_key(m: dict) -> tuple:
    d = (m.get("start_time") or "")[:10]
    names = frozenset(
        [(m.get("camp1") or {}).get("team_name", ""), (m.get("camp2") or {}).get("team_name", "")]
    )
    return d, names


def ingest_bp_orders(conn, leagues_filter: set[str] | None = None) -> dict:
    """抓官方真实 BP 顺序（含 ban），写 bp_order + bans(source='official')。

    这是 ban 数据的唯一官方来源：kplow 的 round_details 只给选人，
    而 smoba 的 /battle/open 给完整 20 手（10 ban + 10 pick）和先后顺序。
    """
    idx = build_match_index(conn)
    if not idx:
        log("   本地还没有官方场次，跳过")
        return {"ok": 0, "fail": 0, "matches": 0}

    leagues = S.fetch_smoba_leagues()
    log(f"   smoba 联赛 {len(leagues)} 个，本地场次 {len(idx)} 个")

    ok = fail = matched = 0
    for lg in leagues:
        name = lg.get("league_name", "")
        lid = lg.get("league_id", "")
        if leagues_filter and name not in leagues_filter and lid not in leagues_filter:
            continue
        try:
            rows = S.fetch_smoba_matches(lid)
        except Exception as e:  # noqa: BLE001
            log(f"   ! {name} 场次失败：{e}")
            continue

        pairs = []
        for m in rows:
            sid = idx.get(_smoba_key(m))
            if sid and (m.get("match_battle_video_list") or []):
                pairs.append((sid, m))
        if not pairs:
            continue

        log(f"   {name}：可对上的场次 {len(pairs)}/{len(rows)}")
        matched += len(pairs)
        for sid, m in pairs:
            for v in m["match_battle_video_list"]:
                bid = v.get("battle_id")
                seq = int(v.get("battle_seq") or 0) or 1
                try:
                    battle = S.fetch_smoba_battle(bid)
                    acts = S.parse_bp_list(battle)
                    if acts:
                        store.replace_bp_order(
                            conn,
                            sid,
                            seq,
                            acts,
                            blue_name=(battle.get("camp1") or {}).get("team_name", ""),
                            red_name=(battle.get("camp2") or {}).get("team_name", ""),
                        )
                        ok += 1
                    else:
                        fail += 1
                except Exception as e:  # noqa: BLE001 - 单局失败不中断
                    fail += 1
                    if fail <= 3:
                        log(f"   ! {bid} {e}")
                time.sleep(0.12)
            if (ok + fail) % 100 < 5:
                log(f"     {ok + fail} 局 …（成功 {ok} / 失败 {fail}）")

    log(f"   真实 BP：成功 {ok} 局，失败 {fail}，涉及 {matched} 场")
    return {"ok": ok, "fail": fail, "matches": matched}


def ingest(season: str | None, db: str, with_detail: bool, detail_limit: int, seasons_n: int,
           with_bp: bool = False) -> None:
    conn = store.connect(db)

    log("① 英雄库 pvp.qq.com …")
    heroes = S.fetch_heroes()
    n = store.upsert_heroes(conn, heroes)
    log(f"   {n} 个英雄")

    log("② 赛季列表 kplow/getSeasonAndStageAndTeamList …")
    seasons = S.fetch_seasons()
    store.upsert_seasons(conn, seasons)
    log(f"   {len(seasons)} 个赛季")

    if season:
        targets = [season]
    else:
        cur = [s for s in seasons if s["is_cur"]]
        head = cur[0]["seasonid"] if cur else seasons[0]["seasonid"]
        if seasons_n <= 1:
            targets = [head]
        else:
            # 从当前赛季往前取 N 个
            ids = [s["seasonid"] for s in seasons]
            start = ids.index(head) if head in ids else 0
            targets = ids[start : start + seasons_n]

    store.set_meta(conn, "seasonid", targets[0])
    log(f"   目标赛季：{', '.join(targets)}")

    total_matches = total_ok = total_fail = 0
    for i, sid in enumerate(targets, 1):
        log("")
        log(f"===== [{i}/{len(targets)}] {sid} =====")
        st = ingest_season(conn, sid, with_detail, detail_limit, tag="")
        total_matches += st["matches"]
        total_ok += st["ok"]
        total_fail += st["fail"]
        time.sleep(0.2)

    log("")
    log("⑦ 按实际站位回填分路 …")
    n = store.recompute_lanes(conn)
    log(f"   {n} 个英雄有对局站位数据")

    log("⑧ 重建同位置对位矩阵 …")
    n = store.rebuild_matchups(conn)
    log(f"   {n} 组对位样本")

    if with_bp:
        log("⑨ 官方真实 BP 顺序（含 ban）prod.comp.smoba.qq.com …")
        ingest_bp_orders(conn)
        cov = store.bp_coverage(conn)
        log(f"   覆盖：{cov['with_bp']}/{cov['games']} 小局有真实 BP，官方 ban {cov['official_bans']} 条")

    stat = conn.execute(
        """SELECT (SELECT COUNT(*) FROM heroes) h,
                  (SELECT COUNT(*) FROM teams) t,
                  (SELECT COUNT(*) FROM players) p,
                  (SELECT COUNT(*) FROM matches WHERE source='official') m,
                  (SELECT COUNT(*) FROM picks) pk,
                  (SELECT COUNT(*) FROM games) g,
                  (SELECT COUNT(*) FROM matchups) mu,
                  (SELECT COUNT(*) FROM bp_order) bo"""
    ).fetchone()
    log("")
    log(
        f"库内：英雄 {stat['h']} · 战队 {stat['t']} · 选手 {stat['p']} · 比赛 {stat['m']} · "
        f"选人 {stat['pk']} · 小局 {stat['g']} · 对位 {stat['mu']} · BP 手数 {stat['bo']}"
    )
    log(f"本次：{len(targets)} 个赛季，比赛 {total_matches}，明细成功 {total_ok}/失败 {total_fail}")
    log(f"数据库：{db}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KPL 数据入库")
    ap.add_argument("--season", default=None, help="赛季 ID，如 KPL2026S3；默认取当前赛季")
    ap.add_argument("--seasons", type=int, default=1, help="抓最近 N 个赛季（默认 1）")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite 路径（默认 {DEFAULT_DB}）")
    ap.add_argument("--no-detail", action="store_true", help="跳过单局 BP 明细")
    ap.add_argument("--detail-limit", type=int, default=0, help="每个赛季只抓最近 N 场明细，0 = 全部")
    ap.add_argument("--with-bp", action="store_true", help="同时抓官方真实 BP 顺序（含 ban）")
    ap.add_argument("--bp-only", action="store_true", help="只抓真实 BP，跳过其他步骤")
    args = ap.parse_args(argv)

    try:
        if args.bp_only:
            conn = store.connect(args.db)
            ingest_bp_orders(conn)
            cov = store.bp_coverage(conn)
            log(f"覆盖：{cov['with_bp']}/{cov['games']} 小局，官方 ban {cov['official_bans']} 条")
        else:
            ingest(args.season, args.db, not args.no_detail, args.detail_limit, args.seasons, args.with_bp)
    except S.SourceError as e:
        log(f"数据源错误：{e}")
        return 2
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
