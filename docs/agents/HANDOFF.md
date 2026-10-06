# HANDOFF.md — 智能体交接记录

> **用途：换智能体时，交接方写"我做到哪了"，接手方读完就知道从哪继续。**
> 每次交接在下方**追加一个交接块，最新的在最上面**。
> ⚠️ 本文件协议要求"顶块全读"，所以**只保留最近 1~2 块**，更早的归档到 `docs/_archive/`。

---

## 交接块模板

```markdown
### 交接：YYYY-MM-DD（A → B）

**状态来源**：HEAD / 工作树 / 是否推送 / 服务是否在跑（都写实测值）

#### 一、当前状态
#### 二、下一步（具体到可执行）
#### 三、注意事项（坑、别动的文件、未对齐的地方）
#### 四、关键文件
```

---

## 交接记录

### 交接：2026-10-06（会话六 → 会话七）—— ✅ **前端「信息密度瘦身」六条处方全部落地**

> 前一个智能体（会话六）出现思考循环、主动交接；本块由**会话七**接手完成后重写。
> 上一版交接块留的题（六条处方 + 第 1 条未提交）已全部做完，故本块改为**完成记录 + 下一步**。

**一句话**：用户反馈新前端「整体交互逻辑和信息面对于人类来说有点变态」，
经 **诊断 → 对标三个开源产品 → 写规范 → 加可断言红灯 → 改页面** 五步，
六条瘦身处方**全部落地并各自提交**。

#### 当前状态（2026-10-06 20:35 实测）

| 项 | 值 |
|---|---|
| HEAD | 本轮 **10 个 commit**（`b571a9d` 第4条 → … → 字号收敛 → **B-6 删旧前端**） |
| 工作树 | 干净（收尾 commit 后） |
| 门禁 | ✅ pytest **831 passed / 2 skipped** ｜ ✅ 前端真机 **78/78** ｜ ✅ 数据契约 **24 项 0 失败** ｜ ✅ `tsc --noEmit` 0 ｜ ✅ `vite build` 绿 |
| 服务 | 起在 `:5020`（需先 `precompute` 预热，否则密度**假低**） |

#### 六条处方落地情况（详见计划书 §13.8「落地记录」）

| # | 内容 | 状态 | 实测（1440×900，数字节点 / 页高屏） |
|---|---|---|---|
| 1 | 曲线去网格（只留形状 + 零轴 + tooltip） | ✅ | 今天 80→74（y 刻度不再计入） |
| 2 | 「今天」瘦成 大数字 + 结论句 + 曲线 | ✅ | 30 → **24** / 1.32 → **1.27** |
| 3 | 数据链路从「今天」搬到「设置」，默认折叠 | ✅ | 今天 50→30 / 研究 107→87 |
| 4 | 持仓表从「今天」移走 | ✅ | 今天 74→50 / 2.27→2.05 |
| 5 | 行情 + 消息面压成一行小条（`MarketStrip`） | ✅ | 今天页高 1.32→1.27 |
| 6 | 筛选池可视列 6 → 4（代码/名称/夏普/近3月） | ✅ | 研究 87→**75** |

**本轮总账（1440×900 数字节点 / 页高屏，基线已同步下调）**

```
今天   80 / 2.3  →  24 / 1.27    （基线 30 / 1.5）✅ 已达 DESIGN 目标（≤40）
研究  108 / 1.7  →  75 / 1.23    （基线 85 / 1.5）
设置  129 / 2.4  →  43 / 1.81    （基线 55 / 2.1）
持仓   53 / 1.6  →  51 / 1.55    （基线 53 / 1.6，本页未瘦）
```

#### 本轮**顺手改掉**的三处非计划内问题（都在 commit 消息里写明了）

1. ⚠️ **密度判据测不出"折叠"**：Chrome 121+ 关闭态 `<details>` 用 `content-visibility: hidden`，
   子孙 `display` 仍非 `none`、rect 非零 → 旧 `DENSITY_JS` 把折叠内容也算进密度
   （实测：搬来折叠卡后「设置」反而 128→**148** 假涨）。已改为跳过 closed details。
2. **结论徽标自相矛盾**：原按 `need_rebalance` 判定，而本机 `need_rebalance=true` 且
   `instructions=[]`，文案写着"无需任何买卖操作" → 改为按**有无非持有指令**判定。
3. **量化模型三表默认展开**：计划书 §13.2 点名它是"审计内容混进日常界面"。
   收进折叠区后本卡 1070px → 约 150px，同时不再压住整行高度。

⚠️ **两条**为保住密度基线上限而做的**等价替换**（不是新增内容）：
「设置」页运维命令表默认折叠（DESIGN §5.1「命令表一律默认折叠」）；
「持仓」页 `PlanProgress` 去掉与进度条重复的「已投 X%」，腾出的额度给
从「今天」搬来的「已实现盈亏」。

#### 后续轮（同日，会话七续）：继续瘦身「研究 / 持仓」+ **修好密度判据的漏计**

> 用户指定做这项（其余两项：字号收敛、B-6 删旧前端，见下方「下一步」）。

⚠️⚠️ **判据口径今天被改了第二次，数字与上一节不可比**：
旧口径只数"叶子元素"，把 `<span class="mono"><i>▲</i>+1.2%</span>` 这类
**"箭头 + 数值"的混合内容整段漏掉**（本项目涨跌大量这样渲染）——
实测漏计 研究 **55** / 持仓 14 / 今天 7 / 设置 10。已把 `DENSITY_JS` 改成数
"含数字的**文本段**"（阈值含义不变，只是不再漏数）。同一天同一构建下：
**今天 24→31、持仓 51→65、研究 75→130、设置 43→53**。
`DENSITY_BASELINE` 已按新口径整体重设（口径变更，不是放宽）。

