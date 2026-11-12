# KPL BP 模拟器

王者荣耀职业联赛（KPL）的 Ban/Pick 模拟器，数据直连**腾讯官方两套赛事接口**，不是手抄 Excel。

界面：左边蓝方、右边红方、中间 133 个英雄的池子、右边给出「这一手该 ban 谁 / 该 pick 谁」以及理由、
下面能翻真实对局、还能**复盘职业对局**——你走一手，跟职业队那一手对答案。

```
python -m kplbp.ingest --seasons 6 --with-bp   # 抓 6 个赛季 + 官方真实 BP（约 30 分钟）
python -m kplbp.server                          # 起服务
# 打开 http://127.0.0.1:8780/
```

零依赖，只用 Python 3.10+ 标准库（`sqlite3` / `http.server` / `urllib`）。不装任何包。

---

## 一、数据从哪来

接了**两套**官方接口。它们的区别很关键：

| 接口族 | 域名 | 给什么 | 缺什么 |
|---|---|---|---|
| kplow | `kplshop-op.timi-esports.qq.com` | 赛季 / 战队 / 赛程 / 每局选人 | **没有 ban** |
| smoba | `prod.comp.smoba.qq.com/leaguesite` | 每局**完整 20 手 BP**（含 ban 和顺序） | 没有选手名单 |

只接第一套的话，「ban 率」永远是 0——这是我第一版最大的窟窿。

### 1. 英雄库

```
GET https://pvp.qq.com/web201605/js/herolist.json
```

- 133 个英雄，字段 `ename / cname / title / hero_type / roles`
- **编码是 UTF-8**（文档常说 GBK，按 GBK 解会得到「寤夐」这种乱码）

### 2. kplow（赛季 / 战队 / 赛程 / 选人）

```
POST https://kplshop-op.timi-esports.qq.com/kplow/<endpoint>
```

base 不是文档里的，是从 `kpl.qq.com` 的 Vite bundle 里逆出来的（`ci = ${baseUrl}/kplow`，
`baseUrl` 来自 `Cpe()`）。可用：`getSeasonAndStageAndTeamList` / `getTeamsList` / `getScheduleList` /
`getScheduleDetail` / `getHeroRankList` / `getPlayerRank` / `getSeasonPlayerPerf` / `getScheduleProgress`。

### 3. smoba（真实 BP，含 ban）

```
GET https://prod.comp.smoba.qq.com/leaguesite/leagues/open
GET https://prod.comp.smoba.qq.com/leaguesite/matches/open?league_id={league_id}
GET https://prod.comp.smoba.qq.com/leaguesite/battle/open?battle_id={battle_id}
```

`battle/open` 的 `data.bp_list` 就是**按顺序排的每一手**：

```json
{"camp": 1, "is_ban_or_pick": 1, "hero_id": 135, "hero_name": "项羽", "position": 3}
```

拿到它之后，ban 数据从 0 变成 **24213 条**，覆盖 **2442/2745** 小局。

---

## 二、五个坑（都是实测踩出来的）

### 坑一：kplow 返回的不是 JSON

PHP 风格扁平 querystring，**键本身被 urlencode 过**：

```
data.seasons%5B0%5D.season_name=2026KPL%E5%B9%B4%E5%BA%A6%E6%80%BB%E5%86%B3%E8%B5%9B
```

顺序必须是「先 `parse_qsl` → 再按 `[n]` 建树」。而且 `seasons` 下标是**稀疏**的（`[0]` 直接跳到 `[10]`），
按连续下标切会错位。

### 坑二：`getScheduleDetail` 参数名错一个字母，它不报错，给你另一场的数据

```
schedule_id=xxx   → 31339 字节，有数据        ← 参数被忽略，返回「最近一场」
scheduleid=xxx    → {"result":10020003,"msg":"schedule not found"}
```

真参数是 `scheduleid`，**并且必须同时传 `seasonid`**。
判据：**每场比赛的选人数不可能完全相同**——第一版跑完 53 场，每场都是 69 条选人、46 个英雄。

### 坑三：`position` 的口径不是国标

官方是 `1对抗 2中路 3发育 4打野 5游走`，跟习惯的 `2打野 4发育` 反着。
判据：沈梦溪（中路）拿到 2、裴擒虎（打野）拿到 4。

### 坑四：`is_ban_or_pick` 的 1 是 **pick**，不是 ban

名字起得像「双关」，看着 `1` 容易以为 true=ban。实际用 `battle_player_list` 的 10 个英雄去对：

```
被玩家实际选用的英雄，其 is_ban_or_pick 取值分布: {1: 160}   ← 全在 1 上
```

所以 **1 = pick，0 = ban**。

### 坑五：`camp1/camp2` ≠ 我们表里的 team_a/team_b

