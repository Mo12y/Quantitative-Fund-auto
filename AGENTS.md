# AGENTS.md — 多智能体协作入口

> **接手的智能体：开工最小读取 = §二（命令）+ §三（铁律）+ `docs/agents/HANDOFF.md` 顶部交接块。**
>
> ⚠️ **为什么需要这份文件**（2026-10-01 新增）：本项目的"项目记忆"原本只写在 `.workbuddy/memory/`，
> 而那个目录被 `.gitignore:75` 忽略 —— **没有进 git**。后果：
> ① 别人 clone 下来完全看不到项目怎么运转；② 换机器/清目录即永久丢失；③ 每次换智能体都要重新摸一遍。
>
> 参照物：作者的毕业设计（`D:\大学\new`）把同样的内容放在**仓库内**（`AGENTS.md` + `docs/agents/` +
> `docs/00-规范/`），所以任何一个新智能体 5 分钟就能开工。本项目补齐这一层。
>
> 分工：**操作记忆**（怎么干活）= 本文件；**项目记忆**（项目是什么）= `README.md` / `DESIGN.md` /
> `docs/` 各权威文档。`.workbuddy/memory/` 降级为**个人速记**，不再作为唯一记录。
>
> 引用规则时**按名称引用**（如「铁律·写库先快照」），不要用编号 —— 编号会随合并变动。

---

## 一、这是什么项目

**《基于多源金融数据的个人基金投资决策支持系统》**（2026-10-15 计划书 §2.1 定题；原「量化基金系统」）。
定位：**决策支持，不是决策替代**——系统用多源金融数据算 行为镜 + 四信号 + 反事实归因，用户自己下判断。
单人自用、真实资金；论文按「系统 vs 实例」诚实声明（当前 pilot=1，**非**多用户部署）。

- **题目 / 换锚理由 / 全链路**：`docs/05-重构/00_重构蓝图.md`（蓝图定方向）
  ＋ `docs/05-重构/01_决策支持系统_执行计划书.md`（**顶层执行计划**：5 阶段 / API 契约 / 删除清单 / 跨依赖）
- **数据规模 / 账本 / 门禁（唯一权威）**：`docs/00-规范/结论权威口径.md`
- ⚠️ **旧命题「温度择时 / 选基排序 / 预测收益」已废止**（回测 alpha 不显著，p=0.38/0.56）；
  其模块**归档冻结**在 `legacy/`（`git mv`，**不是删除**）。
- 账本：`data/fund_quant.db`；前端：React（`frontend/`）由 Flask 托管挂 `/`。
- 技术栈：Python + SQLite + Flask + React/Vite。

## 二、操作记忆：环境与命令

### 2.1 环境

| 项 | 值 |
|:---|:---|
| 仓库 | `D:\DSH\projects\Quantitative-Fund-auto`（Windows，本机即权威） |
| 远端 | `git@github.com:Mo12y/Quantitative-Fund-auto.git` |
| Python | `C:/Users/m1309/AppData/Local/Programs/Python/Python313/python.exe` |
| Node（前端构建） | 托管 node；`cd frontend && npm run build` |

> ⚠️ **HTTPS 连 github.com 不通**（代理链 502），**推送必须走 SSH 显式 URL**：
> `git push git@github.com:Mo12y/Quantitative-Fund-auto.git main`
> ⚠️ 沙箱无法持久化 `refs/remotes/` → `git status` 恒显示 `[gone]`。
> **判断是否同步以 `git ls-remote --heads origin` 为准**，不要信 `[gone]`。

### 2.2 常用命令

| 动作 | 命令 |
|:---|:---|
| 跑测试（基线 **534 passed**，2026-10-16 实测） | `python -m pytest tests/ -q`<br>⚠️ **沙箱里必须换成**：`CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=100000 python -m pytest tests/ -q --basetemp=<全新目录>` —— **每次换全新 basetemp、串行跑**。复用目录会让 safe-delete 的 trash 报 `SAFE_DELETE_FAIL_CLOSED`（`OSError [Errno 53/64]`）→ 变成一堆 **ERROR（不是 failed）**；阈值不够则卡在 100% 不退、汇总被吞。 |
| 起 Web | `python src/main.py web`（默认 :5020） |
| 前端构建 | `cd frontend && npm run build`（沙箱里 npm 不稳 → `D:/nodejs/node.exe node_modules/vite/bin/vite.js build`） |
| **前端真机验收**（**61 项**，需先起服务；首次可先 curl 一轮暖机） | `python scripts/verify_frontend.py` |
| **数据契约检查**（24 项，只读） | `python scripts/check_ledger_invariants.py` |
| 同类结构体检（只读） | `python scripts/analyze_peer_structure.py` |
| 回填指数估值分位（**写库，先快照**） | `python scripts/backfill_index_percentiles.py [--dry-run]` |
| 重建温度历史序列（**写库，先快照**） | `python scripts/build_temperature_history.py` |
| 构建类对相关基线（**只读账本**；corr_overlap 相对模式的门限底数，缺失→该约束声明未评估） | `python scripts/build_overlap_baselines.py` |
| 回填流水 shares / sell_amount（**写库，先快照**；幂等，只补缺失不覆盖） | `python scripts/backfill_transaction_shares.py [--apply]` |
| 预计算快照（让 Web 首屏免冷算） | `python src/main.py precompute` |

