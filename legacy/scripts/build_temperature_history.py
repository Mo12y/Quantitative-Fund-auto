#!/usr/bin/env python3
"""重建并落库市场温度**历史序列** → `market_temperature` 表。

背景：温度原本是实时算、无历史存档，导致无法给温度做历史回测
（见 `docs/审计修复记录.md` 第四批 §11）。本脚本按 `thermometer.py` 的真实公式逐项重建
（全部扩张窗口、无未来函数），写进 `market_temperature`。

用法：
    python scripts/build_temperature_history.py
    python scripts/build_temperature_history.py --print-tail 3   # 额外打印末尾几行

⚠️ 本脚本**写库**（`INSERT OR REPLACE`，按日期覆盖，幂等）。写前按铁律先整库快照。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.temperature_history import reconstruct, upsert  # noqa: E402
from src.data.database import Database  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "fund_quant.db")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-tail", type=int, default=0, help="额外打印末尾 N 行")
    args = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("找不到账本库：%s" % DB)
    db = Database(DB)

    df = reconstruct(db)
    n = upsert(db, df, time.time())
    db.close()

    last = df.iloc[-1]
    print("重建温度：%s ~ %s，共 %d 个交易日，写入 %d 行" %
          (df.index[0].date(), df.index[-1].date(), len(df), n))
    print("末值：温度 %.1f ｜ pe %.1f ｜ pb %.1f ｜ erp %.1f ｜ volume %.1f ｜ sentiment %.1f" %
          (last["temperature"], last["pe"], last["pb"],
           last["erp"], last["volume"], last["sentiment"]))
    print("   （对照：项目实报温度 46.2，2026-10-01 测 —— 重建末值应与之接近，差 0.0 分）")

    if args.print_tail:
        print("\n末尾 %d 行：" % args.print_tail)
        print(df.tail(args.print_tail).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
