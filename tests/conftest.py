# -*- coding: utf-8 -*-
"""测试环境统一约定。

`Database.__init__` 默认**拒绝**在路径不存在时建库（防"路径写错静默建空库"的系统性缺陷）。
但测试里"建临时库"是常态（约 70 处 `Database(tmp_path / "x.db")`）—— 与其逐个加
`allow_create=True`（机械、易错、diff 巨大），不如在这里把测试环境的默认放开：

- 路径守卫的**正向 / 负向行为**在 `tests/test_ledger_write.py` 里用**显式参数**测，
  不依赖这里的默认值；
- 这里只影响"测试没显式传 allow_create 的那些建库调用"，让它们能正常建库。

⚠️ `Database` 是在 `__init__` **运行时**读 `QFA_DB_ALLOW_CREATE`（不是 import 时），
所以本文件在收集阶段设置环境变量即可对后续所有 Database 实例化生效。
"""
import os

os.environ.setdefault("QFA_DB_ALLOW_CREATE", "1")
