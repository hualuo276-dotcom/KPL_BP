"""BO7 专项：验证第 7 局走巅峰对决（10 手盲选、无 ban、可复用已用英雄）。"""
import sys

sys.path.insert(0, r"E:\Work\kpl")
sys.stdout.reconfigure(encoding="utf-8")

from kplbp import engine, rules

print("规则自检:", rules.validate(rules.KPL_8BAN))
print("BO7 第7局步数:", len(rules.steps_for(7, 7, {})), "| blind:", rules.is_blind(7, 7, {}))
print("BO5 第5局步数:", len(rules.steps_for(5, 5, {})), "| blind:", rules.is_blind(5, 5, {}))

st = engine.new_state(bo=7, blue_team="A", red_team="B")
used_in_game1 = []

while not engine.match_finished(st):
    if engine.game_finished(st):
        engine.set_winner(st, "B" if st["game"] % 2 else "R")
        continue
    avail = [h for h in range(105, 240) if engine.hero_state(st, h) == "available"]
    if not avail:
        print("局", st["game"], "没有可用英雄")
        break
    hid = avail[0]
    if st["game"] == 1:
        used_in_game1.append(hid)
    engine.act(st, hid)

print("比分:", engine.score(st), "| 小局:", len([g for g in st["games"] if g["winner"]]))
g7 = st["games"][6]
print("第7局 blind:", g7["blind"], "| ban 数:", len(g7["bans"]["B"]) + len(g7["bans"]["R"]),
      "| pick 数:", len(g7["picks"]["B"]) + len(g7["picks"]["R"]))

reused = [h for h in g7["picks"]["B"] + g7["picks"]["R"] if h in used_in_game1]
print("第7局复用了第1局英雄:", reused or "无")