**本轮瘦身实测（新口径）**

| 页 | 瘦身前 | 瘦身后 | 新基线 |
|---|---|---|---|
| 研究 | 130 | **76**（-42%） | 82 |
| 持仓 | 65 | **59**（-9%） | 64 |
| 今天 / 设置 | 31 / 53 | 未动 | 34 / 57 |

- **研究**：行业板块可视列 6→3（行业/近1月/评分），「近3月 / 近6月 / 波动」进**逐行展开**；
  默认行数 12→8；「动量领先」去掉每枚 chip 重复的「近3月」标签（改在标签里说一次）。
- **持仓**：建仓计划的 6 格"计划元数据"**整块去掉** —— 它与「设置 → 投资计划」逐字重复，
  按 DESIGN §5.4 页面职责，配置归「设置」，本页只回答"计划走到哪了"（保留逐只进度）。
  另去掉定投卡副标题里重复的条数。

#### 后续轮 2（同日）：**字号收敛 —— §13.6 最后一条落地，六条处方全部完成**

`@theme` 补上 **7 档 `--text-*`**（title 24 / hero 48 / metric 16 / body 13.5 /
body-sm 12.5 / caption 11.5 / micro 10），**24 个文件 164 处**裸字号一次换掉，
`body` 与 `.field` 也改走 token → 裸字号 **0 处**；DESIGN §2 重写为唯一来源。
判据**从"0.5px 阶梯 + 种类 ≤16"收紧为「无裸字号 + 阶数 ≤8」**（折中作废）。
⚠️ **刻意不给 `--text-*--line-height`**（保持从 body 继承 1.6）→ 四页页高变化 ≤0.02 屏、数字节点不变。

⚠️ **这一轮当场炸出两个问题（都是判据自己抓的），详见计划书 §13.10.3**：

1. **`层级` 判据变假绿**：它靠 `h1/h2` 的 `className` 里的 `text-[Npx]` 解析字号，
   改 token 后取不到值 → 旧实现 `continue` → **两条断言双双 PASS**（detail 写着"未取到字号"）。
   → 已修（新增 `_text_token_px()`），并**新增「h1/h2 字号必须能解析」一条断言**：
   **"判据看不到数据"必须判红，不是跳过。**
2. **「今天」页基线是在降级态测的**：该页数字与行情端点可用性强相关 ——
   不可用 31 / 可用 **42**（4 组指数点位+涨跌 ≈ +9）。我把基线设成 34 时只测了降级态
   → 一轮 verify 就被抓出来。现按正常态取 **46**。
   ⚠️ **如实记**：联网态 42 比目标 40 **多 2**，来源就是那 4 组指数报价。

#### 后续轮 3（同日）：**B-6 删旧前端 —— B-0~B-6 施工全部完成**

- 删 `src/web/static/app.js` / `app.css`、`src/web/_archive/dashboard.html`（空目录一并删）、
  `scripts/check_rise_fall.py`。
  ⚠️ **`src/web/static/favicon.svg` 保留** —— 计划里没提它，但**新前端的
  `frontend/index.html` 引用了 `/static/favicon.svg`**（Flask 默认静态路由提供），删了就是一条 404。
- ⚠️ **计划漏列的 3 个文件（跑 pytest 才炸出来，这才是 B-6 真正的坑）**：
  删 `tests/test_curve_render.py`、`tests/test_recommend_render.py`
  （**旧前端的静态哨兵**：模块导入时就 `read_text()` 读 `app.js`/`app.css` 源码，被测对象一删
  连**收集**都过不去 → 整轮 pytest `Interrupted`）、`_diag_frontend.py`（在 `scripts/` 下）
  （一次性探测脚本，读 `app.js` + 已在 B-5 移走的 `dashboard.html`，其实早就坏了）。
  ⚠️ **测试总数 845 → 831**（14 个用例）属"被测对象已删除"的**正常下降**，不是丢测试 ——
  同时新增了 5 条前端静态判据（涨跌色 2 + 字号 2 + 层级"可解析"1）。
  ⚠️ **教训**：删代码前要 `grep` 一遍"谁在读这些文件"，**含 `tests/`，并预期"静态哨兵"这种形态**；
  本轮是靠"删完立刻跑 pytest"补上的。
- **替代守卫（删旧守卫的前提）**：`verify_frontend.py` 新增 **2 条静态断言** —
  ① `--color-rise` 红 / `--color-fall` 绿（中国惯例）+ `dirClass()` 映射不反转；
  ② **重复常量** `lib/chartColors.ts` 的 `RISE/FALL` 必须与 `@theme` 同值（Recharts 不认 CSS 变量）。
  ✅ **做了负对照**：把两个色值对调后这 2 条立刻红（9/11），改回即绿。
- `app.py`：删掉**从未被任何路由使用**的 `STATIC_DIR`；更新 `/v2` docstring（原文说"旧前端仍挂在
  `/` 上、app.js 还在（B-6 才删）"，已过期）。`/v2*` → 301 **保留**（给书签/外链用）。
