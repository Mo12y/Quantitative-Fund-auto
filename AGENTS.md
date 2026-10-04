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

个人量化基金投资助手（**单人自用、真实资金**）。Python + SQLite + Flask，
数据层 `collector.py`/`database.py` → 分析层约 20 个模块 → 输出层 CLI 周报 + Web 仪表盘。

- 数据规模：`fund_info` 约 2.8 万只；`fund_nav` 约 2,290 万行 / 2.4 万只有净值
- 账本：`data/fund_quant.db`（`holdings` / `transactions` / `fund_nav` / `fund_info` / `trade_calendar` …）
- 前端：⚠️ **过渡期 —— 两套并存，但已定案合并**。
  **目标态（方案 B，2026-10-04 用户拍板）**：新前端 React（`frontend/`）挂 **`/`**，
  旧仪表盘退役。施工详规见 `docs/前端重构计划书.md` **§10 + §12（B-0~B-6）**。
  **当前态**：新前端仍在 `/v2`（`frontend/dist` 由 Flask 托管），
  旧仪表盘在 `/`（`src/web/static/app.js`，**仍是唯一的写入口**）。
  ⚠️ **B-5（切换）之前旧前端一行都不删** —— 它是唯一写入口 + 唯一回滚路径。

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
| 跑测试（基线 **806 passed / 2 skipped**，2026-10-03 实测） | `python -m pytest tests/ -q` |
| 起 Web | `python src/main.py web`（默认 :5020） |
| 前端构建 | `cd frontend && npm run build` |
| **前端真机验收**（33 项，需先起服务**并先 `precompute` 预热快照**） | `python scripts/verify_frontend.py` |
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
4. **【铁律·测试全绿】**：现有 **806** 个测试必须继续全绿；每项修复配新测试。
5. **【铁律·不新增运行时依赖】**：确需新增先停下来问。（开发工具如 Playwright 不算，不进 `requirements.txt`。）
6. **【铁律·不顺手扩大范围】**：做完所列事项即停；发现的新问题**记下来**，不顺手改。
7. **【铁律·付费服务先设闸】**：任何计费外部 API（同花顺等）先报告预估用量与金额、配硬上限、
   监控"累计花费"而非"进度"。未核实计费的服务**不接入**。
8. **【铁律·大操作即记录】**：改代码 / 采集 / 账本变更 / 里程碑完成后，**立即**更新
   `docs/agents/HANDOFF.md`（换人时）或 `docs/审计修复记录.md`（审计/修复类）并 commit。
   **记录是默认动作，不是可选动作。**

## 四、操作记忆：表达纪律

1. **陈述分级**：只对**进入结论**的陈述标一次级 ——
   `【实测】`本轮跑过（附命令/`文件:行号`）｜`【口径】`来自项目权威文档（**不等于本轮验证过**）｜
   `【文献】`附可查来源｜`【推测】`写明依据 + 什么证据能推翻它。
2. **不迎合**：与用户判断冲突时**先摆证据再给建议**；确信度低就直说没把握，
   **不许为显得独立而制造分歧**，也不许明知有错却顺着说。
3. **机械验收**：任务的"验收"必须写**命令 + 预期输出**（如 `pytest -q 得 806 passed`），
   **不写 prose**（如"数字一致"）。
4. **提醒预算**：只在"会改变结论是否成立"或"成本量级变化"时提醒，每轮最多 3 条、
   单独成节、不混进主回答；**没有达标项就一条都不写**。
5. **不许用"应该没问题"结案**：做完必须给出可检验的完成证据；工具反复失败时**先怀疑自己的用法**。

## 五、机械机制（**把约定变成红灯，而不是靠自觉**）

| 守卫 | 命令 | 挡住什么 |
|:---|:---|:---|
| 功能回归 | `python -m pytest tests/ -q` | 806 项功能断言（基线 **806 passed / 2 skipped**）|
| 数据契约（账本 + 市场数据） | `python scripts/check_ledger_invariants.py` | 值域 / 引用完整性 / 时序 / 覆盖率 / 新鲜度 / **复式记账恒等式**（24 项 = 账本 13 + 市场数据 11）|
| 前端真机 | `python scripts/verify_frontend.py` | 四入口渲染、导航断点、移动端零溢出、动效绑定、数据链路下钻（**33 项**）<br>⚠️ **必须先 `precompute` 预热快照**，否则「研究」在 warming 骨架屏上采样会假红 |
| **文档引用完整性** | `pytest tests/test_doc_refs.py -q` | 文档指向**仓库内不存在**的路径 |
| 同类结构体检 | `python scripts/analyze_peer_structure.py` | 同类相关基线（决定 `corr_overlap` 阈值是否还成立）|

> **为什么单独列出来**：本项目有 53 个测试文件、8,400 行，但 2026-10-01 之前**一条治理守卫都没有** ——
> 全是功能测试。于是"文档要更新""数字要对齐"只能靠自觉，而实测结果是**文档状态长期滞后**
> （2026-09-27 一次核查：5 处"未做"里 4 处其实已做）。
> 参考毕设补上第一条 `test_doc_refs.py` 后，它**当场抓到 2 处真断链**（`src/index.css` 少写 `frontend/`；
> 一处承诺的脚本早已被替代却仍写着"待实现"）。
> **凡是"以后要记得…"的约定，都该问一句：能不能写成测试。**

## 六、开工最小读取

1. 本文件 §二（命令）+ §三（铁律）
2. `docs/agents/HANDOFF.md` **最上面一个交接块**（上一轮做到哪 + 下一步 + 坑）
3. 你要做的那个任务对应的权威文档（见 §六）

> **只能读一个文件的话：读 `docs/agents/HANDOFF.md` 顶部交接块。**

## 七、项目记忆：去哪查（不重新推导）

| 要什么 | 去哪 |
|:---|:---|
| 项目是什么 / 模块地图 | `README.md` |
| **前端重构**（技术栈/分阶段/验收/已知缺口） | `docs/前端重构计划书.md` |
| ⭐ **前端合并方案 B**（决策 + 工作量 + B-0~B-6 施工详规 + 视觉判据） | `docs/前端重构计划书.md` **§10 / §11 / §12**（当前最高优先级工作） |
| **审计与修复史**（含三批事故与教训） | `docs/审计修复记录.md` |
| 设计约束（token / 前端冻结项） | `DESIGN.md` |
| 推荐系统设计与实施顺序 | `docs/基金推荐系统设计方案.md` |
| 数据源扩展计划 | `docs/数据源扩展计划书.md` |
| 现在到哪了 / 上一轮交接 | `docs/agents/HANDOFF.md` 顶部 |
| 个人速记（**不在 git 里，仅本机**） | `.workbuddy/memory/` |

> ⚠️ **引用 `docs/` 里"未做/未落地"的结论前，先 grep 代码核实** ——
> 本项目文档的状态标注**长期滞后**（2026-09-27 一次核查：5 处"未做"里 4 处已做）。
