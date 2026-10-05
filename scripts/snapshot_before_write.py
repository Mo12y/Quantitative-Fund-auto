# -*- coding: utf-8 -*-
"""写库前整库快照 —— 铁律·写库先快照。

用法：
    python scripts/snapshot_before_write.py

做什么：
    用 SQLite 的 backup API 把 `data/fund_quant.db` 完整复制成
    `data/_snapshot_<时间戳>_pre_write.db`，并**在 stdout 打印回滚命令原文**。

为什么用 backup API 而不是 `shutil.copy`：
    WAL 模式下，`-wal` 里可能还有**未 checkpoint 的已提交数据**，光复制 `.db` 文件会丢
    这些数据（复制的是一份"滞后"的快照）。`sqlite3.Connection.backup()` 会走 SQLite 自己的
    在线备份协议，把主库 + WAL 一起读进目标库，得到**一致**的快照。

⚠️ 脚本路径一律用绝对路径（铁律·绝对路径）：`Database("data/fund_quant.db")` 这类相对路径
   在项目根外跑会静默新建空库。本脚本从 `scripts/` 定位项目根，不依赖 cwd。
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "fund_quant.db"


def main() -> int:
    if not DB.exists():
        print(f"[FAIL] 账本不存在：{DB}\n      先核对路径 —— 不要在错误路径上建空库。")
        return 1

    ts = time.strftime("%Y%m%d_%H%M%S")
    snap = ROOT / "data" / f"_snapshot_{ts}_pre_write.db"

    src = sqlite3.connect(str(DB))
    dst = sqlite3.connect(str(snap))
    try:
        with dst:                       # dst 侧包事务，backup 整体原子落盘
            src.backup(dst)
    finally:
        src.close()
        dst.close()

    mb = snap.stat().st_size / 1024 / 1024
    print(f"[OK] 已快照：{snap.name}  （{mb:.1f} MB）")
    print()
    print("回滚命令（⚠️ 先停 web 服务，再还原）：")
    print(f'  cp "{snap}" "{DB}"')
    print(f'  # Windows cmd: copy /Y "{snap}" "{DB}"')
    print()
    print("还原后，若服务在跑，重启：python src/main.py web")
    return 0


if __name__ == "__main__":
    sys.exit(main())
