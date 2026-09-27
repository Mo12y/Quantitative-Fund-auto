"""净值双源校验（数据源扩展计划书 阶段 2）—— **只读比对，绝不覆盖主源**。

设计取舍
--------
计划书原文建议"主源 akshare 写入，同花顺只读比对"。但同花顺的配额/计费未核实，
按项目铁律「付费服务先设闸」**不接入**；改用**天天基金 pingzhongdata**
（与 akshare 走的 JSON 接口不同路径）作为第二源。这不是"两家数据商互校"，
而是"同一数据商两条不同链路互校" —— 它能抓住**解析/口径 bug**（本项目历史上
出过时区与 ms_to_date 的解析事故），但抓不到"东财整体算错"。**这句话必须写在报告里。**

本模块只做**纯比对**（给定两份序列 → 差异与分级），IO（抓取/读库/出报告）在
`scripts/verify_nav_dual_source.py`。拆开的原因：比对逻辑要能用注入数据做
**确定性测试**（验收条款要求"故意注入一个日期错位，证明校验器能抓到"）。

铁律
----
- **绝不写库**：本模块只做纯计算，不触碰数据库、不含任何写语句。
  校验失败只出报告，由人决定怎么处理 —— 自动覆盖等于把"发现错误的能力"换成"传播错误"。
- **差异分级不静默**：舍入差 / 轻微差 / 严重差 / 单侧缺失，分开计数，不揉成一个"不一致率"。
"""
from __future__ import annotations

# 分级（严重度递增）
SEV_OK = "ok"                # 完全一致
SEV_ROUNDING = "rounding"    # 仅浮点/舍入差（≤ round_tol）
SEV_MINOR = "minor"          # 相对差 ≤ minor_tol
SEV_MAJOR = "major"          # 相对差 > minor_tol
SEV_MISSING_REMOTE = "missing_remote"   # 本地有、远端无
SEV_MISSING_LOCAL = "missing_local"     # 远端有、本地无

ROUND_TOL = 1e-4             # 净值一般 4 位小数，绝对差 ≤1e-4 视为完全一致
ROUND_ABS_TOL = 1e-3         # 绝对差 ≤1e-3 → 精度差异（如一侧只给 3 位小数）
MINOR_TOL = 0.005            # 相对差 0.5%


def classify(local_v, remote_v, round_tol: float = ROUND_TOL,
             minor_tol: float = MINOR_TOL, round_abs_tol: float = ROUND_ABS_TOL) -> tuple:
    """两值对比 → `(严重度, 相对差或 None)`。相对差为小数（0.001 = 0.1%）。

    分级（**绝对差优先**，因为净值是小数量级）：
      · `≤1e-4`      → ok（4 位小数舍入）
      · `≤1e-3`      → rounding（一侧精度不同，如 1.0000 vs 1.0005）
      · 相对差 ≤0.5% → minor
      · 其它         → major
    """
    if local_v is None or remote_v is None:
        # local 缺 → 本地没这条；remote 缺 → 远端没这条
        return SEV_MISSING_REMOTE if remote_v is None else SEV_MISSING_LOCAL, None
    try:
        a, b = float(local_v), float(remote_v)
    except (TypeError, ValueError):
        return SEV_MAJOR, None
    d = abs(a - b)
    if d <= round_tol:
        return SEV_OK, 0.0
    base = abs(b) if abs(b) > 0 else 1.0
    rel = d / base
    if d <= round_abs_tol:
        return SEV_ROUNDING, rel
    return (SEV_MINOR if rel <= minor_tol else SEV_MAJOR), rel


def detect_shift(local: dict, remote: dict, date: str, field: str = "unit_nav",
                 local_v=None, max_shift: int = 3) -> int | None:
    """**日期错位识别**：本地 `date` 的值是否等于远端 `date±k` 的值？

    这是阶段 2 验收的核心："故意注入一个日期错位，证明校验器能抓到"。
    仅在"同日比对不上账"时才调用 —— 若对得上，就不是错位。

    Returns 偏移天数（正=本地比远端晚；负=早），无匹配返回 None。
    """
    from datetime import date as _d, timedelta

    v = local_v if local_v is not None else (local.get(date) or {}).get(field)
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    try:
        base = _d.fromisoformat(str(date))
    except ValueError:
        return None
    for k in range(1, max_shift + 1):
        for sign in (1, -1):
            other = (base + timedelta(days=sign * k)).isoformat()
            rv = (remote.get(other) or {}).get(field)
            if rv is None:
                continue
            try:
                if abs(float(rv) - v) <= ROUND_TOL:
                    return sign * k
            except (TypeError, ValueError):
                continue
    return None


def compare_series(local: dict, remote: dict, fields=("unit_nav", "acc_nav"),
                   round_tol: float = ROUND_TOL, minor_tol: float = MINOR_TOL) -> dict:
    """逐日期比对两条净值序列。

    Args:
        local / remote: `{"YYYY-MM-DD": {"unit_nav": float, "acc_nav": float}}`
        fields: 要比对的字段

    Returns:
        dict: `{n_local, n_remote, n_common, counts, alerts, shifted, verdict}`
          · `counts`  按严重度计数（**分开计数，不揉成"不一致率"**）
          · `alerts`  仅收录非 ok 的条目（含 `shift_days`，能识别日期错位）
          · `verdict` 一句话结论
    """
    local, remote = local or {}, remote or {}
    dates = sorted(set(local) | set(remote))
    counts = {k: 0 for k in (SEV_OK, SEV_ROUNDING, SEV_MINOR, SEV_MAJOR,
                             SEV_MISSING_REMOTE, SEV_MISSING_LOCAL)}
    alerts, n_common = [], 0

    for d in dates:
        lrow, rrow = local.get(d), remote.get(d)
        if lrow is not None and rrow is not None:
            n_common += 1
        for f in fields:
            lv = (lrow or {}).get(f)
            rv = (rrow or {}).get(f)
            if lv is None and rv is None:
                continue
            sev, rel = classify(lv, rv, round_tol, minor_tol)
            counts[sev] += 1
            if sev == SEV_OK:
                continue
            item = {"date": d, "field": f, "local": lv, "remote": rv,
                    "severity": sev, "rel_diff": (round(rel, 6) if rel is not None else None)}
            # 同日对不上 → 试着识别"日期错位"（验收要求）
            if lv is not None:
                sh = detect_shift(local, remote, d, f, local_v=lv)
                if sh:
                    item["shift_days"] = sh
                    item["hint"] = "疑似日期错位：本地 %s 的值等于远端 %+d 天的值" % (d, sh)
            alerts.append(item)

    n_major = counts[SEV_MAJOR]
    n_missing = counts[SEV_MISSING_REMOTE] + counts[SEV_MISSING_LOCAL]
    n_shifted = sum(1 for a in alerts if a.get("shift_days"))
    if n_major == 0 and n_missing == 0 and n_shifted == 0:
        verdict = "一致（含 %d 个舍入差、%d 个轻微差）" % (counts[SEV_ROUNDING], counts[SEV_MINOR])
    else:
        verdict = "发现问题：严重差 %d · 单侧缺失 %d · 疑似日期错位 %d" % (n_major, n_missing, n_shifted)

    return {
        "n_local": len(local), "n_remote": len(remote), "n_common": n_common,
        "counts": counts, "alerts": alerts, "n_shifted": n_shifted, "verdict": verdict,
    }
