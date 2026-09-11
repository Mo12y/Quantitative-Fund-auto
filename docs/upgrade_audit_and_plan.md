# 二次升级 —— 底层代码审计汇总 与 改造方案

> **状态回填（2026-09-11）**：本文件是**改造计划**，正文里的"问题/后续"描述的是**计划制定时**的状态。
> A–D 四批审计修复（含"修复任务书"里的条目）已在 2026-09-11 全部执行完毕，但**下面的勾选状态没有逐条回填**：
> - ✅ 已确认完成：`simulation 前视`（组合模拟仓位信号改为严格滞后）、`rebalance 单位`（总资金改为市值+现金弹药）、
>   `索引`（`fund_nav`/`holdings`/`fund_info`/`transactions` 四张表已有索引）、`upsert 批量`、离线温度路径。
> - ⬜ 仍**未做**：`sector_analyzer` / `sentiment_monitor` 的模块级 `import akshare`（仍是 try 包裹的急切导入，
>   尚未改成函数内 lazy）；`RebalanceAdvisor` 内部重复跑 `screen_funds`；`/api/all` 拆分为分面板懒加载。
> - 勾选状态以外的细节，以各批汇报与重新生成的 `docs/*_report.md` 为准。
> - 测试基线：83 → **177** 个用例，`python -m pytest tests/ -q` 全绿。
>
> 生成时间：2026（本会话）。来源：3 个并行只读审计子代理（analysis / data-cli-config / web+perf）
> + 主代理直接性能剖析（对 149MB `fund_quant.db` 与各分析链路的实测）。
> 本文件仅汇总“已收集问题”与“改造计划”；随实施推进逐步勾选。

## 0. 用户需求与方向（已确认）
- 前端改为 **侧边栏布局**（用户未做过侧边栏）。
- 优化 **打开 Web 仪表盘的首屏速度**（非数据采集命令）。
- 底层代码“先收集全部问题，再统一修改”。

## 1. 首屏/启动耗时根因（高优）
| 位置 | 问题 | 处理 |
|---|---|---|
| `thermometer.get_temperature()` | 每次调用都联网 akshare（LPR + 2×sh000300 + 5×申万风格指数），~11s，失败静默回落 50 | **已修**：新增离线(读本地 `index_daily`/`index_valuation`)路径，`QFA_MARKET_LIVE=0` 走本地；web 默认离线。实测 11s→0.02s |
| `RebalanceAdvisor.analyze()` | 内部重算 thermometer + screener，与 `/api/all` 其它 worker 重复 | 部分缓解：thermometer 离线后该重复变便宜；仍需避免其内部 `screen_funds` 重算（后续优化） |
| `sector_analyzer` / `sentiment_monitor` | 模块级 `import akshare`（启动重、急切）；thermometer 却是函数内 lazy，不一致 | 后续：改函数内 lazy import |
| `cmd_web` (main.py web) | `app.run` 无 `threaded=True`，单线程下慢请求阻塞异步卡片；且重复启动板块预计算 | **已修**：`cmd_web` 改为调用 `app.main()`（threaded=True + 单一入口） |
| `/api/all` 冷启动 ~10s | 本地并行已做；首屏仍等最慢 worker（funds 5.7s + rebalance） | 后续：拆“轻量总览”先出（temp/portfolio/plan），重 section 独立懒加载入各自面板 |
| sector 首屏兜底 | 冷缓存时请求内同步抓 31 行业，且与启动预计算线程重复抓 | 后续：single-flight + 文件缓存统一写入口 |
| `/api/quant_models` | 每次读/解析 4 个 CSV，未缓存 | 后续：按文件 mtime 缓存 |

## 2. 已实施修复（本轮基础层）
- `src/web/app.py`：新增全局 `_SafeJSONProvider`，任何 NaN/±Inf→null（否则浏览器 `JSON.parse` 抛错显示“连接失败”）；`_get_nav_trend` 对 NULL `unit_nav` 跳过（不再 TypeError 拖垮基金区）；`api_all` 增加 **single-flight**（并发 cache miss 只算一次）；web 默认 `QFA_MARKET_LIVE=0`（离线快温度）。
- `src/analysis/thermometer.py`：新增离线路径（`_hs300_df` 读本地 index_daily；offline 时 LPR→3.0、风格→unknown、成交量维度因本地无 volume 为中性50）；`live` 由 `QFA_MARKET_LIVE` 环境变量控制（CLI 默认仍联网）。
- `src/data/database.py`：新增 `get_index_daily(index_code)`（供离线温度复用，避免裸 SQL）。
- `src/cli/output_cmds.py` `cmd_web`：委托 `app.main()`（threaded + 单入口）。
- `src/output/reporter.py`：`quick_report` 的 `FundScorer`→`FundScreener`（修复 NameError），并补 import。

实测：`/api/all` 冷载 ~10s（原联网不稳 15–60s）、缓存 45ms、offline 温度 0.02s、HTTP 无 NaN/Infinity。

