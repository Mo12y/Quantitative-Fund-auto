"""用户画像（B 层的数据源，设计稿 §4.4）—— 本地配置文件读取。

职责边界
--------
本模块**只做两件事**：读本地画像文件、声明来源与状态（缺文件/解析失败都如实说明）。
把画像翻译成约束是 `user_constraint.build_constraints_from_user` 的职责，
本模块**不做任何语义映射**（不猜"稳健"= 什么阈值）。

为什么用本地文件而不是数据库
----------------------------
画像属个人数据（风险偏好/组别偏好），不该进公开仓库；沿用 F-01 的模式：
模板 `config/user_profile.local.example.yaml`（可入库）+ 真实文件
`config/user_profile.local.yaml`（已 gitignore）。将来 C 层上线可换成账号体系，
届时只需替换本模块的读取实现，`user_constraint` 的契约不变。
"""
from __future__ import annotations

import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROFILE_PATH = os.path.join(ROOT_DIR, "config", "user_profile.local.yaml")
EXAMPLE_PATH = os.path.join(ROOT_DIR, "config", "user_profile.local.example.yaml")


def load_profile(path: str = None) -> tuple:
    """读本地画像配置 → `(profile_dict, note)`。

    note **显式声明来源与状态**（供 §4.6 可解释性直接展示，不静默）：
      - 未配置      → `"无本地画像配置（config/user_profile.local.yaml 不存在）"`
      - 配置正常    → `"本地画像：user_profile.local.yaml"`
      - 解析失败    → `"本地画像解析失败：<err> → 按未配置处理"`（**不编造默认画像**）

    空文件/非 dict（如写成列表）→ 语义同"未配置"，同样在 note 里说明。
    """
    p = path or PROFILE_PATH
    name = os.path.basename(p)
    if not os.path.exists(p):
        return {}, "无本地画像配置（config/%s 不存在）" % name
    try:
        import yaml
        with open(p, encoding="utf-8") as f:
            d = yaml.safe_load(f)
    except Exception as e:
        return {}, "本地画像解析失败：%s → 按未配置处理" % str(e)[:80]
    if not isinstance(d, dict):
        return {}, "本地画像内容不是键值表（%s）→ 按未配置处理" % name
    return dict(d), "本地画像：%s" % name


#: 温度→权益仓位**枢轴**的默认值 `(长期中性目标 %, 温度调节 ±pp)` —— **单一来源**：
#: `thermometer` 的类常量从这里 import（改一处即可）。
#: 依据见 docs/审计修复记录.md 第四批 §10-§11：20 年真实温度回测下"按温度降暴露"在风险调整后
#: 减分（年化 −2.45pp、夏普 0.16 vs 恒定 60% 的 0.26），平均权益只有 40%
#: （AQR："50 多年只收了 89% 的风险溢价"）→ 改为围绕**明示的中性目标**摆动。
DEFAULT_EQUITY_PIVOT = (60.0, 25.0)


def load_equity_pivot(path: str = None) -> tuple:
    """读画像里的**权益枢轴**声明 → `((中性 %, ±pp), note)`。

    键全部可选（只写一个 = 另一个用默认；note 会说明来源）：
      - `equity_neutral_pct`：长期中性权益目标（0-100，默认 60）
      - `equity_tilt_pp`    ：温度调节幅度（0-50，默认 25）

    非法值 → 该项用默认并在 note 里声明（不猜、不静默；与 `target_equity_pct` 同一纪律）。
    """
    prof, _ = load_profile(path)
    neutral, tilt = DEFAULT_EQUITY_PIVOT
    notes, overridden = [], False
    for key, lo, hi in (("equity_neutral_pct", 0.0, 100.0), ("equity_tilt_pp", 0.0, 50.0)):
        raw = prof.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            notes.append("%s=%r 不是数字 → 用默认" % (key, raw))
            continue
        if not (lo <= v <= hi):
            notes.append("%s=%s 超出 [%.0f,%.0f] → 用默认" % (key, raw, lo, hi))
            continue
        if key == "equity_neutral_pct":
            neutral = v
        else:
            tilt = v
        overridden = True
    note = "权益枢轴：中性 %.0f%% ±%.0fpp（%s）" % (neutral, tilt, "画像声明" if overridden else "默认")
    if notes:
        note += "；" + "；".join(notes)
    return (neutral, tilt), note