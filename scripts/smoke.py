"""最终检查：服务在跑、接口都在、库是干净的（不产生任何数据）。"""
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
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def head(path, body=None):
    st, d = call(path, body)
    keys = [k for k in d.keys() if k != "ok"][:6]
    print(f"  {path:34} {st}  keys={keys}")
    return d


print("接口连通性：")
head("/api/meta")
head("/api/heroes")
head("/api/bans")
head("/api/matches?source=official&only_played=1&limit=2")
head("/api/matches?source=sim")

print("\n空库确认：")
print("  ban 记录:", len(call("/api/bans")[1]["bans"]))
print("  模拟对局:", len(call("/api/matches?source=sim")[1]["matches"]))
print("  官方对局:", len(call("/api/matches?source=official&only_played=1")[1]["matches"]))

print("\n保存闸门（未打完应被拒，且不留数据）：")
v = call("/api/bp/new", {"bo": 3, "blue": "A", "red": "B"})[1]
st, r = call(f"/api/bp/{v['id']}/save", {})
print(f"  {st} {r.get('error')}")
print("  库里 ban 数仍为:", len(call("/api/bans")[1]["bans"]))