### 追加修复（后续轮次）
- `reporter.generate_full_report`：先判定“极简模式”再跑昂贵的筛选池（原先把最重的筛选算了才判断是否可跳过）。
- `cmd_update`（修改持仓金额）：买入净值未知时不再把份额强制清零，而是只更新金额并提示；找不到持仓会报错。
- `database.upsert_fund_info`：ON CONFLICT 补齐 custodian/purchase/redeem_fee 与 risk_level/investment_style（重采不再静默不刷新这些字段）。
- `database.__init__`：幂等补索引 idx_holdings_status / idx_fund_info_mgt_fee / idx_fund_nav_date（实测已建，upsert 正常）。

### 追加修复（round 5）
- `historical_recommender`：`_load_nav_tuples` 弃用固定 `LIMIT 300`（会截断多年回测的早期月份），改为按回测窗口日期过滤加载（实测 1 只 2092 行、升序）；
  缓存 `_fund_types()` 映射消除 `_get_fund_info` 逐基金全表 O(F²)（候选从慢→0.18s）。副作用：`/api/recommend` 由 15–20s 降到 ~0.5s。
- `api_sectors` / `_precompute_sectors`：新增 single-flight（`_sectors_computing`/`_sectors_done`），冷兜底与启动预计算不再重复抓 31 行业；内存缓存取引用在锁外序列化。
- 回归：`pytest -q tests` **49 通过**（含改过的 thermometer/database 等），无回归。

### 追加修复（round 6）
- `rebalance_advisor`：触发调仓的偏差阈值从“绝对金额 ¥5”改成**权益仓位百分点**（`REBALANCE_PP=5.0`），并与 `need_rebalance` 同源（原指令按元±5判断、标志按 pp>10，口径不一致会矛盾）；卖出下限 ¥10 不再会超过“仍需卖出额”。
- `collector.save_fund_nav_batch`：按列名识别累计净值/日增长率，修复 akshare(无累计净值)把第 3 列日增长率误当累计净值入库的污染（实测 akshare acc=0、efinance acc=累计值）。

## 3. 底层代码明显问题（审计汇总，拟后续修复，按优先级）
### analysis 层（high 优先）
- `historical_recommender._load_nav_tuples` LIMIT 300 截断历史，与 lookback_years 不符 → 旧采样月无推荐却报满格。
- `historical_recommender._get_fund_info` 每候选重载并线性扫全表 fund_info → O(F²)。
- `sentiment_monitor` 在逐基金循环内重复下载公告全表（≤20×），裸 except 把全失败当 all_clear。
- `rebalance_advisor` 调仓阈值单位不一致：`_generate_instructions` 用元(±5) 判断，`analyze` 用 pp(>10) → 指令与 need_rebalance 矛盾；卖出下限 ¥10 可能超卖。
- `backtest.py` `select_top_n_at` 每期每基金全量 `get_fund_nav`；卖出费按旧基准值算。
- `portfolio.get_performance_history` O(周×行) 全扫描 + 每基金全历史 akshare。
- `strategy_engine.get_weekly_suggestion` 调 get_temperature 2–3×；“占仓位%”除 10000 魔法数。
- `portfolio_simulation` 把 month-(m+1) 目标信号用在 month-m 收益上（1 月错位/前视）；组合基金按全 OOS 覆盖筛选（窗内幸存者偏差）。
- `drawdown_warning` 尾部月份错标 0（shift(-1) NaN→0 未 drop）污染训练。
- `fund_scorer` pool 筛选逐基金全历史查询；“近1年”回撤在不足 252 行时用了全历史。
- `factor_test` bench_tr 恒 True，回落仍标“全收益”。
- sector 映射在不同模块(drawdown/sector_analyzer vs factor_test)不一致；成本常量/HAR/walk-forward 重复。

### data / cli / config / output 层
- `database.upsert_fund_info` 每行 commit（~20k 单行事务）；ON CONFLICT 只更一部分列（custodian/purchase/redeem/risk 静默不更新）。
- `collector.save_fund_nav_batch` 用列位置启发式把日增长率当累计净值（毒化 NAV）；无任何网络超时。
- `hithink_collector` 宽 except→None 无日志；ms→date 时区无关（非 +8 偏移一天）；类从未被实例化（死代码但 README 宣传）。
- `config.py` 配置中心几乎无人用（22 处硬编码 `Database("data/fund_quant.db")`）；settings.yaml 大段死配置；相对路径与 CWD 矛盾。
- `main.py` 分发不传 argv[2:]（`buy --help` 会进交互）；README 命令与实际不一致。
- `reporter` 模块导入时若 rich 缺失会 print（web 导入也会触发）。
- 缺索引：holdings(status,buy_date)、fund_info(mgt_fee)。

