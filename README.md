# KPL BP 模拟器

王者荣耀职业联赛的 Ban/Pick 模拟器，数据来自腾讯官方赛事接口。

- 按 KPL 赛制走完整 BP（20 手，双方各 5 ban 5 pick）
- 用真实对局数据给 ban / pick 建议
- 复盘职业对局，逐手对比命中
- 可切换赛季

```
python -m kplbp.ingest --seasons 6 --with-bp   # 抓 6 个赛季 + 官方 BP 记录（约 30 分钟）
python -m kplbp.server                          # 起服务
# http://127.0.0.1:8780/
```

零依赖，Python 3.10+ 标准库（`sqlite3` / `http.server` / `urllib`）。

---

## 一、数据来源

用到两套官方接口，字段互补。

| 接口 | 域名 | 提供 | 缺 |
|---|---|---|---|
| kplow | `kplshop-op.timi-esports.qq.com` | 赛季、战队、选手、赛程、每局选人 | 不返回 ban |
| smoba | `prod.comp.smoba.qq.com/leaguesite` | 每局完整 20 手 BP，含 ban 和顺序 | 不返回选手名单 |

只用 kplow 的话 ban 统计为 0，所以两套都要接。

### 1. 英雄库

```
GET https://pvp.qq.com/web201605/js/herolist.json
```

133 个英雄，字段 `ename / cname / title / hero_type / roles`。编码是 UTF-8，按 GBK 解会乱码。

### 2. kplow

```
POST https://kplshop-op.timi-esports.qq.com/kplow/<endpoint>
```

base 地址没有公开文档，从 `kpl.qq.com` 的前端 bundle 里读出来的（`ci = ${baseUrl}/kplow`，`baseUrl` 来自 `Cpe()`）。

可用接口：`getSeasonAndStageAndTeamList`、`getTeamsList`、`getScheduleList`、`getScheduleDetail`、`getHeroRankList`、`getPlayerRank`、`getSeasonPlayerPerf`、`getScheduleProgress`。

### 3. smoba

```
GET https://prod.comp.smoba.qq.com/leaguesite/leagues/open
GET https://prod.comp.smoba.qq.com/leaguesite/matches/open?league_id={league_id}
GET https://prod.comp.smoba.qq.com/leaguesite/battle/open?battle_id={battle_id}
```

`battle/open` 的 `data.bp_list` 是按出手顺序排列的数组：

```json
{"camp": 1, "is_ban_or_pick": 1, "hero_id": 135, "hero_name": "项羽", "position": 3}
```

接入后本库有 24213 条 ban 记录，覆盖 2442 / 2745 小局。

---

## 二、五个接口坑

### 1. kplow 返回的不是 JSON

是 PHP 风格的扁平 querystring，键本身也被 urlencode：

```
data.seasons%5B0%5D.season_name=2026KPL%E5%B9%B4%E5%BA%A6%E6%80%BB%E5%86%B3%E8%B5%9B
```

解析顺序：先 `parse_qsl` 解出 `data.seasons[0].season_name`，再按 `[n]` 建树。直接对原始文本做正则匹配不会命中。

`seasons` 的下标是稀疏的，`[0]` 之后直接跳到 `[10]`，按连续下标切会错位。

### 2. `getScheduleDetail` 参数名错一个，静默返回另一场

```
schedule_id=xxx   → 31339 字节，有数据    参数被忽略，返回最近一场
scheduleid=xxx    → {"result":10020003,"msg":"schedule not found"}
```

正确的参数名是 `scheduleid`，且必须同时传 `seasonid`。

这种情况不会报错，只能靠数据特征发现：每场比赛的选人数不可能完全相同。第一次抓完 53 场，每场都是 69 条选人、46 个英雄。

### 3. `position` 的编号不是通用定义

官方口径是 `1 对抗路 / 2 中路 / 3 发育路 / 4 打野 / 5 游走`，和常见的 `2 打野 / 4 发育路` 相反。

验证方式：沈梦溪（中路）记为 2，裴擒虎（打野）记为 4。

### 4. `is_ban_or_pick` 的 1 表示 pick

字段名像是「是否 ban」，实际值为 1 时是选人。用 `battle_player_list` 的 10 个英雄比对确认：

```
被玩家实际选用的英雄，其 is_ban_or_pick 取值分布: {1: 160}
```

所以 1 = pick，0 = ban。

### 5. `camp1/camp2` 不等于 `team_a/team_b`

`camp1` 指先 ban 的一方，每小局会换边。复盘时直接拿 `team_a` 当蓝方会把蓝红摆反。

解决方式不用联网：拿 `bp_order` 里 camp1/camp2 的选人集合，和 `picks` 表里各战队的选人集合比对，两边都在库里。`backfill_bp_sides()` 用这个方法补了 2406 局。

---

## 三、数据库

```
heroes      133    英雄（lane 按实际站位出现频率得出）
seasons      24
teams        48    战队，跨 6 个赛季
players     405
matches     651    官方比赛
games      2745    小局
picks     27380    选人
bp_order  48568    真实 BP 手数（2442 局 × 约 20 手）
bans      24213    官方 ban，source='official'
matchups   2636    同分路对位
bp_sides   2406    camp 到战队的映射
```

