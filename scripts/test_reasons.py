"""验证推荐标签的语义：出场 / 被 ban / 对面选手 / 权重再分配。"""
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
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def dump(title, v):
    print(f"\n=== {title} ===")
    print(f"轮到 {v['summary']['turn']}")
    for x in v["suggest"][:6]:
        d = x["detail"]
        print(f"  {x['cname']:8} {x['score'] * 100:5.1f}  " + " | ".join(x["reasons"]))


def main():
    _, meta = call("/api/meta")
    teams = [t["team_id"] for t in meta["teams"]]
    _, v = call("/api/bp/new", {"bo": 5, "blue": teams[0], "red": teams[1]})
    sid = v["id"]

    dump("第一轮 ban（我方无已选，counter 权重让给 strong/threat）", v)

    # 走到第一轮 pick
    for _ in range(4):
        v = call(f"/api/bp/{sid}/act", {"hero_id": v["suggest"][0]["hero_id"]})[1]
    dump("第一个 pick", v)

    # 让双方各拿几手，再看第二轮 ban（我方已有 pick）
    for _ in range(12):
        if v["summary"].get("turn") is None or v["summary"]["game_finished"]:
            break
        if v["summary"]["turn"]["action"] == "ban":
            dump("第二轮 ban（我方已有已选，counter 生效）", v)
            break
        v = call(f"/api/bp/{sid}/act", {"hero_id": v["suggest"][0]["hero_id"]})[1]

    # 检查是否有任何标签把 presence 说成「出场」
    bad = []
    for x in v["suggest"]:
        for r in x["reasons"]:
            if "本赛季出场" in r or "招牌" in r:
                bad.append((x["cname"], r))
    print(f"\n旧措辞残留：{bad or '无'}")
    return 0


sys.exit(main())
