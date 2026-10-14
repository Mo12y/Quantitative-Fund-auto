r"""GARCH / EGARCH 波动率基线（批次 4）。

背景
----
《外部参考整合方案与任务书》§暂不执行·批次 4：「修复 `arch` 并补 GARCH(1,1)/EGARCH 基线」。
`docs/vol_model_risk_report.md` 另指出"学术界 vol 预测的标准基线是 HAR-RV，不是 EWMA" ——
HAR-RV 已在 `vol_model_comparison` 的对比表里（`HAR-RV (Corsi 2004)`），本模块补上**另一族**
标准基线：GARCH 族。

为什么要两个族
--------------
· **HAR-RV** 是**已实现波动率**的回归（日/周/月 RV 做特征）—— 需要日频 RV；
· **GARCH 族**是**参数化条件方差**模型（用日收益平方/残差递推）—— 参数更少、更经典，
  且 EGARCH 能刻画"跌比涨更放大波动"的杠杆效应。
两者回答同一个问题（预测下月 vol），属不同族，报告里应**同时**呈现（只报一个都算选择偏差）。

沙箱安装坑（2026-09-27 实测，值得记）
------------------------------------
`pip install arch` 在本机**装不进 user site**：AppData\Roaming 的写入被沙箱静默拦截 ——
只落下 `arch-8.0.0.dist-info`，`arch/` 包目录**根本不落盘**，`import arch` 必失败
（还会被 pip 误报成 "Requirement already satisfied"）。
可行做法：`pip install --target <可写目录> arch -i https://mirrors.aliyun.com/pypi/simple/`
再把该目录加进 `sys.path`（本项目实测用 `D:\DSH\scratch_hold\_vendor`）。

→ 因此本模块**优雅降级**：`arch` 不可用时返回 `None` + `reason`，
  由上层如实声明「未评估」，**绝不静默跳过**（铁律 5）。
"""
from __future__ import annotations

import os
import sys

# 允许通过环境变量指定 arch 的安装目录（沙箱/离线环境的兜底）
ARCH_PATH_ENV = "QFA_ARCH_PATH"
TRADING_DAYS = 244          # 项目口径，与 nav_metrics 一致


def _ensure_path():
    """把 `QFA_ARCH_PATH` 指到的目录加进 sys.path（若设置了）。"""
    p = os.environ.get(ARCH_PATH_ENV)
    if p and os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)


def available() -> tuple:
    """`(是否可用, 说明)`。不可用时说明原因（不静默）。"""
    _ensure_path()
    try:
        import arch  # noqa: F401
        return True, "arch %s" % getattr(arch, "__version__", "?")
    except ImportError as e:
        return False, ("arch 未安装（%s）。安装：pip install --target <可写目录> arch，"
                       "并设 %s=<该目录>" % (e, ARCH_PATH_ENV))


def fit(returns, vol: str = "Garch", p: int = 1, o: int = 0, q: int = 1,
        dist: str = "normal", horizon: int = 1) -> dict:
    """拟合 GARCH 族并预测下一期年化波动。

    Args:
        returns: 日收益率序列（**小数**，如 0.012 = +1.2%）
        vol:     `"Garch"`（对称）或 `"EGARCH"`（含杠杆效应）
        p/o/q:   GARCH 阶数；`o=1` 即 GJR-GARCH 的不对称项
        horizon: 预测步长（交易日）

    Returns:
        `{ok, model, params, aic, forecast_vol_pct, reason}` ——
        `ok=False` 时 `reason` 说明原因（arch 缺 / 样本不足 / 拟合失败）。
    """
    # ① 先做**与 arch 无关**的输入校验 —— "样本不足"这件事跟安没装 arch 没关系，
    #    放在 arch 检查之后会被"arch 未安装"掩盖，掩盖真实原因。
    try:
        seq = list(returns) if returns is not None else []
    except TypeError:
        seq = []
    vals = []
    for x in seq:
        try:
            vals.append(float(x))
        except (TypeError, ValueError):
            continue
    try:
        import numpy as np
    except ImportError:                                    # pragma: no cover
        return {"ok": False, "model": None, "reason": "numpy 不可用"}
    r = np.asarray(vals, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 100:
        return {"ok": False, "model": None,
                "reason": "样本不足（%d 个日收益 < 100）" % r.size}

    # ② 再检查 arch 是否可用
    ok, why = available()
    if not ok:
        return {"ok": False, "model": None, "reason": why}
    try:
        from arch import arch_model
    except ImportError as e:                              # pragma: no cover
        return {"ok": False, "model": None, "reason": "import 失败：%s" % e}

    name = "%s(%d,%d)" % (vol, p, q) + ("+o" if o else "")
    try:
        # arch 以**百分数**为量纲更稳定（数值尺度），故 ×100 后拟合，最后再换回小数
        am = arch_model(r * 100.0, vol=vol, p=p, o=o, q=q, dist=dist)
        res = am.fit(disp="off")
        # 多步预测：部分模型（EGARCH）**不支持解析多步** → 退回模拟法（固定种子，可复现）
        try:
            f = res.forecast(horizon=horizon, reindex=False)
        except Exception:
            import numpy as _np
            _np.random.seed(0)          # 模拟法有随机性 → 固定种子保证**可复现**
            f = res.forecast(horizon=horizon, reindex=False, method="simulation",
                             simulations=200)
        # ⚠️ 必须取**累计**方差（horizon 步求和），不是只取第 1 步：
        # `variance.values[-1, :h]` 是逐步方差，预测"未来 h 天"要用它们的和。
        steps = f.variance.values[-1, :horizon]
        total_var_pct2 = float(np.sum(steps))
        daily_vol = (max(total_var_pct2, 0.0) / max(horizon, 1)) ** 0.5 / 100.0
        ann_pct = daily_vol * (TRADING_DAYS ** 0.5) * 100.0
    except Exception as e:                                  # noqa: BLE001
        return {"ok": False, "model": name, "reason": "拟合失败：%s" % str(e)[:110]}

    return {"ok": True, "model": name, "reason": None,
            "params": {k: round(float(v), 5) for k, v in res.params.items()},
            "aic": round(float(res.aic), 2), "bic": round(float(res.bic), 2),
            "forecast_vol_pct": round(ann_pct, 3),
            "n_obs": int(r.size)}


def compare(returns, **kw) -> dict:
    """同时拟合**对称 GARCH(1,1)** 与 **EGARCH(1,1)**，返回一组结果（便于并排比较）。

    EGARCH 用 `vol="EGARCH", o=1` —— 标准的杠杆效应设定。
    """
    g = fit(returns, vol="Garch", p=1, q=1, **kw)
    e = fit(returns, vol="EGARCH", p=1, o=1, q=1, **kw)
    out = {"GARCH(1,1)": g, "EGARCH(1,1)": e}
    usable = {k: v for k, v in out.items() if v.get("ok")}
    if len(usable) >= 2:
        # 谁更优：AIC 更小者（同一样本、同一量纲，可直接比）
        best = min(usable, key=lambda k: usable[k]["aic"])
        out["best_by_aic"] = best
        out["note"] = ("AIC 更小者更优（同为 %d 个观测、同一量纲，可直接比）；"
                       "EGARCH 的价值在于能刻画「跌比涨更放大波动」的杠杆效应。"
                       % usable[best]["n_obs"])
    else:
        out["best_by_aic"] = None
        out["note"] = next((v.get("reason") for v in out.values()
                            if isinstance(v, dict) and not v.get("ok")), "不可用")
    return out
