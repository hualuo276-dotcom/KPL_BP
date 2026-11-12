"""端到端：跑完一整场 BO5，验证规则 / 全局 BP / 撤销 / 推荐接口。"""
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
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


def main():
    meta = call("/api/meta")
    print("赛季:", meta["season"], "| 战队:", len(meta["teams"]))
    teams = [t["team_id"] for t in meta["teams"]]

    v = call("/api/bp/new", {"bo": 5, "blue": teams[0], "red": teams[1], "config": {"global_bp": True}})
    sid = v["id"]
    print("新比赛（显式开全局BP）:", sid, v["team_names"], "| 英雄池:", len(v["heroes"]))
    print("第一步:", v["summary"]["turn"], "| 推荐前3:", [(x["cname"], x["score"]) for x in v["suggest"][:3]])

    # --- 验证非法操作：同一个英雄不能在一局里用两次 ---
    first = v["suggest"][0]["hero_id"]
    v = call(f"/api/bp/{sid}/act", {"hero_id": first})
    bad = call(f"/api/bp/{sid}/act", {"hero_id": first})
    print("重复出手被拒:", bad.get("error"))

    # --- 验证撤销 ---
    step_before = v["summary"]["step"]
    v = call(f"/api/bp/{sid}/undo", {})
    print("撤销: step", step_before, "->", v["summary"]["step"])

    # --- 打完一整场 ---
    games = 0
    steps_total = 0
    while not v["summary"]["match_finished"]:
        s = v["summary"]
        if s["game_finished"]:
            side = "B" if s["score"]["B"] <= s["score"]["R"] else "R"
            v = call(f"/api/bp/{sid}/winner", {"side": side})
            games += 1
            print(f"第 {s['game']} 局结束 -> 胜方 {side}，比分 {v['summary']['score']}")
            continue

        avail = [h for h in v["heroes"] if h["status"] == "available"]
        if not avail:
            print("!! 没有可用英雄了")
            return 1
        hid = avail[0]["hero_id"]
        new = call(f"/api/bp/{sid}/act", {"hero_id": hid})
        if not new.get("ok", True):
            print("!! 出手失败:", new)
            return 1
        v = new
        steps_total += 1
        if steps_total > 400:
            print("!! 步数异常")
            return 1

    s = v["summary"]
    print(f"比赛结束 比分 {s['score']} | 小局数 {len([g for g in s['games'] if g['winner']])}")
    print("全局锁定英雄数:", s["locked_count"])

    # --- 验证全局 BP：第 2 局里第 1 局用过的英雄必须是 locked ---
    g1 = s["games"][0]
    used_g1 = g1["picks"]["B"] + g1["picks"]["R"]
    st = {h["hero_id"]: h["status"] for h in v["heroes"]}
    leaked = [h for h in used_g1 if st.get(h) != "locked"]
    print("第1局用过的英雄是否全部锁死:", "是" if not leaked else f"否 {leaked}")

    # --- 比赛结束后不能再撤销 ---
    v2 = call(f"/api/bp/{sid}/undo", {})
    print("结束后撤销被拒:", v2.get("error"))

    # --- 真实对局接口 ---
    ms = call("/api/matches?limit=3&only_played=1")
    m0 = ms["matches"][0]
    d = call(f"/api/match/{m0['schedule_id']}")
    print("真实对局:", d["match"]["team_a_name"], "vs", d["match"]["team_b_name"], "| 小局", len(d["rounds"]))
    print("  第1局选人:", [p["hero_name"] for p in d["rounds"][0]["picks"]][:5])
    return 0


sys.exit(main())
