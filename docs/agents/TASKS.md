# TASKS.md — 任务清单（只跳到你手上那一条）

> **预算 ≤200 行**，由 `tests/test_boot_budget.py` 钉住。**不要通读**；只跳到你手上那一条，看它的「验收」。
> 验收一律写**命令 + 预期输出**，不写散文。

---

## 执行顺序总览

| 批次 | 主题 | 状态 |
|:---|:---|:---|
| S0 | 治理骨架 + 重构蓝图 | ✅ 完成（2026-10-14）|
| S1 | 归档冻结命题层 | ✅ 全部完成（14 模块 → legacy/）|
| S1'' | src/ 目录分层（ingest/…/deliver）| ⏳ 待做 |
| S2 | ML 目标重定向（行为建模）| ⏳ 待做 |
| S3 | 一页行为审计交付物 + 前端 + 论文口径 | ⏳ 待做 |

---

## S0（已收口）
- [x] 清理整库快照 33 → 2（`data/` 30.8 GB → 7.3 GB）｜验收：`data/` 占用 **7.3 GB**、`fund_quant.db` `quick_check`=ok
- [x] 治理骨架（`AGENTS.md` / `docs/agents/` / `docs/00-规范/` / `docs/_archive/` / `test_boot_budget.py`）｜验收：`pytest tests/test_boot_budget.py -q` 全绿
- [x] 重构蓝图（题目 + 全链路 + 保留/冻结边界 + ML 重定向）｜验收：文件存在且被 `HANDOFF.md` 引用

## S1 —— 归档冻结命题层（安全子集已完成）
- [x] 归档冻结**无人引用的离线研究模块** → `legacy/`：
  `vol_model_comparison` `garch_baseline` `factor_test` `return_cluster` `temperature_history`
  （+ 脚本 `cluster_peer_check` `build_temperature_history`，+ 测试 3 个）
  ｜验收：`pytest -q` **812 passed**（新基线）
- [x] **S1'-a**：归档冻结 `sector_analyzer` `sentiment_monitor`（+ 测试 1）；摘 CLI `sector`/`sentiment`；
  web 侧两处 import 改**占位降级**。｜验收：`pytest -q` **804 passed**、CLI 命令表无 `sector`/`sentiment`
- [x] **S1'-b（口径拆分）**：新建保留模块 `src/analysis/fund_classify.py`（`type_bucket`/`format_fee`/申购标记）；
  `fund_scorer` re-export；`reporter`/`web`/`cli` 改指向。｜验收：`pytest -q` **804 passed**
- [x] **S1'-c1**：归档 `historical_recommender` `oos_validate`（+ 测试 2）；摘 CLI `recommend`/`oos`。
  ｜验收：`pytest -q` **772 passed**、CLI 无 `recommend`/`oos`
- [x] **S1'-c2**：归档 `thermometer` `fund_scorer` `rebalance_advisor` `strategy_engine` `backtest`
  + `src/output/reporter.py` + `src/cli/backtest_cmds.py`（+ 测试 15）；摘 CLI
  `score`/`temp`/`rebalance`/`strategy`/`backtest*`/`report`。｜验收：`pytest -q` **538 passed**、
  `import src.web.app` OK、CLI 25 命令
- [ ] **S1''（最后做）**：`src/` 目录分层 `ingest/governance/warehouse/features/ledger/audit/ml/deliver`。
  ｜验收：`pytest -q` 全绿 + `python src/main.py --help` 正常。

## S2 —— ML 目标重定向
**做**：复用 `recsys_dataset` 的 PIT 面板构造，把标签从「下期收益分位」换成
「**该笔买入最终是否亏损 / 是否触发 7 天惩罚费**」（标签来自 `holdings`+`transactions`）；
用现有 walk-forward + XGBoost/Ridge 跑一次。
**不做**：不引入新预测目标（预测市场=旧命题）。
**验收**：一条命令跑通并输出 AUC/IC + **小样本局限声明**（样本≈59 笔买入）。

## S3 —— 交付物与论文口径
**做**：把 `behavior` + `counterfactual` + `portfolio` 三个只读输出合成**一页行为漏损报告**
（追高 / 短持 / 赎回费 / 卖飞 / 同标的择时损失 5 个数字）；回填 `结论权威口径.md` 与 `口径引用索引.md`。
**不做**：不碰前端视觉、不改入口。
**验收**：一条命令产出该页；5 个数字**均可在库中指明来源**。