`v_pick_stat` / `v_ban_stat` / `v_player_hero` 三个视图都带 `source` 字段（`official` / `sim` / `manual`），官方数据和模拟数据分开统计。

---

## 四、BP 规则

规则在 `kplbp/rules.py`。数字来自 `bp_list` 逐手核对。

一局 20 手，双方各 5 ban 5 pick：

```
1 蓝ban  2 红ban  3 蓝ban  4 红ban
5 蓝pick 6 红pick 7 红pick 8 蓝pick 9 蓝pick 10 红pick
11 红ban 12 蓝ban 13 红ban 14 蓝ban 15 红ban 16 蓝ban
17 红pick 18 蓝pick 19 蓝pick 20 红pick
```

`scripts/verify_rules.py` 对 2442 局真实序列逐手比对：

```
手数分布: {5:4, 6:3, 7:9, 8:2, 9:2, 19:7, 20:2415}
与 KPL_10BAN 逐手比对：不一致 11 局（2404 局完全一致）
```

不一致的 11 局分散在 4 个赛季，没追根因。

### 现行 KPL 不是全局 BP

初版默认开启全局 BP（用过的英雄后续小局锁定），与数据不符：

```
BO5: 492 场  → 无跨局重复 9 场，有重复 483 场
BO7:  88 场  → 有重复 88 场
```

单场例子：

```
第1局 picks=[敖隐,张飞,海月,女娲,苏烈,赵云,夏洛特,达摩,裴擒虎,百里守约]
第2局 picks=[关羽,少司缘,猪八戒,大司命,戈娅,安琪拉,莱西奥,墨子,女娲,赵云]
```

女娲和赵云在两局都出现。现行 KPL 是每局独立 BP，`global_bp` 默认 `false`，前端留了勾选框给世冠、挑战者杯这类全局 BP 赛事。

可配置项：

| 键 | 默认 | 含义 |
|---|---|---|
| `ban_slots` | `10` | 每队 ban 数（现行 5，老赛制 4 对应 18 手） |
| `global_bp` | `false` | 全局 BP |
| `lock_bans` | `false` | 全局 BP 时被 ban 的是否也锁 |
| `blind_last` | `true` | BO7 第七局巅峰对决（10 手盲选，无 ban） |
| `second_ban_red_first` | `true` | 第二轮 ban 红方先 |

---

## 五、推荐算法

`kplbp/suggest.py`、`counter.py`、`planner.py`。

| 信号 | 来源 | ban / pick 权重 |
|---|---|---|
| `strength` | 真实出场次数 + 胜率 | 0.45 / 0.35 |
| `opp` | 对手战队英雄池 | 0.35 / — |
| `own` | 我方战队英雄池 | — / 0.22 |
| `need` | 已选英雄的分路覆盖 | — / 0.18 |
| `counter` | 同分路 1v1 胜负 | 0.20 / 0.12 |
| `plan` | 模拟剩余出手 | — / 0.13 |

### 强度不用 presence

`presence = 出场 + 被 ban`。用它当强度会形成回路：

```
被 ban 多 → presence 高 → 推荐去 ban → 被 ban 更多
```

海月：出场 57，被 ban 136，presence 193。按 presence 排在 ban 榜第一，但它只是因为大家都在防。改成出场次数 + 胜率后降到第 5。

标签也拆开了，原来把 193 写成「本赛季出场 193 次」，现在是 `出场 57 · 被 ban 136`。

### 招牌选手取对手阵容里的人

`hero_rank.best_player` 是全联盟数据。本场对手不是那支队时，这个人不在场上。

改为从 `opp_pool`（对手战队英雄池）取，显示 `对面 流星 9 场 78%`。少于 5 场只报场次不报胜率。

### 第一轮 ban 的权重分配

第一轮 ban 时我方没有已选英雄，`counter` 没有输入。0.20 的权重挂在常数 0.5 上会把候选压成窄带。这时按 6:4 分给 `strength` 和 `threat`。

### 对位克制

每小局蓝红双方 position 相同的两个英雄互为对位，按胜负计一次。

样本量小：133 英雄 × 5 分路，一万多场对位摊到每对只有个位数。用贝叶斯收缩：

```
wr' = (w + k·0.5) / (g + k)        k = 6
```

3 局全胜得 0.67，30 局全胜得 0.94。再乘置信度 `min(1, 总局数/20)`。

早期版本用 min-max 归一化，2 局样本的 0.625 会被拉到 0.938，已改掉。

### 版本衰减

每个赛季的样本乘 `0.85 ** 赛季差`：

```
最新赛季 1.00 · 上季 0.85 · 0.72 · 0.61 · 0.52 · 0.44
```

### 前瞻

`planner.lookahead()` 对前 30 个候选各模拟一次：假设取 X，双方按剩余顺序轮流取当前最强且能补分路的英雄，累计我方阵容强度，取最大。一层前瞻加双方贪心，不是 Minimax。

