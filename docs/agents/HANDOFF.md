# HANDOFF.md — 智能体交接记录

> **用途：切换智能体时，交接方写「我做到哪了」，接手方读完就知道从哪继续。**
> **只有最上面一个交接块是「当前」**；历史块进 `docs/_archive/`。**预算 ≤160 行**，由 `tests/test_boot_budget.py` 钉住。

---

## 交接块模板

```markdown
### 交接：YYYY-MM-DD（A → B）

**当前进度：** 一句话说明做到哪了

**下一步：** 接手方要做什么（具体到可执行）

**注意事项：** 有什么坑、什么文件别动、什么还没对齐

**关键文件：** 涉及哪些文件的最新状态
```

---

## 交接记录

### 交接：2026-10-16（DSH → 执行智能体）【决策支持系统重构 · 进行中】

**当前进度：**
决策支持系统重构 **阶段 0 + 阶段 1 + T2.0 已完成**（2026-10-15 ~ 16）：
- **T0.3**：守卫第 19 条改「净值新鲜度覆盖率 ≥ 90%」（负对照 15 passed；主库 24 项 0 失败）。
- **T1.1**：题目与入口对齐（README 全文重写 +「系统 vs 实例」诚实声明；`DESIGN` / `AGENTS` §一 / `docs/README`）。
- **T1.2**：权威口径重写（行为镜实测 + 反事实占位 + 体积三档；引用点已同步，含 `AGENTS.md` 基线 538→541）。
- **T2.0**：行为口径对照表（8 口径全判【项目自定】）+ spot-check 9 项（2 项差异已记录，无阻断）。
全量 `pytest` **541 passed**；守卫 24 项 0 失败；已推送（远端 = 本地）。

**下一步（按计划书 §7；顶层计划 `docs/05-重构/01_决策支持系统_执行计划书.md`）：**
1. **T2.1**（决策台 7 个 API：`/api/behavior` `/api/counterfactual` `/api/breakeven` `/api/risk` `/api/peer`
   `/api/exclusion` `/api/research`）→ **T2.2**（删壳 + `precompute` 同步删 job）→ **T2.3**（`main.py` 无参数）。
2. **阶段 3**（前端决策台三页）→ **阶段 4** → **阶段 5**（T5.1 反事实正式整理后**回填**权威口径 §七）。
   可并行：T4.2 / T5.1。

**注意事项（坑）：**
- ⚠️ T2.1 前置：`data/peer_distributions.json`（version==2）**已存在**（10-03 生成）✓；契约见计划书 §3。
- ⚠️ T2.2 跨依赖：`cmd_precompute` 引用了 `_all_funds` / `_all_rebalance` / `_compute_board_pool` ——
  删路由时必须**同步删 job**（计划书 §10.2），否则 `precompute` 崩。
- ⚠️ 第 19 条旧口径"物化表追平"**已弃**（其形态本身即假绿）→ 物化表同步不再由守卫检查；
  "只刷新部分数据"的操作会让第 19 条变红——**设计意图**（提示错位），全市场刷新后回落绿。
- ⚠️ `FRESH_WINDOW_TDAYS=5` / `FRESH_COVER_MIN_PCT=90` 是两个工程参数：改它们**必须同步**
  `tests/test_data_contract.py`。
- ⚠️ **已知未修**：守卫第 13 条对空 `index_daily` 会崩（`MIN(close)` 为 NULL → `%.2f` TypeError；
  修法 = 重排第 13 条 detail 构造，可随 T2.x 捎带）。见 `docs/agents/STATUS.md` 已知坑。
- ⚠️ `test_doc_refs.EXCEPTIONS` 现 **43** 条；**T2.1 产出 `tests/test_web_behavior_api.py` 后删该条登记**
  （T2.0 产出物的登记已按约删除，示范在案）。
- ⚠️ 计划书里写的"基线 538"是**任务起点**值；现行基线 **541**（`AGENTS.md` 已同步）。
- ⚠️ 跑测试仍须：串行 + 全新 `--basetemp` + `CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=100000`。

**关键文件：** `docs/05-重构/01_决策支持系统_执行计划书.md`（顶层计划）、
`docs/00-规范/行为口径对照表.md`（T2.0 产出）、`docs/00-规范/结论权威口径.md`（已重写）、
`scripts/check_ledger_invariants.py`（第 19 条覆盖率判据）、`tests/test_data_contract.py`（15 passed）。
