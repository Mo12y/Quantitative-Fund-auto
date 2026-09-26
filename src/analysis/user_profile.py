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