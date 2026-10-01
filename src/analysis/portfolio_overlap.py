"""组合重叠度（相关性级）—— 回答「我的分散是不是假的」（设计稿 §4.4 / 数据源计划书 阶段 3 的替代实现）。

为什么是"相关性级"而不是"成分股穿透"（2026-09-26 只读实测，见 `.workbuddy/memory/2026-09-26.md`）：

| 档位 | 实测结果（用户 7 只持仓） |
|:--|:--|
| 标的级（跟踪标的是否同一个） | **0 检出** —— 7 只分别跟踪 中证500 / 无 / 纳斯达克100 / 科创芯片 / 上海金 / 富时亚太 / 创业板AI |
| **相关性级（近 1 年周收益）** | **5/21 对 r ≥ 0.60**，核心簇 `信澳业绩驱动 ~ 创业板AI = 0.88`、`科创芯片 ~ 中证500 = 0.79` |

也就是说：**主题词层面看着分散（芯片 / AI / 中证500 / 主动混合），"会不会一起跌"这一维并不分散** ——
而板块关键词法看不见这一层。成分股穿透（联接基金→ETF→成分股）成本高、且季报滞后 1–3 个月，
对"全是指数/联接"的持仓与相关性级结论基本等价，故本模块选相关性级。

口径（每一条都可追溯）
--------------------
- 净值列走 SSOT `nav_series.VALUATION_NAV_SQL`（累计净值优先、逐行回退）——**不自己挑列**
- 周频：每自然周取**最后一个**净值点；周收益 = 相邻周比值
- 窗口默认 **400 天（≈56 周）**；少于 `min_weeks=20` 周 → **不产出结果**（由调用方声明"未评估"）
- 相关性 = Pearson r（周收益序列，取两序列等长的尾部）

⚠️ **阈值是政策、不是口径**：本模块只算 r（与**类对基线**）；"r 多大算重叠"由
`user_constraint` 的约束参数决定。两种模式（见 2026-10-01 的实测，docs/审计修复记录.md 第四批 §13）：

- **相对基线（推荐）**：门限 = `max(该类对 μ + k·σ, 绝对底线)`；底数来自
  `build_baselines()` 产出的 `data/overlap_baselines.json`（用**同一估计器、同一窗口**测得）。
- **绝对阈值（legacy）**：画像键 `corr_max_r`，默认不启用。

阈值属**统计判断值**（本项目吃过"拍脑袋阈值"的亏），启用时必须在 UI/文档里标明性质与依据。
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

import numpy as np

from .nav_series import VALUATION_NAV_SQL

DEFAULT_WINDOW_DAYS = 400      # ≈1 年（56 周）
DEFAULT_MIN_WEEKS = 20

# ── 类对基线（"相对同类基线"判定的底数，2026-10-01）────────────────────
# 为什么要它：绝对阈值 0.8 对不同类对没有意义 —— 实测（1 年窗、本模块同一估计器）：
#   指数型-股票 类内 μ=0.46±0.35 ｜ 混合型-偏股 0.40±0.37 ｜ 指数×海外 0.31±0.21
#   ｜ 指数×债券型-长债 −0.05±0.12 —— 跨类差异比"0.8 还是 0.7"大得多。
# 阈值政策（k / 底线）在 `user_constraint`；本模块只产出**分布底数**。
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASELINE_PATH = os.path.join(ROOT, "data", "overlap_baselines.json")

DEFAULT_SAMPLE_K = 50          # 每类抽样只数（固定 seed，可复现）
DEFAULT_SEED = 42
DEFAULT_ACTIVE_DAYS = 90       # 只从"近 90 天有净值"的存活基金里抽（死基金窗口内没数据、白占样本）
MIN_UNIVERSE = 40              # 类内（存活）基金数 < 此值 → 不建该类的基线


def weekly_returns(conn, codes, since: str = None) -> dict:
    """批量取**周收益**序列 → `{code: np.ndarray}`（一次查询，避免逐只查库）。

    codes 为空/无净值 → 该 code 不出现在结果里（调用方按"无数据"处理，不猜）。
    """
    codes = [str(c) for c in (codes or []) if c]
    if not codes:
        return {}
    since = since or (date.today() - timedelta(days=DEFAULT_WINDOW_DAYS)).isoformat()
    ph = ",".join("?" * len(codes))
    rows = conn.execute(
        "SELECT fund_code, nav_date, %s AS v FROM fund_nav "
        "WHERE fund_code IN (%s) AND nav_date >= ? ORDER BY fund_code, nav_date" % (VALUATION_NAV_SQL, ph),
        list(codes) + [since]).fetchall()

    weekly = {}
    for code, d, v in rows:                      # 已按 code, date 排序 → 同周后写覆盖前写=取最后一点
        if v is None:
            continue
        try:
            y, w, _ = date.fromisoformat(str(d)[:10]).isocalendar()
        except ValueError:
            continue
        weekly.setdefault(str(code), {})[(y, w)] = float(v)

    out = {}
    for code, wk in weekly.items():
        ks = sorted(wk)
        vals = np.array([wk[k] for k in ks], dtype=float)
        if len(vals) < 3:
            continue
        rets = np.diff(vals) / vals[:-1]
        rets = rets[np.isfinite(rets)]
        if rets.size:
            out[code] = rets
    return out


def overlap_map(conn, candidate_codes, held_codes, since: str = None,
                min_weeks: int = DEFAULT_MIN_WEEKS) -> dict:
    """候选 × 持仓的**最大相关性** → `{candidate: {"max_r", "against", "n", "cand_class", "held_class"}}`。

    - 只包含**两者都有足够周数（≥ min_weeks）**的候选；其余不出现（调用方声明"未评估"）。
    - 自己与自己（候选就是已持有的那只）**不参与**：那种情况由 `holding_overlap` 的
      "已持有"判定处理，这里重复算 r=1.00 只会产生误导性的理由。
    - `against` = 相关性最高的那只持仓代码；`n` = 参与计算的周数（可解释性要用）。
    - `cand_class` / `held_class` = 双方 `fund_info.fund_type`（相对基线判定要用；
      查不到 → None，由调用方按"数据缺失"声明未评估，**不猜**）。
    """
    held = [str(c) for c in (held_codes or []) if c]
    cands = [str(c) for c in (candidate_codes or []) if c]
    if not held or not cands:
        return {}
    series = weekly_returns(conn, sorted(set(held) | set(cands)), since)
    cls = class_of(conn, set(held) | set(cands))
    out = {}
    for c in cands:
        rc = series.get(c)
        if rc is None or rc.size < min_weeks:
            continue
        best_r, best_code, best_n = None, None, None
        for h in held:
            if h == c:
                continue
            rh = series.get(h)
            if rh is None or rh.size < min_weeks:
                continue
            n = min(rc.size, rh.size)
            if n < min_weeks:
                continue
            r = float(np.corrcoef(rc[-n:], rh[-n:])[0, 1])
            if not np.isfinite(r):
                continue
            if best_r is None or r > best_r:
                best_r, best_code, best_n = r, h, n
        if best_r is not None:
            cc, hc = cls.get(c), cls.get(best_code)
            out[c] = {"max_r": round(best_r, 4), "against": best_code, "n": int(best_n),
                      # 类对（相对基线判定用）：键格式与 build_baselines 的 pairs 一致；
                      # 缺分类 → None，由调用方按要求声明"未评估"（不猜）
                      "cand_class": cc, "held_class": hc,
                      "pair": pair_key(cc, hc) if (cc and hc) else None}
    return out


def pair_key(a: str, b: str) -> str:
    """类对键（与顺序无关）：`"指数型-股票|混合型-偏股"`（两段按字典序排列）。"""
    return "|".join(sorted([str(a), str(b)]))


def class_of(conn, codes) -> dict:
    """代码 → `fund_info.fund_type`。查不到 / 类型为空的代码**不出现**（调用方按缺失处理）。"""
    codes = [str(c) for c in (codes or []) if c]
    if not codes:
        return {}
    out = {}
    CH = 400
    for i in range(0, len(codes), CH):
        chunk = codes[i:i + CH]
        ph = ",".join("?" * len(chunk))
        for code, ft in conn.execute(
                "SELECT fund_code, fund_type FROM fund_info WHERE fund_code IN (%s)" % ph, chunk):
            if ft:
                out[str(code)] = str(ft)
    return out


def class_pair_baselines(series: dict, code_class: dict,
                         min_weeks: int = DEFAULT_MIN_WEEKS) -> dict:
    """从 `{code: 周收益}` + `{code: 类}` 计算**类对**的相关分布（纯计算，可注入合成数据测试）。

    与 `overlap_map` **同一估计器**（尾部对齐 Pearson r、同 min_weeks），
    所以门限与候选的 r 是同一把尺子量的。

    返回 `{pair_key: {"mu", "sigma", "n_pairs", "p90", "p95"}}`；
    同类对取上三角、跨类对取全交叉；没有任何可用基金对的类对**不出现**。
    """
    by_class = {}
    for code, ft in (code_class or {}).items():
        arr = series.get(code)
        if arr is None or arr.size < min_weeks:
            continue
        by_class.setdefault(str(ft), []).append(code)
    classes = sorted(by_class)
    out = {}
    for i, a in enumerate(classes):
        for b in classes[i:]:
            same = (a == b)
            rs = []
            for pa, ca in enumerate(by_class[a]):
                ra = series[ca]
                for pb, cb in enumerate(by_class[b]):
                    if same and pb <= pa:
                        continue
                    rb = series[cb]
                    n = min(ra.size, rb.size)
                    if n < min_weeks:
                        continue
                    r = float(np.corrcoef(ra[-n:], rb[-n:])[0, 1])
                    if np.isfinite(r):
                        rs.append(r)
            if not rs:
                continue
            arr = np.asarray(rs)
            out[pair_key(a, b)] = {
                "mu": round(float(arr.mean()), 4),
                "sigma": round(float(arr.std(ddof=1)) if arr.size > 1 else 0.0, 4),
                "n_pairs": int(arr.size),
                "p90": round(float(np.percentile(arr, 90)), 4),
                "p95": round(float(np.percentile(arr, 95)), 4),
            }
    return out


def build_baselines(conn, sample_k: int = DEFAULT_SAMPLE_K, seed: int = DEFAULT_SEED,
                    window_days: int = DEFAULT_WINDOW_DAYS,
                    active_days: int = DEFAULT_ACTIVE_DAYS,
                    min_universe: int = MIN_UNIVERSE,
                    min_weeks: int = DEFAULT_MIN_WEEKS) -> dict:
    """在真实库上构建类对基线 artifact（**只读账本**；落盘由调用方决定）。

    抽样纪律：
    - 只从**近期存活**（近 `active_days` 天有净值）的基金里抽 —— 死基金在回看窗里
      没有序列，会白占样本（实测：不加此滤条件时 800 只里只有 509 只有效）；
    - 每类抽 `sample_k` 只、固定 `seed`（可复现）；类内存活数 < `min_universe` 不建该类。
    """
    since = (date.today() - timedelta(days=window_days)).isoformat()
    active_since = (date.today() - timedelta(days=active_days)).isoformat()
    rng = np.random.default_rng(seed)
    sample, universe = {}, {}
    for ft, n in conn.execute(
            "SELECT f.fund_type, COUNT(DISTINCT f.fund_code) FROM fund_info f "
            "JOIN fund_nav v ON v.fund_code = f.fund_code AND v.nav_date >= ? "
            "WHERE f.fund_type IS NOT NULL AND f.fund_type != '' "
            "GROUP BY f.fund_type ORDER BY f.fund_type", (active_since,)):
        if n < min_universe:
            continue
        codes = [r[0] for r in conn.execute(
            "SELECT DISTINCT f.fund_code FROM fund_info f "
            "JOIN fund_nav v ON v.fund_code = f.fund_code AND v.nav_date >= ? "
            "WHERE f.fund_type = ? ORDER BY f.fund_code", (active_since, ft))]
        universe[ft] = int(n)
        if len(codes) > sample_k:
            idx = sorted(rng.choice(len(codes), size=sample_k, replace=False))
            codes = [codes[i] for i in idx]
        sample[ft] = codes
    all_codes = [c for cs in sample.values() for c in cs]
    ser = weekly_returns(conn, all_codes, since)
    code_class = {c: ft for ft, cs in sample.items() for c in cs}
    return {
        "as_of": date.today().isoformat(),
        "window_days": window_days, "since": since,
        "sample_k": sample_k, "seed": seed, "min_weeks": min_weeks,
        "min_universe": min_universe, "active_days": active_days,
        "classes": {ft: {"n_universe_active": universe[ft], "n_sample": len(cs),
                         "n_series": sum(1 for c in cs if c in ser)}
                    for ft, cs in sample.items()},
        "pairs": class_pair_baselines(ser, code_class, min_weeks),
        "note": "类对周收益相关分布（同一估计器：尾部对齐 Pearson r，见 portfolio_overlap）。"
                "阈值政策在 user_constraint：相对模式 max(mu+k*sigma, floor)。",
    }


def save_baselines(data: dict, path: str = None) -> str:
    """写 artifact（默认 `data/overlap_baselines.json`）。返回实际写入路径。"""
    path = path or BASELINE_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return path


def load_baselines(path: str = None) -> dict | None:
    """读类对基线 artifact；不存在/损坏 → `None`。

    ⚠️ 调用方拿到 `None` 时**必须显式声明"未评估"**（skipped），不得当成"通过"。
    """
    path = path or BASELINE_PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("pairs"), dict):
        return None
    return data