camp1 只是「先 ban 的那一方」，每小局还会换边。复盘时如果直接拿 team_a 当蓝方，
蓝红会摆反。用 `bp_order` 里 camp1/camp2 的选人集合去和 `picks` 表各队的集合比对，
离线就能推出对应关系（`backfill_bp_sides()` 补了 2406 局，不用再联网）。

---

## 三、库里有什么

```
heroes      133    英雄（lane 按实际站位投票得出）
seasons      24
teams        48    战队（跨 6 赛季）
players     405    选手
matches     651    官方比赛
games      2745    小局
picks     27380    选人
bp_order  48568    官方真实 BP 手数（2442 局 × 约 20 手）
bans      24213    官方 ban（source='official'）
matchups   2636    同分路对位
bp_sides   2406    camp → 战队 的映射
```

三个视图都带 `source`（`official` / `sim` / `manual`），官方数据和模拟数据在同一张表里也不会串味。

---

## 四、BP 规则：**先说结论，之前的 8 ban 是错的**

规则在 `kplbp/rules.py`，数字全部来自 `bp_list` 逐手核对，不是记忆。

一局 **20 手**，蓝红各 **5 ban + 5 pick**：

```
1 蓝ban  2 红ban  3 蓝ban  4 红ban
5 蓝pick 6 红pick 7 红pick 8 蓝pick 9 蓝pick 10 红pick
11 红ban 12 蓝ban 13 红ban 14 蓝ban 15 红ban 16 蓝ban
17 红pick 18 蓝pick 19 蓝pick 20 红pick
```

`scripts/verify_rules.py` 拿库里 2442 局真实序列逐手比对：

```
手数分布: {5:4, 6:3, 7:9, 8:2, 9:2, 19:7, 20:2415}
与 KPL_10BAN 逐手比对：不一致 11 局（2404 局完全一致）
```

不一致的 11 局是零星异常（分散在 4 个赛季），主流形态就是这张表。

### 还有一条更重要的：**现行 KPL 没有全局 BP**

第一版我默认开了全局 BP，以为「用过的英雄后续小局锁定」。拿数据一查：

```
BO5: 492 场  → 无跨局重复 9 场，有重复 483 场
BO7:  88 场  → 有重复 88 场
```

随机抽一局看：

```
第1局 picks=[敖隐,张飞,海月,女娲,苏烈,赵云,夏洛特,达摩,裴擒虎,百里守约]
第2局 picks=[关羽,少司缘,猪八戒,大司命,戈娅,安琪拉,莱西奥,墨子,女娲,赵云]  ← 女娲、赵云复用
```

**女娲和赵云在第 1、2 局都上场了。** 所以现行 KPL 是**每局独立 BP**，
`global_bp` 默认改成 `false`，前端留了个勾选框给世冠 / 挑战者杯那种真全局 BP 的赛事。

可配置项：

| 键 | 默认 | 含义 |
|---|---|---|
| `ban_slots` | `10` | 每队 ban 数（现行 5，老赛制 4 → 18 手） |
| `global_bp` | `false` | 全局 BP（用过的英雄后续锁定） |
| `lock_bans` | `false` | 全局 BP 时被 ban 的是否也锁 |
| `blind_last` | `true` | BO7 第七局走巅峰对决（10 手盲选、无 ban、不受全局限制） |
| `second_ban_red_first` | `true` | 第二轮 ban 红方先 |

---

## 五、推荐怎么算

`kplbp/suggest.py` + `counter.py` + `planner.py`。全部输入来自真实数据：

| 信号 | 来源 | ban / pick 权重 |
|---|---|---|
| 英雄强度 `strength` | **真实出场次数** + 胜率 | 0.45 / 0.35 |
| 对手威胁 `opp` | 对手战队英雄池 | 0.35 / — |
| 我方熟练 `own` | 我方战队英雄池 | — / 0.22 |
| 阵容缺口 `need` | 已选英雄的分路覆盖 | — / 0.18 |
| 对位克制 `counter` | 同分路 1v1 真实胜负 | 0.20 / 0.12 |
| **全局前瞻 `plan`** | 模拟剩余出手，双方贪心 | — / 0.13 |

### 强度为什么不用 presence

第一版拿 `presence`（出场 + 被 ban）当强度，出了个正反馈陷阱：

```
被 ban 越多 → presence 越高 → 越推荐去 ban → 又被 ban
```

海月最典型：**出场 57 次、被 ban 136 次，presence 193**。按 presence 算，它会永远躺在 ban 榜第一，
但它其实只是「大家都在 ban 它」，不是「它强」。改成真实出场次数 + 胜率之后，
它从第 1 掉到第 5。

标签也拆开了——原来把 `193` 写成「本赛季出场 193 次」，那是错的，现在是 `出场 57 · 被 ban 136`。