- ⚠️ **验收口径要改**：计划书原文写「`grep -r "app\.js"` 无仓库内残留」，但那会与项目自己的
  **"不改历史文档"** 规则冲突（多份历史任务书/执行报告引用了这些文件）。
  已按 `test_doc_refs.py` 里 `templates/dashboard.html` 的**先例**办：**登记 EXCEPTIONS + 写理由**，
  只改"活文档"（`AGENTS.md` 的当前态、计划书 B-6 与 §11.3 行、本文件）。
- 顺带修了 `AGENTS.md` 的**过时基线**：806→**831** 项（其中 845→831 是 B-6 删掉 2 个
  旧前端哨兵测试的**正常下降**，不是丢测试，见计划书 §12 B-6 第 4 条）、前端真机 33→**65** 项、前端段落的
  "过渡期两套并存"→ 已全部落地。

#### 后续轮 4（同日）：**把这几轮"测出来但没改"的坑修掉**（详见计划书 §13.11）

> 用户原话：「刚才不是测出来很多坑？修复一下」。修的是**已判定为缺陷、当时只记下来**的四类；
> 需要用户拍板的**三个产品取舍**不在其列（仍在下面「下一步」里）。

1. ⚠️ **DESIGN §5.2 有 3 行只是愿望、从没写成断言**（首屏 / 单卡 / 移动页高）—— 实测**全超标**
   （首屏 25/51/76/42 vs ≤15；单卡 16/34/41/24 vs ≤8；设置移动页高 3.52 vs ≤3.5）。
   已按同一套「基线上限」接上：新增 `DENSITY_DETAIL_BASELINE` + **12 条断言**，真机 65 → **77**。
   ⚠️ 这正是 DESIGN §7 第 4 条的反面教材：「写不成断言的，不许只写在文档里当愿望」。
2. ⚠️⚠️ **`tests/test_doc_refs.py` 的守卫自己有个洞**：`SKIP_MARKERS` 里的 `'*'` 是**行级**判据，
   而 `**加粗**` 也含 `*` → **任何带加粗的行整行不检查**。
   ✅ 先做**负对照**证明洞存在（注入含 `**加粗**` 的断链行 → 守卫照样通过），
   再去掉那个多余的 `'*'`（`PATH_RE` 本来就匹配不上 `*`）→ 负对照立刻红，
   并**当场暴出 6 处长期被吞的真断链**（2 处是漏写 `frontend/` 前缀、1 处是我自己写的、
   1 处是 AGENTS.md 举例时引用了错的路径、2 处登记为 EXCEPTIONS：跨项目技能引用 / 已废弃脚本）。
   ⚠️ 教训已写进 AGENTS.md：**行级跳过标记越宽，判据越像装饰**。
3. **全站最后一处裸色清零**：`PoolBoard` 的 `#3d3117` → `warn/30 + bg-warn/10`，
   `_BARE_COLOR_ALLOW` 现为 `{}` → 真机输出「白名单 **0** 处」。
4. **DESIGN §1 颜色表按实现重写**：原来的 `--up`(绿)/`--down`(红) 是**欧美惯例**，
   照它实现就会"盈利显示成绿" —— 已换成与 `index.css` 一致的 19 个 `--color-*` + 两条硬约束。

⚠️ **仍未做**：`.claude/skills/` 那份从毕设迁来的技能副本与本项目 `.workbuddy/skills/` 那份
长期可能漂移（引用毕设目录已登记 EXCEPTIONS），**是否删 `.claude/` 那份属你的环境决策**。

**同一轮追加的两条（更早几轮的坑）**：

5. ⚠️⚠️ **最老的坑终于有机械守卫了**：从交接第一天就写着「必须先 `precompute`，否则冷缓存 →
   密度**假低**」，但那**只是叮嘱**。现 `DENSITY_JS` 顺带检测页面上的 `<Warming>`
   「正在计算…还需约 N 秒」，密度段**先做一条前置断言**：检测到 warming 就**判红**
   （提示先 precompute），不再拿骨架屏的数字去比基线。✅ 负对照：注入
   `{ok:true,data:null,status:"warming"}` 的响应 → 检测器 true；正常态 false。
6. **「跑 pytest 的正确姿势」原来只在交接块里，没进入口文档** —— `AGENTS.md` §2.2 命令表
   只写了 `python -m pytest tests/ -q`，照它做**必踩** safe-delete 的 ERROR 陷阱。
   已把完整调用（换全新 basetemp + 串行 + `CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=100000`）写进命令表。

> ⭐ **这两条共同的教训**（值得记住）：**坑写在交接块里 ≠ 坑被封住**。
> 交接块会被归档、会被跳过；只有**入口文档**（AGENTS.md）与**判据**（verify/pytest）是必经之路。

#### 下一步（按优先级）

> ⚠️ **施工项（B-0~B-6）已全部完成**，剩下的是**待用户拍板**的事项。

1. ⚠️ **待拍板：DESIGN §5.2 的"数字节点 ≤40"是否对**表格页**合适？**
   实测「今天」31~42（联网态 42 略超）、「研究」76、「持仓」59，其构成是**表格内容本身**
   （研究两张表 8 行 × 3 字段；持仓表 7 只 × 4 字段 ≈ 28 就是下限）。
   见计划书 §13.9.3，**没有擅自改 §5.2 的目标值**。
2. ⚠️ **待拍板（三个产品取舍，见 §13.9.3 / §13.10.3）**：
   ① 研究「筛选池每板块 8 → 5 只」（省约 9 个读数）；
   ② 持仓「去掉收益率% 列」（省 7；但它是跨基金可比大小的唯一指标）；
   ③ 今天「行情小条去掉指数点位」（省 4 → 联网态 38，可回到 ≤40）。
