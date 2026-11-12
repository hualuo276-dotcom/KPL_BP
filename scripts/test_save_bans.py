"""验证「保存本局 → ban 进榜」这条链路。"""
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


def play_game(v, sid, winner="B"):
    while not v["summary"]["game_finished"]:
        avail = [h for h in v["heroes"] if h["status"] == "available"]
        v = call(f"/api/bp/{sid}/act", {"hero_id": avail[0]["hero_id"]})
    return v


def main():
    meta = call("/api/meta")
    teams = [t["team_id"] for t in meta["teams"]]

    before = {b["cname"]: b["bans"] for b in call("/api/bans")["bans"]}
    print("保存前 ban 榜条数:", len(before))

    v = call("/api/bp/new", {"bo": 3, "blue": teams[0], "red": teams[1]})
    sid = v["id"]

    # 空局保存应当被拒
    r = call(f"/api/bp/{sid}/save", {})
    print("未打完保存被拒:", r.get("error"))

    v = play_game(v, sid)
    v = call(f"/api/bp/{sid}/winner", {"side": "B"})
    v = play_game(v, sid)
    v = call(f"/api/bp/{sid}/winner", {"side": "B"})
    print("小局数:", len([g for g in v["summary"]["games"] if g["winner"]]))

    saved = call(f"/api/bp/{sid}/save", {})
    print("保存成功:", saved.get("saved"))

    after = call("/api/bans")["bans"]
    print("保存后 ban 榜条数:", len(after))
    print("榜首:", [(b["cname"], b["bans"], b["sources"]) for b in after[:5]])

    # 幂等 / 覆盖：再点一次，条数不应翻倍
    again = call(f"/api/bp/{sid}/save", {})
    after2 = call("/api/bans")["bans"]
    print("重复保存后总 ban 数:", sum(b["bans"] for b in after2), "（第一次:", sum(b["bans"] for b in after), "）")
    print("重新保存复用同一编号:", again.get("saved") == saved.get("saved"))

    # 模拟记录列表 + 明细（要能带出 ban）
    sims = call("/api/matches?source=sim&limit=5")["matches"]
    print("模拟对局列表:", [(m["schedule_id"], m["score_a"], m["score_b"], m["bans"]) for m in sims[:3]])
    d = call(f"/api/match/{again['saved']}")
    r1 = d["rounds"][0]
    print(f"第1局 ban {len(r1['bans'])} 个:", [b["hero_name"] for b in r1["bans"]])
    print(f"第1局 pick {len(r1['picks'])} 个:", [p["hero_name"] for p in r1["picks"]][:5])

    # presence 是否把 ban 算进去了
    heroes = call("/api/heroes")["heroes"]
    top = sorted(heroes, key=lambda h: -h["bans"])[:5]
    print("英雄 presence 前5:", [(h["cname"], h["picks"], h["bans"], h["presence"]) for h in top])

    official = call("/api/matches?source=official&only_played=1&limit=3")
    print("官方对局仍然正常:", [(m["team_a_name"], m["rounds"]) for m in official["matches"]])
    return 0


sys.exit(main())