### 招牌选手：只看对面场上的人

第一版直接显示 `hero_rank` 里的 `best_player`，那是**全联盟**最会用这个英雄的人。
如果本场对手不是那支队，这个人根本不在场上，标签是误导。

现在从 `opp_pool`（对手战队真实英雄池）取，显示成 `对面 流星 9 场 78%`——
是本场对手的选手、本场对手的使用场次。3~4 场只报场次不报胜率（样本太薄）。

### 第一轮 ban 的权重再分配

第一轮 ban 时我方还没选人，`counter` 整个用不上。0.20 的权重挂在常数 0.5 上，
会把所有候选压成一条窄带（70~54 那种挤在一起的分）。所以这时把这份权重按 6:4 让给
`strength` 和 `threat`，区分度才回来。

### 对位克制：贝叶斯收缩

每小局蓝红双方 position 相同的两个英雄互为对位，谁赢给谁记一笔。

样本很薄（133 英雄 × 5 分路，一万多场对位摊下去每对只有个位数），所以：

```
wr' = (w + k·0.5) / (g + k)        k = 6
```

同样全胜，3 局给 0.67、30 局给 0.94；再乘置信度 `min(1, 总局数/20)`。

> 踩过的坑：一开始用 min-max 归一化，2 局样本的 0.625 被拉到 0.938，比不收缩还危险。

### 版本衰减

英雄平衡每版本都在改，所以每个赛季的样本乘 `0.85 ** 赛季差`：

```
最新赛季 1.00 · 上季 0.85 · 0.72 · 0.61 · 0.52 · 0.44
```

### 全局前瞻

单局贪心只回答「这一手谁强」。`planner.lookahead()` 对前 30 个候选各模拟一次：
假设我拿 X，然后双方按剩余出手顺序轮流拿（都拿当前最强且补得上分路的），
算我方阵容总强度，取最大。**不是 Minimax，是 1 层前瞻 + 双方贪心**——成本低、可解释。

实测三手：

```
[第一轮 ban]
曹操   82.0  对面 流星 9 场 78% | 出场 78 · 被 ban 27 | 胜率 62%
海月   68.7  对面 花卷 6 场     | 出场 57 · 被 ban 136 | 胜率 68%

[第一个 pick]
敖隐   72.7  补 发育路 | 我方常用 一诺 | 出场 77 · 被 ban 64

[第二轮 ban]
女娲   55.0  对面 长生 5 场 40% | 克我方已选（27 局对位） | 出场 34 · 被 ban 33
```

---

## 六、复盘模式：跟职业队对答案

「对局数据」里点某局的「复盘这局」，进入训练：

- 系统加载这一局**真实的 20 手顺序**，你按顺序走，每走一手立刻告诉你跟职业选择是否一致
- 命中数实时累计，走完给命中率
- 「对方」不是 AI，就是那场比赛的真实 BP 记录

实现上没有另写一套引擎，只给 `engine.steps()` 开了个 `custom_steps` 口子：
复盘时按真实序列走，连巅峰对决那种 10 手盲选也能原样复现。

```
[复盘] 启动 session=723d60c9b466 总手数=20
[复盘] #1 不同  我=廉颇  职业=钟馗
[复盘] #2 不同  我=小乔  职业=鲁班大师
[复盘] 蓝方=北京WB 红方=广州TTG     ← camp1 对齐后的结果
```

可复盘的场次：**581 场**（其余场次 smoba 没有 bp_list，或对不上本地场次）。

---

## 七、保存本局 / 手工补 ban

打完一局点「保存本局」，ban / pick 写入 `source='sim'` 的对局。**统计口径是分开的**：

| 字段 | 取哪些来源 | 为什么 |
|---|---|---|
| `picks` / `wins` | 只取 `official` | 模拟局胜负是随手点的，掺进去会把真实胜率带偏 |
| `bans` | 所有来源求和 | 官方数据 + 模拟 + 手工，都算 |

官方 bp_list 没覆盖到的场次，可以手工补：

```bash
python scripts/import_bans.py --template > bans.csv
python scripts/import_bans.py bans.csv --dry-run
python scripts/import_bans.py bans.csv          # 写 source='manual'
```

---

## 八、从 GitHub 同类项目里吸收了什么

扒了一圈（`kpl-bp-dataset` / `hok-draft-engine` / `honor-of-kings-bp-assistant` /
`xia-gu-bp` / `FearlessDraft`），有四条真用上了：

