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
决策支持系统重构 **阶段 0 + 阶段 1 + T2.0 ~ T2.2 已完成**（2026-10-15 ~ 16）：
- **T0.3**：守卫第 19 条改「净值新鲜度覆盖率 ≥ 90%」（负对照 15 passed；主库 24 项 0 失败）。
- **T1.1**：题目与入口对齐（README 全文重写 +「系统 vs 实例」诚实声明；`DESIGN` / `AGENTS` §一 / `docs/README`）。
- **T1.2**：权威口径重写（行为镜实测 + 反事实占位 + 体积三档；引用点已同步）。
- **T2.0**：行为口径对照表（8 口径全判【项目自定】）+ spot-check 9 项（2 项差异已记录，无阻断）。
- **T2.1**：决策台 7 个 API（`/api/behavior` `/api/counterfactual` `/api/breakeven` `/api/risk`
  `/api/peer` `/api/exclusion` `/api/research`〔`/api/quant_models` 已改名，**旧路径 404**〕）；
  参照系缓存**补 ter 网格并重建**（旧 5 指标逐位零漂移）；`tests/test_web_behavior_api.py` **8 例**。
- **T2.2**：删壳 —— **6 个占位类 + 6 个死路由**（funds / rebalance / sectors / sentiment / recommend /
  funds·board）+ 连带辅助 / 常量 / 后台预热全清；`precompute` 只剩 overview（**0.2s**）；
  **15** 个死路由测试移除（`test_api_degraded.py` 等，EXCEPTIONS 登记）；死缓存快照已清。
全量 `pytest` **534 passed**；守卫 24 项 0 失败；已推送（远端 = 本地）。

**下一步（按计划书 §7；顶层计划 `docs/05-重构/01_决策支持系统_执行计划书.md`）：**
1. **T2.3**（`main.py` 无参数修复 + docstring 对齐真实 25 命令）—— 阶段 2 收口。
2. **阶段 3**（前端决策台三页；**§3 契约 + 本块"注意事项"为唯一交接依据**；删壳后旧前端调死路由
   会 404，属**预期窗口期**，T3.2 重做前端时消解）→ **阶段 4** →
   **阶段 5**（T5.1 反事实正式整理后**回填**权威口径 §七）。可并行：T4.2 / T5.1。

**注意事项（坑）：**
- ℹ️ 参照系缓存（`data/peer_distributions.json`）**已于 2026-10-16 重建**：新增 **ter 网格**（6 组齐全），
  旧 5 指标与旧缓存**逐位一致**（零漂移）。⚠️ `peer` 分位是**原始分位**（越大 = 值越大；
  TER 越大 = 越贵 → 排雷判据用 ≥P90）。
- ℹ️ T2.2 已完成（原跨依赖已处理）：`precompute` 只剩 overview；`__dash__` 缓存槽保留
  （`_compute_dashboard` 现为 `_overview_compute` 的别名，`/api/all` 协议不动）。
- ⚠️ `_peer_block` / `_nav_spans` / `_user_constraints` 等**保留**（仍服务 `/api/explain`）——
  不要按"旧命题残留"误删。
- ⚠️ 第 19 条旧口径"物化表追平"**已弃**（其形态本身即假绿）→ 物化表同步不再由守卫检查；
  "只刷新部分数据"的操作会让第 19 条变红——**设计意图**（提示错位），全市场刷新后回落绿。
- ⚠️ `FRESH_WINDOW_TDAYS=5` / `FRESH_COVER_MIN_PCT=90` 是两个工程参数：改它们**必须同步**
  `tests/test_data_contract.py`。
- ⚠️ **已知未修**：守卫第 13 条对空 `index_daily` 会崩（`MIN(close)` 为 NULL → `%.2f` TypeError；
  修法 = 重排第 13 条 detail 构造，可随 T2.x 捎带）。见 `docs/agents/STATUS.md` 已知坑。
- ⚠️ `test_doc_refs.EXCEPTIONS` 现 **43** 条（T2.0/T2.1 产出物两条已按约删除；+1 为 T2.2 删掉的
  `tests/test_api_degraded.py`，被计划书与历史文档继续引用）。
- ⚠️ 计划书里写的"基线 538"是**任务起点**值；现行基线 **534**（`AGENTS.md` / `README` / 权威口径已同步）。
- ⚠️ 跑测试仍须：串行 + 全新 `--basetemp` + `CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=100000`。

**关键文件：** `docs/05-重构/01_决策支持系统_执行计划书.md`（顶层计划）、
`src/web/app.py`（决策台 7 路由 + 删壳后的精简结构）、`tests/test_web_behavior_api.py`（8 passed）、
`docs/00-规范/行为口径对照表.md`（T2.0 产出）、`docs/00-规范/结论权威口径.md`（已重写）、
`scripts/check_ledger_invariants.py`（第 19 条覆盖率判据）。