其它 CLI（`python src/main.py <cmd>`）：`collect` `index` `nav` `snapshot` `calendar` `temp` `score`
`sector` `sentiment` `recommend` `strategy` `portfolio` `buy` `sell` `rebalance` `dca` `plan` `report` `oos`
`counterfactual` `sell_rules` `breakeven` `behavior` `drift`。

## 三、操作记忆：铁律

1. **【铁律·写库先快照】**：任何写 `data/fund_quant.db` 的操作（对账 / 补录 / 迁移）**之前**，
   先整库快照 `data/_snapshot_<时间戳>.db` 并记录回滚命令。**没有快照不动账本。**
2. **【铁律·绝对路径】**：脚本一律用 `os.path.join(项目根, "data", "fund_quant.db")`。
   `Database("data/fund_quant.db")` 是**相对路径**，在项目根之外跑会**静默新建空库**
   （表现为"持仓=0、最新净值=None"，极易误判成数据丢失）。
3. **【铁律·先复现再修改】**：结论与文档不符时**停下来报告**，不要硬改。
   口径不同 ≠ bug —— 本项目反复栽在"把口径当 bug 修"和"把 bug 当口径放过"两头。
4. **【铁律·测试全绿】**：现有 **534** 个测试必须继续全绿；每项修复配新测试。
5. **【铁律·不新增运行时依赖】**：确需新增先停下来问。（开发工具如 Playwright 不算，不进 `requirements.txt`。）
6. **【铁律·不顺手扩大范围】**：做完所列事项即停；发现的新问题**记下来**，不顺手改。
7. **【铁律·付费服务先设闸】**：任何计费外部 API（同花顺等）先报告预估用量与金额、配硬上限、
   监控"累计花费"而非"进度"。未核实计费的服务**不接入**。
8. **【铁律·大操作即记录】**：改代码 / 采集 / 账本变更 / 里程碑完成后，**立即**更新
   `docs/agents/STATUS.md`（顶部"现在"）+ `docs/agents/HANDOFF.md`（换人时）并 commit。
   **记录是默认动作，不是可选动作。**

## 四、操作记忆：表达纪律

1. **陈述分级**：只对**进入结论**的陈述标一次级 ——
   `【实测】`本轮跑过（附命令/`文件:行号`）｜`【口径】`来自项目权威文档（**不等于本轮验证过**）｜
   `【文献】`附可查来源｜`【推测】`写明依据 + 什么证据能推翻它。
2. **不迎合**：与用户判断冲突时**先摆证据再给建议**；确信度低就直说没把握，
   **不许为显得独立而制造分歧**，也不许明知有错却顺着说。
3. **机械验收**：任务的"验收"必须写**命令 + 预期输出**（如 `pytest -q 得 534 passed`），
   **不写 prose**（如"数字一致"）。
4. **提醒预算**：只在"会改变结论是否成立"或"成本量级变化"时提醒，每轮最多 3 条、
   单独成节、不混进主回答；**没有达标项就一条都不写**。
5. **不许用"应该没问题"结案**：做完必须给出可检验的完成证据；工具反复失败时**先怀疑自己的用法**。

## 五、机械机制（**把约定变成红灯，而不是靠自觉**）

