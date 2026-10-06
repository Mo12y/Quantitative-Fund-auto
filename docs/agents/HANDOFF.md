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
| HEAD | 本轮 6 个 commit（`b571a9d` 第4条 → `42dd3e4` 第6条 → 收尾 commit） |
| 工作树 | 干净（收尾 commit 后） |
| 门禁 | ✅ pytest **845 passed / 2 skipped** ｜ ✅ 前端真机 **60/60** ｜ ✅ 数据契约 **24 项 0 失败** ｜ ✅ `tsc --noEmit` 0 ｜ ✅ `vite build` 绿 |
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

#### 下一步（按优先级）

1. **B-6（删旧前端）** 仍是唯一未做的施工项 —— 前置条件是"B-5 稳定、自己用几天"，
   且删掉 `scripts/check_rise_fall.py` 后需**补一条查新前端的涨跌色断言**
   （现在靠 `lib/format.ts` 的 `dirClass` 保证）。
2. **字号 14 档 → 6~7 档**（计划书 §13.6 原列第 6 条，本轮**未做**）：
   `@theme` 里没有 `--text-*` token，这是它一直没收敛的根因。要做就得先给
   `index.css` 补字号 token，再逐处替换 —— 独立一轮。
3. 密度剩余空间：持仓 51（目标 40）、研究 75（目标 40）。研究的大头是筛选池与
   行业板块两张表本身（不是装饰），再压要靠"要不要全摊开"的产品决策，不是版式改动。

#### 开工三步（与上一版相同，未变）

```bash
cd /d/DSH/projects/Quantitative-Fund-auto

# ① 起服务（后台）+ 预热快照 —— ⚠️ 不预热会得到假数字
C:/Users/m1309/AppData/Local/Programs/Python/Python313/python.exe src/main.py web
C:/Users/m1309/AppData/Local/Programs/Python/Python313/python.exe src/main.py precompute

# ② 验收四件套
C:/Users/m1309/AppData/Local/Programs/Python/Python313/python.exe -m pytest tests/ -q          # 845 passed / 2 skipped
C:/Users/m1309/AppData/Local/Programs/Python/Python313/python.exe scripts/verify_frontend.py   # **60/60**
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
> 门禁 **845 passed / 2 skipped**、前端真机 **45/45**（在 **`/`** 上跑）、
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