输出示例：

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

## 六、复盘模式

在「对局数据」里点某局的「复盘这局」。

- 加载该局真实的 20 手顺序，按顺序出手，每手立即显示与职业选择是否一致
- 累计命中数和命中率
- 对手就是那场比赛的 BP 记录

没有单独写引擎。`engine.steps()` 增加了 `custom_steps` 入口，按真实序列走，巅峰对决的 10 手盲选也能复现。

```
[复盘] 启动 session=723d60c9b466 总手数=20
[复盘] #1 不同  我=廉颇  职业=钟馗
[复盘] #2 不同  我=小乔  职业=鲁班大师
[复盘] 蓝方=北京WB 红方=广州TTG
```

可复盘 581 场。其余场次 smoba 没有 `bp_list`，或无法与本地场次对应。

---

## 七、保存本局与手工补 ban

打完一局点「保存本局」，写入 `source='sim'`。

| 字段 | 来源 | 原因 |
|---|---|---|
| `picks` / `wins` | 只取 `official` | 模拟局胜负是手点的，会计入真实胜率 |
| `bans` | 所有来源求和 | 官方、模拟、手工都计入 |

官方 `bp_list` 没覆盖的场次可以手工补：

```bash
python scripts/import_bans.py --template > bans.csv
python scripts/import_bans.py bans.csv --dry-run
python scripts/import_bans.py bans.csv          # 写入 source='manual'
```

---

## 八、参考过的项目

| 项目 | 用到的部分 |
|---|---|
| [Fire-Phoenixs/kpl-bp-dataset](https://github.com/Fire-Phoenixs/kpl-bp-dataset) | 发现 `prod.comp.smoba.qq.com` 接口和 `bp_list` 字段；完整对局的校验条件 `picks==10 && bans∈{8,10}` |
| [Andndre/hok-draft-engine](https://github.com/Andndre/hok-draft-engine) | 前瞻搜索的思路（它用 Minimax） |
| [Fire-Phoenixs/honor-of-kings-bp-assistant](https://github.com/Fire-Phoenixs/honor-of-kings-bp-assistant) | 复盘报告的呈现方式 |

hok-draft-engine 的分阶段权重和 flex lane 还没做，记在已知限制里。

---

## 九、HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/meta` | 赛季 + 战队 |
| GET | `/api/heroes?season=` | 英雄池，含出场 / ban / 胜率 |
| GET | `/api/hero/<id>` | 英雄对位详情 |
| GET | `/api/bans` | ban 榜 |
| GET | `/api/matchups?min_games=` | 对位矩阵规模 |
| GET | `/api/matches?source=&only_played=1` | 赛程 |
| GET | `/api/match/<schedule_id>` | 某场每小局 ban 与选人 |
| GET | `/api/drill/list` | 可复盘对局 |
| POST | `/api/drill/start` | `{schedule_id, round}` |
| POST | `/api/season` | `{season}` 切换赛季 |
| POST | `/api/bp/new` | `{bo, blue, red, config}` |
| POST | `/api/bp/<id>/act` | `{hero_id}` |
| POST | `/api/bp/<id>/undo` | 撤销 |
| POST | `/api/bp/<id>/winner` | `{side}` |
| POST | `/api/bp/<id>/save` | 写入 ban 库 |
| POST | `/api/bp/<id>/reset` | 重开 |

---

## 十、测试

```bash
python scripts/verify_rules.py      # 用真实 bp_order 核对规则表
python scripts/e2e.py               # BO5 端到端
python scripts/test_bo7.py          # 巅峰对决规则
python scripts/test_drill.py        # 10 ban / ban 榜 / 复盘
python scripts/test_reasons.py      # 推荐标签语义
python scripts/test_save_bans.py    # 保存本局后 ban 入库
python scripts/test_counter.py      # 对位矩阵 / 英雄详情 / 赛季切换
python scripts/smoke.py             # 接口连通性
python scripts/clean_sim.py         # 清模拟数据（--yes）
```

---

## 十一、已知限制

- 11 局的手顺序与规则表不一致，分散在 4 个赛季，未追根因。复盘模式用真实序列，不受影响
- 对位数据跨版本使用 `0.85^赛季差` 衰减，同一赛季内的版本变化无法区分
- 2025 三个赛季 `getTeamsList` 返回空，对手英雄池和我方熟练度两路信号为空。选人数据本身完整
- 英雄只绑一个主分路，能打多路的英雄在补位推荐里会偏差
- ban1 / pick1 / ban2 / pick2 共用一套权重
- 对位样本每对只有个位数
- 单进程、单 SQLite 连接、全局锁串行

---

## 十二、目录

```
kplbp/
  sources.py   两套官方接口与解析
  store.py     schema、写入、聚合、对位矩阵、BP 顺序、camp 映射
  counter.py   对位克制
  planner.py   前瞻
  ingest.py    抓取 CLI
  rules.py     规则表与配置
  engine.py    状态机
  suggest.py   推荐打分
  server.py    API 与静态服务
web/           前端
data/kpl.db    数据库
scripts/       验证与测试
```
