"""验证对位克制矩阵 + 英雄详情 + 赛季切换。"""
import json
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8780"


def call(path, body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def main():
    st, d = call("/api/matchups?min_games=6")
    print(f"对位矩阵（min_games=6）: {st} pairs={d.get('pairs')}")

    st, d = call("/api/matchups?min_games=12")
    print(f"对位矩阵（min_games=12）: {st} pairs={d.get('pairs')}")

    # 找一个有对位数据的英雄
    st, heroes = call("/api/heroes")
    rows = heroes["heroes"]
    rows.sort(key=lambda h: -(h["picks"] or 0))
    target = rows[0]
    print(f"\n出场最多的英雄: {target['cname']} (picks={target['picks']}, wr={target['pick_wr']})")

    st, hd = call(f"/api/hero/{target['hero_id']}")
    print("英雄详情:", st, "| 压制", len(hd["matchups"]["beats"]), "个 | 怕", len(hd["matchups"]["loses"]), "个")
    print("  压制:", [(x["cname"], x["wr"], x["games"]) for x in hd["matchups"]["beats"][:4]])
    print("  怕:  ", [(x["cname"], x["wr"], x["games"]) for x in hd["matchups"]["loses"][:4]])
    print("  常用选手:", [(p["name"], p["games"], p["wr"]) for p in hd["players"][:3]])

    # 打一场，看看推荐里有没有「克制」理由
    meta = call("/api/meta")[1]
    teams = [t["team_id"] for t in meta["teams"]]
    v = call("/api/bp/new", {"bo": 3, "blue": teams[0], "red": teams[1]})[1]
    sid = v["id"]

    # 走到有「对面已选英雄」的阶段，看推荐里有没有克制信号
    for _ in range(40):
        s = v["summary"]
        if s["game_finished"] or not s["turn"]:
            break
        if s["turn"]["action"] == "pick" and s["step"] >= 7:
            print(f"\n第 {s['step'] + 1} 手 ({s['turn']['side']} pick) 推荐前3:")
            for x in v["suggest"][:3]:
                print(
                    f"   {x['cname']:8} {x['score']:.3f}  counter={x['detail']['counter']} "
                    f"样本={x['detail']['counter_samples']}  理由={x['reasons']}"
                )
        choice = v["suggest"][0]["hero_id"] if v["suggest"] else None
        if not choice:
            avail = [h for h in v["heroes"] if h["status"] == "available"]
            if not avail:
                break
            choice = avail[0]["hero_id"]
        v = call(f"/api/bp/{sid}/act", {"hero_id": choice})[1]

    # 赛季切换
    seasons = meta["seasons"]
    other = next((s["seasonid"] for s in seasons if s["seasonid"] != meta["season"]), None)
    if other:
        st, d = call("/api/season", {"season": other})
        print(f"\n切赛季 -> {other}: {st} {d}")
        st, h2 = call("/api/heroes")
        print(f"  新赛季英雄数 {len(h2['heroes'])}，示例 {h2['heroes'][0]['cname']} picks={h2['heroes'][0]['picks']}")
        call("/api/season", {"season": meta["season"]})
        print(f"  已切回 {meta['season']}")
    return 0


sys.exit(main())