| 来源 | 吸收的点 | 具体落在哪 |
|---|---|---|
| **Fire-Phoenixs/kpl-bp-dataset** | 官方还有一套 `prod.comp.smoba.qq.com` 接口，`battle/open` 带 `bp_list` | 整个 ban 数据链路（24213 条） |
| 同上 | 「完整对局」要校验 `picks==10 && bans∈{8,10}`，否则统计会脏 | `verify_rules.py` 的手数分布检查 |
| **Andndre/hok-draft-engine** | 1. **分阶段权重**（ban1/pick1/ban2/pick2 该用不同权重）<br>2. **前瞻优于贪心**（它用 Minimax 深度 1~4 跑在 Web Worker）<br>3. **flex lane**（英雄可打多路，别硬绑） | 2 直接做成 `planner.py`；1、3 记进「已知限制」，还没做 |
| **Fire-Phoenixs/honor-of-kings-bp-assistant** | 「赛后队伍总评」+ 可复盘 BP 文本 | 复盘模式的报告区 |

没吸收的（不是不好，是当前没这需求）：它的 10 ban 五排赛制、`xia-gu-bp` 的实时悬浮窗（要读游戏进程）、
`FearlessDraft` 的 LoL 英雄数据。

---

## 九、HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/meta` | 赛季 + 战队列表 |
| GET | `/api/heroes?season=` | 英雄池 + 出场/ban/胜率 |
| GET | `/api/hero/<id>` | 英雄详情：压制谁 / 怕谁 / 常用选手 |
| GET | `/api/bans` | ban 榜（各来源汇总） |
| GET | `/api/matchups?min_games=` | 对位矩阵规模 |
| GET | `/api/matches?source=&only_played=1` | 赛程（`official`｜`sim`） |
| GET | `/api/match/<schedule_id>` | 某场每小局的 ban + 选人 |
| GET | `/api/drill/list` | 可复盘的对局 |
| POST | `/api/drill/start` | `{schedule_id, round}` 开一局复盘 |
| POST | `/api/season` | `{season}` 切当前赛季 |
| POST | `/api/bp/new` | `{bo, blue, red, config}` 开新比赛 |
| POST | `/api/bp/<id>/act` | `{hero_id}` 落一手（复盘会话会自动对答案） |
| POST | `/api/bp/<id>/undo` | 撤销一手 |
| POST | `/api/bp/<id>/winner` | `{side}` 记录小局胜方 |
| POST | `/api/bp/<id>/save` | 写进 ban 库 |
| POST | `/api/bp/<id>/reset` | 重开 |

---

## 十、测试

```bash
python scripts/verify_rules.py      # 拿真实 bp_order 逐手核对规则表（先跑这个）
python scripts/e2e.py               # 整场 BO5 端到端
python scripts/test_bo7.py          # 巅峰对决规则（纯逻辑）
python scripts/test_drill.py        # 10 ban / ban 榜 / 复盘
python scripts/test_reasons.py      # 推荐理由的语义（出场 / 被 ban / 对面选手）
python scripts/test_save_bans.py    # 保存本局 → ban 进榜
python scripts/test_counter.py      # 对位矩阵 / 英雄详情 / 赛季切换
python scripts/smoke.py             # 接口连通性 + 空库确认
python scripts/clean_sim.py         # 清模拟数据（--yes 才真删）
```

---

## 十一、已知限制

- **11/2415 局的手顺序与规则表不一致**：零星异常，分散在 4 个赛季，没去追根因；
  复盘模式不受影响（它用真实序列，不用规则表）
- **对位数据跨版本混用**：已加 `0.85^赛季差` 衰减，但同一赛季内的版本迭代还是看不出来
- **2025 赛季的战队/选手名单拿不到**：`getTeamsList` 对 2025 三个赛季返回空，
  「对手英雄池 / 我方熟练」两路信号为空（选人数据本身是全的）
- **flex lane 没做**：英雄现在只绑一个主分路（按实际站位投票），
  能打多路的英雄（比如某些中单/辅助）在补位推荐里会吃亏
- **分阶段权重没做**：ban1 / pick1 / ban2 / pick2 目前共用一套权重
- **对位样本仍然薄**：2442 局 ≈ 12000 个同分路对位，摊到 133 个英雄上，每对还是只有个位数
- 单进程、单 SQLite 连接、全局锁串行；本地单人够用

---

## 十二、目录

```
kplbp/
  sources.py   两套官方接口 + php-querystring 解析（五个坑的注释都在这）
  store.py     schema、写入、聚合、对位矩阵、BP 顺序、camp 映射
  counter.py   对位克制（贝叶斯收缩 + 置信度）
  planner.py   全局 BP 前瞻
  ingest.py    抓取 CLI（多赛季 / --with-bp）
  rules.py     20 手规则表（逐手核对过）+ 可配置项
  engine.py    状态机（custom_steps 支持复盘）
  suggest.py   推荐打分
  server.py    API + 静态
web/           前端：赛季切换 / 右键英雄详情 / 复盘模式
data/kpl.db    数据库
scripts/       验证与测试
```