3. ⚠️ **回滚 B-6 的话**：文件只在 git 历史里（`git show 3425ed5:src/web/static/app.js`）。
   真要恢复"旧前端可用"，得同时恢复 `app.py` 的 `/` 路由 —— 不建议，那是把 B-5 一起退回去。

#### 开工三步（与上一版相同，未变；只是真机项数 **78**）

```bash
cd /d/DSH/projects/Quantitative-Fund-auto

# ① 起服务（后台）+ 预热快照 —— ⚠️ 不预热会得到假数字
C:/Users/m1309/AppData/Local/Programs/Python/Python313/python.exe src/main.py web
C:/Users/m1309/AppData/Local/Programs/Python/Python313/python.exe src/main.py precompute

# ② 验收四件套
C:/Users/m1309/AppData/Local/Programs/Python/Python313/python.exe -m pytest tests/ -q          # 831 passed / 2 skipped
C:/Users/m1309/AppData/Local/Programs/Python/Python313/python.exe scripts/verify_frontend.py   # **78/78**
C:/Users/m1309/AppData/Local/Programs/Python/Python313/python.exe scripts/check_ledger_invariants.py  # 24 项 0 失败
cd frontend && D:/nodejs/node.exe node_modules/typescript/bin/tsc -b --noEmit && D:/nodejs/node.exe node_modules/vite/bin/vite.js build

# ③ 推送（⚠️ 必须 SSH 显式 URL，HTTPS 不通）
git push git@github.com:Mo12y/Quantitative-Fund-auto.git main
```

#### ⚠️ 坑（本轮新增，接上上一版的 7 条）

8. ⚠️ **折叠区不能只看 `display`**：判"在不在屏上"要显式排除 `details:not([open])` 的子孙
   （见上面"顺手改掉 1"）。
9. ⚠️ **Bento 栅格里"加一张卡"未必加页高**：「设置」页第二行的高度由左侧
   「量化模型」（当时 1070px）决定，右列里堆卡不增加页高；单独开一行（span 12）才 +120px。
   搬卡进某页前**先量同行最高者**。
10. ⚠️ **密度基线的"只许往下调"遇到"搬内容"会冲突**：本轮靠**同页等价替换**
    （折叠命令表 / 去掉重复的已投%）解决，不上调任何一页。以后搬内容请照此办理。
11. ⚠️⚠️ **判据会"看不见"混合内容**：只数"叶子元素"会把
    `<span class="mono"><i>▲</i>+1.2%</span>` 整段漏掉（外层有子元素、内层箭头没数字）。
    本项目涨跌全是这种渲染 → 漏计达 研究 55 / 持仓 14。**改判据后必须在同一天同一构建上
    重测四种页**并整体重设基线（否则新口径下全红）。改口径时要在注释里写清
    "与旧值不可比"与漏计证据，否则后人会以为是在放宽。
12. ⚠️⚠️ **"判据看不到数据"必须判红，不许跳过**：字号改 token 后，`层级` 判据因为
    `h1/h2` 的 className 里不再有 `text-[Npx]` 而**取不到字号** → 旧实现 `continue`
    → 两条断言**双双 PASS**（detail 里还写着"未取到字号"）。凡"解析失败/字段缺失"
    的分支，默认必须是 FAIL 或显式报出，绝不能默认通过。
13. ⚠️ **基线要在"正常态"下测，别测到降级态**：「今天」页的数字与**行情端点是否可用**
    强相关（不可用 31 / 可用 42）。我把基线设成 34 时只测了降级态 → 一轮 verify 就红。
    **凡是"某块外部依赖可能降级"的页面，定基线时要确认当前处于哪一态**（看 `/api/market/live`
    的 `available`）。

#### 约束（未变）

- **不动 `data/fund_quant.db`**（`precompute` 会写 `analysis_snapshot` 缓存表，属正常读缓存，非账本变更）。
- **不新增运行时依赖**（`@container` 用的是 Tailwind v4 内建容器查询，不是新依赖）。
- **不顺手扩大范围**：新问题记在 commit 与本节，不悄悄改。
- **B-6（删旧前端）不在本轮**。

> 上一版交接块（会话六，含六条处方的**原始**对照参照与 7 条坑）已并入本节；
> 其内容要点：首屏核心 = 一个大数字 + 一条曲线、大数字约正文 3~6 倍、
> 数字必须带参照（变化额 + 变化率 + 时间窗）、导航只留图标、首屏零解释性文字
> —— 依据是 Wealthfolio / Ghostfolio / Rotki 三个独立产品的实测交集（计划书 §13.3.2）。
> ⚠️ 三者都是欧美配色（涨绿跌红），**本项目必须涨红跌绿 + ▲▼**，此条不照搬。

---

### 交接：2026-10-05（DSH 会话五 → 下一个智能体）—— ⭐ **B-0 ~ B-5 已全部完成，剩 B-6（删旧前端）**

> **状态来源（本轮实测）**：工作树干净（本块提交后）、已推送、
> 门禁 **845 passed / 2 skipped**（当时值）、前端真机 **45/45**（在 **`/`** 上跑）、
> 数据契约 **24 项 0 失败**、前端 `tsc --noEmit` 0、`vite build` 成功。
> ⭐ **新前端已挂在 `/`**（B-5 完成）；`/v2` → 301 到 `/`；旧仪表盘已归档。
> ⚠️ **账本新增一张表** `ledger_commits`（撤销日志，B-4b-1）；动它之前已快照
> `data/_snapshot_20261005_173940_pre_write.db`。
>
> ⚠️ **跑测试/Playwright 一律用系统 Python**：
> `C:/Users/m1309/AppData/Local/Programs/Python/Python313/python.exe`（托管那个没有 pytest）。