### web 层
- `api_recommend`(15–20s) 未缓存且页面从未调用（死路由，文档还宣传其卡片）。
- `api_all` 各 section 失败静默吞错，前端显示成 0/空（“0.0°/暂无基金”）无提示。
- `api_sectors` 冷兜底与预计算线程竞态重复抓；jsonify 在锁内。
- 缓存无 bypass（刷新在 TTL 内返回旧 cache），无磁盘持久化（除 sectors）。
- 建议 `/api/overview` 轻量先出 + 重 section 懒加载（配合侧边栏改版）。

### 前端 JS/CSS（将随侧边栏改版一并做）
- 单一巨型 innerHTML 重建；每次刷新全量重取 4 接口、重建 ~100 节点。
- 三个卡片加载/重试脚手架几乎重复；`sleep` 声明在使用之后。
- `esc()` 仅在 quant/sentiment 错误路径用；持仓/基金/计划/信号名裸插 innerHTML（存储型 XSS 面）。
- 布局随数据变化/分组混乱/无“无需调仓/暂无持仓”态；仓位卡与计划卡金额口径不一致。
- CSS 扁平散乱、~40 处内联、死规则 `.risk-section/.risk-title/.warn-tag`、死函数 `riskColor()`。
- `chartHTML` 空值画成 0 点（折线下坠）——应断点。

## 4. 改造方案（路线）
1. ✅（已完成）审计收集 + 首屏基础修复（离线温度/NaN/单飞/threaded/小 bug）。
2. ✅（已完成）前端**侧边栏布局**改版：5 个主题面板（总览 / 持仓·计划·调仓 / 筛选池 / 板块 / 量化模型），
   固定侧边栏 + KPI 卡 + 空态（暂无持仓/无需调仓）+ 统一 HTML 转义 + 复用加载脚手架 + 移动端顶栏适配；
   用 node 端到端 harness 验证全部面板渲染无错、HTTP 正常提供新模板。
3. ✅（已完成）**首屏“轻量总览先出”**：新增快速 `/api/overview`（温度+持仓+计划，并行 ~3.4s）+ 通用单飞缓存 `_cached_get`；
   新增懒加载 `/api/funds`、`/api/rebalance`（仅打开对应面板才触发）；前端 `boot()` 两阶段：总览 3.4s 先渲染，后台补齐完整数据再切完整布局。
   实测：总览冷载 3.4s（原 /api/all ~10s）/ 缓存 14ms；`/api/rebalance` 因离线温度降为 ~1.3s。node 端到端 harness 验证两阶段无错。
4. ⬜ 底层明显问题按上表优先级修复（历史推荐 O(F²)/LIMIT、rebalance 单位、simulation 前视、upsert 批量、collector 布局、索引、lazy akshare 等）。
5. ⬜ 浏览器端到端回归验证 + 回归测试（现有 34/9 用例不一致需核对）。

## 收尾与验证（round 7 实测）
- 端到端（HTTP 冒烟，服务 http://127.0.0.1:5020 最新代码）：`/` 200、`/api/overview` 冷 3.15s/缓存、`/api/all` 冷 10.1s/缓存 45ms、`/api/quant_models` 33ms、`/api/recommend` 584ms。
- 严格区分大小写探测：各端点 JSON **无 `NaN`/`Infinity`** token（早前 PowerShell `-match` 为大小写不敏感误报）。
- 回归：`pytest -q tests` = **49 passed**。
- 前端两阶段启动 + 5 面板/异步卡用 Node DOM harness 端到端渲染验证通过（全部 PASS）。
- 环境限制：真实浏览器截图/渲染受本沙箱限制（Chrome/Edge 无法启动子进程，Access denied），改用 HTTP + Node DOM harness 作浏览器端到端代理；已在真实服务器上人工可访问验证。
- 改动范围：9 个 src 文件（app.py、dashboard.html、thermometer/database/collector/rebalance_advisor/historical_recommender/reporter/output_cmds）+ 本审计文档。

### 清理（补充轮次，已处理）
- `main.py`：usage docstring 与真实命令对齐（补齐 test/index/hithink/backtest*/strategy 等）；子命令带 `--help/-h` 时输出总帮助，避免 `buy --help` 误进交互。
- `config.get_db_path()`：相对路径按项目根目录解析（不再随 CWD 漂移）；settings.yaml 解析失败时向 stderr 提示而非静默回退。
- `cmd_hithink/_load_api_key`：优先读环境变量 `HITHINK_API_KEY`，其次 .env。
- `cmd_enrich`/`cmd_backtest`：用 `Database.get_all_fund_codes()` 取代手写 `SELECT DISTINCT` 裸 SQL。
- 回归：`pytest -q tests` 49 通过。

### 遗留（受环境/侵入面限制，按需再处理）
- `config.py` 死配置段（settings.yaml 多数 section 代码未消费）——需贯通 22 处硬编码调用点，改动面大。
- README/使用指南 部分文案与命令漂移、旧文案仍宣传“历史验证推荐卡片”。
- /api/all 各 section 失败静默吞错 → 前端应显式标注“该区块失败”而非显示 0/空。
- 浏览器真实像素级截图无法在本沙箱产出（Chrome/Edge 子进程被禁）。
