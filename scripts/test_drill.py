"""验证：10 ban 规则、官方 ban 入库、复盘模式。"""
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


def main():
    st, meta = call("/api/meta")
    teams = [t["team_id"] for t in meta["teams"]]

    # 1. 20 手 & 5+5 ban
    st, v = call("/api/bp/new", {"bo": 5, "blue": teams[0], "red": teams[1]})
    s = v["summary"]
    from collections import Counter

    c = Counter((a["side"], a["action"]) for a in s["step_table"])
    print(f"[规则] 步数={s['total_steps']}  统计={dict(c)}")
    st2, v2 = call("/api/bp/new", {"bo": 5, "blue": teams[0], "red": teams[1]})
    print(f"[规则] 默认配置 global_bp={v2['summary']['locked_count']} 锁定（现行 KPL 每局独立 BP，应为 0）")

    # 2. ban 榜
    st, b = call("/api/bans")
    print(f"[ban] 榜上英雄 {len(b['bans'])} 个，榜首 {b['bans'][:3]}")

    # 3. 英雄 presence 是否含官方 ban
    st, hs = call("/api/heroes")
    top = sorted(hs["heroes"], key=lambda h: -(h["bans"] or 0))[:3]
    print(f"[ban] 被 ban 最多：{[(h['cname'], h['bans'], h['picks'], h['presence']) for h in top]}")

    # 4. 复盘模式
    st, lst = call("/api/drill/list?limit=3")
    print(f"[复盘] 可复盘场次 {len(lst['matches'])}，示例 {lst['matches'][0]['schedule_id']} rounds={lst['matches'][0]['rounds']}")
    sid_m = lst["matches"][0]["schedule_id"]

    st, d = call("/api/drill/start", {"schedule_id": sid_m, "round": 1})
    if st != 200:
        print("[复盘] 启动失败", d)
        return 1
    dd = d["drill"]
    print(f"[复盘] 启动 session={d['id']} 总手数={dd['total']} kind={d['kind']}")

    # 照真实答案走前 3 手（全部命中），第 4 手故意走错
    truths = []
    for i in range(3):
        turn = d["summary"]["turn"]
        # 从推荐里拿一个，实际我们的目标是命中；直接查真实答案不方便，
        # 就用状态里 hero 列表找「下一手真实英雄」——通过读取 drill 结果不可行，
        # 所以这里改为：随机走一手，命中与否都记录
        avail = [h for h in d["heroes"] if h["status"] == "available"]
        st, d = call(f"/api/bp/{d['id']}/act", {"hero_id": avail[0]["hero_id"]})
        r = d["drill"]["results"][-1]
        truths.append((r["seq"], r["hit"], r["mine"], r["truth"]))

    for t in truths:
        print(f"[复盘] #{t[0]} {'命中' if t[1] else '不同'}  我={t[2]}  职业={t[3]}")
    print(f"[复盘] 进度 {d['drill']['cursor']}/{d['drill']['total']}  命中 {d['drill']['hits']}")

    # 5. 用户方 ID 映射（camp1 = 蓝）
    print(f"[复盘] 蓝方={d['team_names']['B']} 红方={d['team_names']['R']}")
    return 0


sys.exit(main())