| 守卫 | 命令 | 挡住什么 |
|:---|:---|:---|
| 功能回归 | `python -m pytest tests/ -q` | 534 项功能断言（基线 **534 passed**，T2.2 后）|
| 数据契约（账本 + 市场数据） | `python scripts/check_ledger_invariants.py` | 值域 / 引用完整性 / 时序 / 覆盖率 / 新鲜度 / **复式记账恒等式**（24 项 = 账本 13 + 市场数据 11）|
| 前端真机 | `python scripts/verify_frontend.py` | 三入口（决策台/账本/数据状态）渲染、七卡、导航断点、移动端零溢出、动效绑定、深链与旧 hash 回落、**信息密度基线（§5.2 五行全接 · 三页目标）**、**字号 token**、**涨跌色**（**61 项** = 动态 50 + 静态 11）<br>⚠️ 需先起服务；首次可先 curl 一轮暖机（`/api/explain` 冷算约 37s） |
| **文档引用完整性** | `pytest tests/test_doc_refs.py -q` | 文档指向**仓库内不存在**的路径 |
| **开工必读链预算** | `pytest tests/test_boot_budget.py -q` | 必读文件超行数/字符预算（AGENTS ≤200 / STATUS ≤160 / HANDOFF ≤160 / TASKS ≤200）|
| 同类结构体检 | `python scripts/analyze_peer_structure.py` | 同类相关基线（决定 `corr_overlap` 阈值是否还成立）|

> **为什么单独列出来**：本项目有 53 个测试文件、8,400 行，但 2026-10-01 之前**一条治理守卫都没有** ——
> 全是功能测试。于是"文档要更新""数字要对齐"只能靠自觉，而实测结果是**文档状态长期滞后**
> （2026-09-27 一次核查：5 处"未做"里 4 处其实已做）。
> 参考毕设补上第一条 `test_doc_refs.py` 后，它**当场抓到 2 处真断链**（一处是 `frontend/src/index.css` 漏写了 `frontend/` 前缀；
> 一处承诺的脚本早已被替代却仍写着"待实现"）。
> **凡是"以后要记得…"的约定，都该问一句：能不能写成测试。**
>
> ⚠️ **2026-10-06 又抓到一次**：这条守卫自己有个洞 —— `SKIP_MARKERS` 里的 `'*'`
> （本意是跳过通配符路径）把**任何含 `**加粗**` 的行整行**都跳过了。
> 负数对照：往一个入库的 `.md` 追加一行「- **加粗的坑**：见 `<不存在的路径>`」→ 守卫**照样通过**。
> 去掉那个 `'*'` 后当场暴出 **6 处长期被吞的真断链**（2 处是漏写 `frontend/` 前缀的旧账）。
> **教训：行级跳过标记越宽，判据越像装饰。**

## 六、开工最小读取（**分三层，别越层读**）

| 层 | 何时读 | 读什么 | 预算 |
|:--|:--|:--|:--|
| **L0** | 每次会话自动加载 | 本文件 `AGENTS.md` 全文 | ≤200 行 |
| **L1** | 一开工就读 | `docs/agents/STATUS.md` 全文（只装"现在"）+ `docs/agents/HANDOFF.md` **最上面一个交接块** | 各 ≤160 行 |
| **L2** | ⛔ 不要通读，用到再跳 | `docs/agents/TASKS.md`（只跳你手上那条）、`docs/00-规范/结论权威口径.md`、`docs/05-重构/00_重构蓝图.md` | 见 §七 |

> **只能读一个文件的话：`docs/agents/STATUS.md`**（顶部即"现在"）。
> **预算由守卫钉住**：`pytest tests/test_boot_budget.py -q` —— 这几个文件再长回去就红。

## 七、项目记忆：去哪查（不重新推导）

| 要什么 | 去哪 |
|:---|:---|
| **核心数字（唯一权威）** | `docs/00-规范/结论权威口径.md` |
| **改数字前查引用** | `docs/00-规范/口径引用索引.md` |
| **题目 / 全链路 / 保留-冻结边界 / ML 重定向** | `docs/05-重构/00_重构蓝图.md` |
| 现在到哪了 | `docs/agents/STATUS.md` **全文**（顶部即"现在"）+ `docs/agents/HANDOFF.md` 顶部交接块 |
| 做什么 / 验收 | `docs/agents/TASKS.md` —— ⚠️ 只跳到你手上那一条 |
| 文档总索引 | `docs/README.md` |
| 过期/历史文档（可 grep，**不作口径**） | `docs/_archive/` |
| 设计约束（token / 前端冻结项） | `DESIGN.md` |
| 项目是什么 / 模块地图 | `README.md` |
| 个人速记（**不在 git 里，仅本机**） | `.workbuddy/memory/` |

> ⚠️ **引用旧 `docs/*.md` 里"未做/未落地"的结论前，先 grep 代码核实** ——
> 本项目文档状态标注**长期滞后**（2026-09-27 一次核查：5 处"未做"里 4 处已做）。
> ⚠️ 重构前的旧文档（前端重构计划书 / 审计修复记录 / 基金推荐系统设计方案 …）**尚未归并**，
> S1 完成后按「权威 → `00-规范/`、过期 → `_archive/`」二分归位。
