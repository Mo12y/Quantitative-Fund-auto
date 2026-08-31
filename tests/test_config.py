"""
配置中心单元测试。

运行方式:
    python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config


class TestConfigDefaults(unittest.TestCase):
    """配置文件缺失时回退到默认值"""

    def test_missing_file_uses_defaults(self):
        cfg = Config(path="/nonexistent/settings.yaml")
        self.assertEqual(cfg.get("backtest", "temp_threshold"), 15)
        self.assertEqual(cfg.get("backtest", "selection_weights"), [0.4, 0.3, 0.3])
        self.assertEqual(cfg.get("data", "db_path"), "data/fund_quant.db")

    def test_unknown_key_returns_default(self):
        cfg = Config(path="/nonexistent/settings.yaml")
        self.assertIsNone(cfg.get("backtest", "no_such_key"))
        self.assertEqual(cfg.get("backtest", "no_such_key", 42), 42)


class TestConfigMerge(unittest.TestCase):
    """用户配置覆盖默认值，未覆盖的保留默认"""

    def _write_tmp_yaml(self, content: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_user_overrides_and_defaults_preserved(self):
        path = self._write_tmp_yaml("backtest:\n  temp_threshold: 25\n")
        try:
            cfg = Config(path=path)
            self.assertEqual(cfg.get("backtest", "temp_threshold"), 25)  # 用户覆盖
            self.assertEqual(cfg.get("backtest", "top_n"), 3)            # 默认保留
        finally:
            os.unlink(path)

    def test_nested_section_merge(self):
        path = self._write_tmp_yaml("data:\n  db_path: 'custom.db'\n")
        try:
            cfg = Config(path=path)
            self.assertEqual(cfg.get("data", "db_path"), "custom.db")
            self.assertEqual(cfg.get("data", "lookback_years"), 3)  # 未覆盖键保留默认
        finally:
            os.unlink(path)

    def test_broken_yaml_falls_back(self):
        path = self._write_tmp_yaml("backtest: [broken\n  :::")
        try:
            cfg = Config(path=path)
            self.assertEqual(cfg.get("backtest", "temp_threshold"), 15)  # 回退默认
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
