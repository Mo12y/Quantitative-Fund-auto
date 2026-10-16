# -*- coding: utf-8 -*-
"""T4.2 验收：`Database` 的相对路径按**项目根**解析（铁律·绝对路径的系统性兜底）。

场景（旧行为会踩的坑）：cwd 不在项目根时构造 `Database("data/fund_quant.db")` ——
旧代码会去 **cwd** 下找：默认拒建时抛错（可接受），而 `allow_create=True` 时更糟 ——
在 cwd 建一个**空库**，表现为"持仓=0 / 最新净值=None"的假故障。
新代码必须解析到**项目根**的库（本机真库恒存在）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.database import Database, _PROJECT_ROOT  # noqa: E402


def test_relative_path_resolves_to_project_root(tmp_path, monkeypatch):
    """cwd 在项目根外时，相对路径仍定位到**项目根**的库（而不是 cwd）。"""
    monkeypatch.chdir(tmp_path)
    db = Database("data/fund_quant.db")          # 相对路径 + 默认拒建（真库在本机存在）
    try:
        assert os.path.abspath(db.db_path) == os.path.join(
            _PROJECT_ROOT, "data", "fund_quant.db")
    finally:
        db.close()


def test_no_empty_db_created_outside_project(tmp_path, monkeypatch):
    """项目根外 + allow_create=True 也**不许**在 cwd 下建空库（回归守卫）。"""
    monkeypatch.chdir(tmp_path)
    db = Database("data/fund_quant.db", allow_create=True)
    db.close()
    assert not (tmp_path / "data").exists(), "cwd 下被建了 data/ —— 相对路径解析回退了？"


def test_absolute_path_untouched(tmp_path):
    """绝对路径原样（仅规范化）——临时库等既有用法不受影响。"""
    p = str(tmp_path / "x.db")
    db = Database(p, allow_create=True)
    try:
        assert os.path.abspath(db.db_path) == os.path.abspath(p)
    finally:
        db.close()


def test_missing_relative_path_still_refuses(tmp_path, monkeypatch):
    """相对路径指向不存在的库 → 仍然**拒绝**（默认不建空库），且不在 cwd 留下任何目录。

    ⚠️ conftest 全局设了 `QFA_DB_ALLOW_CREATE=1`（测试建临时库用）—— 本测试验证的是
    "**默认拒绝**"语义本身，故先删掉该环境变量（同 `test_ledger_write.py` 的先例）。
    """
    monkeypatch.delenv("QFA_DB_ALLOW_CREATE", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        Database("data/__not_exists__.db")
    assert not (tmp_path / "data").exists()