#### 一、本轮完成：B-0 ~ B-5（视觉判据 / API 客户端 / 写门面 / 3 个展示块 / 写操作上界面 / 切换）

**B-0**：`scripts/verify_frontend.py` 追加 7 条静态判据，抓出并修掉 5 处真实违规。

**B-1**：`client.ts` 加 `apiPost`/`apiEnvelope` + `ApiResult.envelope`；补 5 个纯读端点
（`sentiment` 三态专用）；`types.ts` 补类型。5 端点与真实响应核对 0 处不符。

**B-2**（⚠️ 唯一碰账本，已快照保护）：快照工具 `snapshot_before_write.py`；
写门面 `ledger_write.py`；修两个口径缺陷（update 同步 transactions / delete 备份两张表 + 连带删流水）；
`Database.__init__` 路径守卫 + `tests/conftest.py` 放行测试建临时库。

**B-3**（§12 B-3，详见该节「落地记录」）：
`LiveQuoteCard`（行情，`available=false` 是降级）、`SentimentCard`（三态自管轮询 + 旧值标注）、
`BacktestRec`（折叠 + 引用信封层 `purpose`/`methodology_note`）；
`useApi` 加 `envelope` 字段；`verify_frontend.py` 加 3 条断言（第 41~43 项）。
⚠️ 首轮真机验收**抓到 375 横向溢出 2px**（grid 子项缺 `min-w-0`）→ 已修并转绿。

**B-4a**（§12 B-4，提交 `9605181`，**只铺通道、未动界面**）：
修 `apiPost` 契约缺陷 —— 后端写端点**返回形状不统一**（`/api/holdings` 成功时只有 `message`、
`/api/reconcile` 给 `data`），原设计会把"记买入成功"判成"空数据"而报错；
改为返回整个信封 + `ApiEnvelope` 补 `message`。
补 6 个写端点封装：`holdings` / `reconcile` / `holdingsRefresh` / `navUpdate` / `planAction` / `dcaAction`。

#### 二、下一步：按 DESIGN §5 瘦身（用户 2026-10-05 反馈后定的新主线）

> ⚠️ **本块已完全被顶部块取代**：B-5 之后的 B-6 与 §13.6 六条处方**都已做完**，
> 本块的"下一步 / 待办"**全部过时**，只作为"当时怎么定的"的线索保留。

> **用户反馈原话**：「改完前端后，整体交互逻辑和信息面对于人类来说有点变态了，
> 能不能学习一下其他人的再来做优化？」

**已做（本轮，只写规范 + 判据，未动页面）**：

- **量了再说**：4 页 × 2 尺寸实测密度 → 今天 80 / 研究 107 / 设置 128 个**含数字节点**；
  设置页 4.6 屏（移动）；全站 14 档字号（单页 8~10 档，六档挤在"正文"区间）。
- **对标 Ghostfolio**（AGPL）：它总览页**只有 3 块**（总市值 / 净值曲线 / 资产配置），
  持仓、交易、行情、账户、分析**各有独立页**；「扫一眼就知道当天要不要做调整」。
- ⭐ **对标 Wealthfolio（真实界面，已补）**：用户开 Clash（7897）后下载了它的官方展示图 →
  `docs/prototype/refs/wealthfolio-overview.jpg`。其 **Dashboard 首屏只有 3 块**：
  ① 大数字（字号约正文 3~4 倍）② **无网格/无刻度/无边框**的面积曲线 ③ 两块小列表（无表头）。
  移动首屏 ≈ 1 个大数字 + 1 条曲线 + 3 行列表（≈15 个数字节点）。详见计划书 §13.3.1。
  ⚠️ 它的配色是欧美惯例（涨绿跌红）——**本项目必须涨红跌绿，此条不照搬**。
- **规范**：`DESIGN.md` **§5 重写为可断言**（首屏规则 / 密度上限 / 折叠规范 / 页面职责表 / 数字带参照）。
- **判据**：`verify_frontend.py` 新增 **8 条密度断言**，用**基线上限**起步
  （现状超标 → 先钉"不许更差" + 打印"距目标还差多少"；每瘦身一轮往下调基线）。
  ⚠️ 基线必须是**实测值** —— 首次写时估算成 1.5/1.6，实测 1.52/1.65，被自己的断言当场抓出。
- 详情见 `docs/前端重构计划书.md` **§13**（含六条瘦身处方）。

**⬜ 下一步（按计划书 §13.6 执行）**：

1. 「今天」页瘦成 3 块：**结论 + 3 个数字 + 曲线**；结论落到 `要动手/不用动手/待数据`
2. 数据链路（审计）整体搬到「设置」，默认折叠
3. 持仓表从「今天」移走（「持仓」页本就有）
4. 行情 + 消息面压成一行小条（不再各占一张卡）
5. 筛选池一行 8 列 → 4 列（代码 / 名称 / 夏普 / 动量）
6. 字号 14 档 → 6~7 档（明确 4 个层级）

预期：「今天」页 2.27 屏 → 约 1 屏；数字节点 80 → ~20。**改完记得把 `DENSITY_BASELINE` 往下调。**

⚠️ **B-6（删旧前端）仍待办**，但优先级低于上面的瘦身（旧文件留着不影响使用）。


**B-4a ✅**（`9605181`，只铺通道）：修了 `apiPost` 契约缺陷（后端写端点**返回形状不统一**，
`/api/holdings` 成功时只有 `message` 没有 `data`，原设计会把它判成"空数据"而报错）；
补 6 个写端点封装（`holdings`/`reconcile`/`holdingsRefresh`/`navUpdate`/`planAction`/`dcaAction`）。

**B-4b-1 ✅**（后端撤销机制，本轮提交）：新表 `ledger_commits` + `ledger_write` 的
`snap`/`record`/`rollback`/`recent_commits`（`COMMIT_RETENTION = 20`）+ 端点
`POST /api/holdings/rollback`、`GET /api/holdings/commits` + `tests/test_ledger_rollback.py`（8 例）。
撤销 = 按 `spec` 删当前行、插回写前快照 → 新增/修改/删除**三种语义都能还原**。
⚠️ 定位一律用主键/外键（`id`/`holding_id`），**不用 `fund_code`**（同基金多批次会被卷进来）。
⚠️ 覆盖范围只含**用户主动的写操作**；`reconcile` 的到期结算与分红自动落账**不记提交**（幂等、可重跑）。

**B-4b-2 ✅**（本轮提交，确认流 + 持仓页）：
新增 `ConfirmDialog`（写操作确认卡，**不用原生 confirm**）+ `LedgerActions`（记买入/记卖出/
改金额日期/分红方式/删除持仓，**全部先出确认卡**，成功后带「撤销」）；
`/api/holdings` 各 action 响应补 `commit_id` + `ledger_write.latest_commit_id()`；
「持仓」页接入，「本页只读」注释已删。**端到端测试 4 例**（走 Flask test client：
买入→拿 commit_id→rollback→持仓与流水一起回滚；重复撤销被拒）。

**B-4b-3 ✅**（本轮提交，设置页）：
新增 `components/OpsActions.tsx` —— 四类写入全部先出确认卡：**更新净值 / 对账（高危，明示待确认 N 笔 + ¥X）**
/ **定投**（同步·补录·执行到期 都明示"将记 N 笔真实买入"；暂停/恢复/删除逐条）/ **投资计划**（改信息 / 删计划）。
预估值**从已有数据推算**（`holdings` 的 `pending_confirm`、`plans` 的 `due`），不新增端点。
⚠️ 本页操作**不记提交**，确认卡统一写「不可撤销」——只有「持仓」页那 5 个 action 可撤销。

**B-5 ✅**（本轮提交，切换 —— 新前端挂 `/`）：
`/` 改为托管 `FRONTEND_DIST`（未构建时 **503 + 明确提示**）；**新增 `/assets/<path>`**
（⚠️ 必需：`base='/'` 后 `index.html` 引用的是 `/assets/...`，少了它页面白屏）；
`/v2`、`/v2/`、`/v2/<path>` → **301** 到 `/` 或 `/#/<path>`（深链保留）；
`dashboard.html` `git mv` 到 `src/web/_archive/`；vite `base` → `/` **并重建 dist**；
`Sidebar` 删「旧仪表盘 ↗」；`verify_frontend.py` 从 `/v2` 改指向 `/`（8 处）。
验收：`curl /` → 200（新前端）｜`/v2` → 301 → `/`｜`/v2/research` → 301 → `/#/research`｜
`/assets/*` → 200；真机 **45/45**（在 `/` 上跑）；契约 24 项 0 失败；pytest 845/2。
⚠️ 踩到：切换后**旧服务仍占着 5020**，新服务静默失败 → 表现为"改了没生效"。
定位：`netstat -ano | grep :5020` 拿 PID；`kill` 要用 **bash 的 PID**（不是 Windows PID），
或 `taskkill //F //PID <winpid>`。

**双界面手工走查 ✅ 用户已确认通过**（记一笔 → 撤销 → 与旧界面对齐，均正常）。

**⬜ 唯一剩余：B-6（删除旧前端）** —— 计划书要求"等 B-5 稳定（自己实际用几天，不设形式化天数）"。

1. 删 `src/web/static/app.js`（1395 行）/ `app.css`（311 行）/ `src/web/_archive/`
2. 删 `scripts/check_rise_fall.py` —— ⚠️ **先确认无其他引用**；⚠️ 注意它现在在
   §11.3 里被记作"色彩判据"，删掉后**新前端的涨跌色就没有脚本守卫了**
   （现在靠 `lib/format.ts` 的 `dirClass` 保证）→ B-6 应补一条查**新前端**的色彩/涨跌色断言
3. `tests/test_doc_refs.py` 清断链引用
   （⚠️ 已登记的历史例外 `src/web/templates/dashboard.html` **要保留** ——
   `docs/前端展示优化任务书.md` 是历史文档，仍会引用它）
4. ⚠️ **删之前先确认 B-5 没被回滚过**；删了就真没了（除非 git 历史）

⚠️ **B-4 手工走查通过之前不进 B-5**（B-5 之后旧界面就没了，是最后一道对照）。

#### 三、注意事项（本轮新增的坑，别重复踩）

1. ⚠️⚠️ **`urllib` 会读 `HTTP_PROXY` 环境变量 → 探针假绿**（本轮最险的坑）。
   本机沙箱导出 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:<port>`（WorkBuddy 自己的代理），
   于是**连 `localhost:5020` 也被转发、返回 502** → `probe_server` 判"服务不可达" →
   **动态 33 项被静默 `[SKIP]`，汇总只显示 `7/7 通过` 且退出码 0**。
   已修：探针显式用 `urllib.request.build_opener(urllib.request.ProxyHandler({}))`。
   **自查**：完整跑必须是 **40/40**；若看到 7/7 + `[SKIP]` 行，先查服务与代理，别当成通过。
   （Playwright 不走这套变量，所以只有探针中招；`curl` 在沙箱里也要加 `--noproxy '*'`。）

2. ⚠️ **起服务别用 `(python src/main.py web &)`**：子 shell 分离的进程会在**该次工具调用结束时被清理**
   （实测 curl 返回 200 后约 30 秒即消失）→ 紧随其后的 verify 又变"不可达"。
   要用**受管的常驻后台**方式启动。

3. ⚠️ **跑 pytest 要调高清理钩子阈值 + 先清 basetemp**（2026-10-05 踩两轮）：
   ```bash
   # ① 必须先清空 basetemp（Python 删，别用 bash rm -rf）—— 残留会让 pytest 报
   #    `OSError: [Errno 53] 找不到网络路径`，表现为一堆 ERROR（非 failed）、耗时翻倍
   python -c "import shutil; shutil.rmtree(r'D:\DSH\scratch_hold\_pytmp', ignore_errors=True)"
   # ② 再跑（调高阈值，否则收尾删 tmp 被拦成 SAFE_DELETE_BULK_CONFIRM_REQUIRED → 卡 100% 不退）
   CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=100000 python -m pytest tests/ -q --tb=line \
     -p no:cacheprovider --basetemp=D:/DSH/scratch_hold/_pytmp
   ```
   预期 `820 passed, 2 skipped`。basetemp **别放 `%TEMP%`**（拦截区，更慢）、**别放仓库内**（污染 git status）。

4. ⚠️ **判据与观感冲突时先改判据**（计划书 §11.4）。B-0 已按此改过 4 处，
   全部记在 **§11.4.1**（含"4 的倍数 → 2px 网格"的裁决理由）。**未决点**：
   若严格要求「间距是 4 的倍数」，需改动约 20 个文件 —— 已按"2px 网格"落地，如需回退请先说。

5. ⚠️ **`--static-only` 不需要服务**：改前端样式后自查最快路径是
   `python scripts/verify_frontend.py --static-only`（秒级，退出码 0/1）。

#### 四、关键文件

| 文件 | 本轮变化 |
|---|---|
| `scripts/verify_frontend.py` | 追加 7 条静态断言 + `--static-only` + `[SKIP]` 降级 + 探针绕代理 |
| `frontend/src/api/client.ts` | 新增 `apiPost` / `apiEnvelope`；`ApiEnvelope.status` 放宽；`ApiResult.envelope` |
| `frontend/src/api/endpoints.ts` | 补 5 个纯读端点（`sentiment` 三态专用） |
| `frontend/src/api/types.ts` | 补 `MarketLive` / `Sentiment*` / `Recommend*` / `AllDashboard` |
| `docs/前端重构计划书.md` | §10.3 差集进度、§10.4 施工表、§11.3/§11.4、§12.0 基线更正、§12 B-0/B-1 标完成 |
| `frontend/src/components/SourceTag.tsx` 等 4 个 tsx | B-0 判据抓出的 5 处真实违规修复 |
| `scripts/snapshot_before_write.py` | 整库快照工具（backup API，含 WAL 合并，打印回滚命令） |
| `src/analysis/ledger_write.py` | 写门面：`set_dividend_policy` / `update_holding`（同步 tx）/ `delete_holding`（备份两张表） |
| `src/data/database.py` | `__init__` 路径守卫 `allow_create`（默认读 `QFA_DB_ALLOW_CREATE`） |
| `src/web/app.py` / `src/cli/output_cmds.py` | update/delete/dividend_policy 改走门面 |
| `tests/conftest.py` | 设 `QFA_DB_ALLOW_CREATE=1` 放行测试建临时库 |
| `tests/test_ledger_write.py` | 路径守卫 + 口径①② + 孤儿流水（13 个用例） |
| `frontend/src/components/BacktestRec.tsx` | B-3：历史回测折叠块（引用信封层 `purpose`） |
| `frontend/src/components/MarketStrip.tsx` | B-3 的行情块 + 消息面块 —— 2026-10-06 瘦身第 5 条把原先那两个组件合并成本文件（消息面三态轮询抽到 `frontend/src/lib/useSentiment.ts`） |
| `frontend/src/lib/useApi.ts` | B-3：加 `envelope` 字段（带出信封层文案） |
| `frontend/src/routes/Today.tsx` / `Research.tsx` | B-3：接入三个块（子项给 `min-w-0`） |

**回滚**：`git revert <本块提交>`。⚠️ B-2 是唯一碰账本的一步 —— 若需回滚**账本数据**，
用快照 `data/_snapshot_20261005_131223_pre_write.db` 还原（先停服务）：`cp <快照> data/fund_quant.db`。

---

### 交接：2026-10-04（DSH 会话四收官 → 下一个智能体）—— ⭐ **前端改为方案 B：新前端替换旧前端**

> **状态来源（本轮实测）**：工作树干净（本块提交后）、已推送、门禁 **822 passed / 2 skipped**、
> 数据契约 **24 项 0 失败**、前端真机 **33/33**（历史值）、**无服务在跑**。
> ⚠️ **测试基线从 820 更正为 822**（多出的是 `test_counterfactual_same_fund.py` +
> `test_drawdown_risk_jump.py` 等新文件，属正常增长）。
> ⚠️ **跑测试/Playwright 一律用系统 Python**：
> `C:/Users/m1309/AppData/Local/Programs/Python/Python313/python.exe` ——
> **托管 Python 里没有 pytest**（实测 `No module named pytest`）。

#### 一、本轮最重大变化：前端方向反转（用户拍板，我先前的做法被否）

**用户指正原话**：「我记得当初是为了重新做一版美化，现在出来两个前端，这确实不符合我的原意。
原前端 UI 有点过时所以要更新升级到一版新的，但是你直接做出来一个新前端，只通过路由链接，
实际上只是一个展示大屏，不是工具。」

**用户决策**：「**B，这样子我们就不用在旧前端上面搭房子了，而是直接在新前端引擎的地基上建新房。**」

即：**新前端挂 `/`，旧前端退役**。原「两套并存 + 对照跑两周」方案**作废**。
实测证据（"展示大屏不是工具"成立）：新前端 `<input>` **0 个**、`<select>` 0 个、
`<button>` 9 个（5 个是导航）、**POST 0 处**（`client.ts` 只有 `apiGet`）；
对照旧前端：9 / 1 / 21 / **18 处**。

**完整方案已写入 `docs/前端重构计划书.md`**：
- **§10** 决策变更 + 为什么改 + 真实工作量（双向差集）+ 施工顺序 + 风险
- **§11** 视觉验收判据（把「Awwwards 级品质」改成 7 个可断言项）
- **§12** ⭐ **B-0~B-6 逐步施工详规**（每步：输入/产出文件/验收命令/已知坑/回滚方式）

#### 二、下一步（**按 §12 顺序，不跳步**）

| 步 | 内容 | 备注 |
|---|---|---|
| **B-0** | 视觉判据：在 `scripts/verify_frontend.py` **追加** 排版/留白/层级 三组断言 | ⬜ 待做，**建议先做**（上次错在"先动手后补标准"） |
| **B-1** | `client.ts` 加 `apiPost`；补 9 个端点封装（**纯读批先做**） | ⬜ 待做 |
| **B-2** | 写操作后端门面 + 事务边界 + `Database` 路径守卫 + 全库快照工具 | ⬜ ⚠️ **唯一碰账本** |
| **B-3** | 补 3 个独占展示块（行情卡 / 情绪 / 回测折叠） | ⬜ 待做 |
| **B-4** | 写操作接进新前端（确认流 + 撤销） | ⬜ ⚠️ **唯一不可回退** |
| **B-5** | 切换：新前端挂 `/`，旧版归档，`/v2` 301 → `/` | ⬜ |
| **B-6** | 删除旧 `app.js`/`app.css`/`dashboard.html` | ⬜ B-5 稳定后 |

#### 三、注意事项（**本轮的坑，别重复踩**）

1. ⚠️⚠️ **B-5 之前，旧前端一行都不删** —— 它是**唯一的写入口**，也是唯一的回滚路径。
2. ⚠️ **"对照跑 ≥2 周"判据已作废** —— 那是为"并存"设计的。别再拿它当"门槛未到"的理由。
3. ⚠️ **双向差集，不是单向补齐**：新前端缺 9 个端点（`/api/all`、`/api/holdings`、
   `/api/holdings/refresh`、`/api/market/live`、`/api/nav/update`、`/api/portfolio/curve`、
   `/api/recommend`、`/api/reconcile`、`/api/sentiment`）；但已封装的 `/api/rebalance`、
   `/api/explain`、`/api/funds`、`/api/funds/board` 是旧前端**没有**的新能力。
4. ⚠️ **`/api/sentiment` 是三态**（`ok` / `scanning` 要轮询 / `ok:false` 3 分钟不重扫），
   **别复用 warming 分支**；`/api/market/live` 出网失败返回 `ok=true`+`available=false`
   （**不是错误**）；`/api/recommend` 15~20s 且**语义是"历史回测验证"不是"推荐"**（§4.4）。
5. ⚠️ **`dividend_policy` 是裸 SQL**（`executemany` + `commit()`），**绕开了 `_WRITE_LOCK` 和事务**
   —— B-2 必须收进门面。这是现存并发风险最高处。
6. ⚠️ **两个只读盘查新发现的口径缺陷**（不在旧清单，B-2 一并修）：
   ① `update` 改 `buy_amount` 重算 `shares` 但**不同步 `transactions`**（复式记账对不上）；
   ② `delete_holding` 只备份 `holdings` 单行，**不备份对应 `transactions` 行**。
7. ⚠️ **`doc_refs` 守卫会抓"计划新建但还不存在"的文件路径** —— 本轮我写
   `src/analysis/ledger_write.py` 被它抓红。**计划产出写成「文件待建：xxx」**，别裸写路径。
8. 其余工程坑见 §12.1 一页备忘（Python 解释器 / precompute 前置 / `?fresh=1` /
   视口截图 / `.rise-in` 的 `backwards` / 沙箱限制）。

#### 四、关键文件

- **本轮唯一改动**：`docs/前端重构计划书.md`（+~450 行，§10/§11/§12）、`AGENTS.md`（前端条目 + 文档索引）
- **下一步要碰**：`scripts/verify_frontend.py`（B-0）、`frontend/src/api/*`（B-1）、
  `src/analysis/portfolio.py` + `src/data/database.py`（B-2）、`frontend/src/routes/Position.tsx` +
  `Settings.tsx`（B-4）
- **本步之后另需引用**：`docs/前端重构计划书.md` §12

---
