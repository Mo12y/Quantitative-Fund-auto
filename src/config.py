"""
配置中心：加载 config/settings.yaml，合并默认值，提供类型化访问。

用法:
    from src.config import get_config

    cfg = get_config()
    cfg.get("backtest", "temp_threshold", 15)      # 取单个值
    cfg.section("backtest")                        # 取整个 section
    cfg.get("data", "db_path")                     # 数据配置
"""

import copy
import os
import sys
from typing import Any, Dict, Optional

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CONFIG_PATH = os.path.join(_ROOT, "config", "settings.yaml")

# 默认配置：settings.yaml 缺省时的兜底（新增配置段时在此补默认值）
_DEFAULTS: Dict[str, Any] = {
    "data": {
        "db_path": "data/fund_quant.db",
        "lookback_years": 3,
        "collect_on_startup": True,
    },
    "backtest": {
        "lookback_years": 5,
        "selection_weights": [0.4, 0.3, 0.3],
        "adaptive_cold_weights": [0.2, 0.4, 0.4],
        "adaptive_hot_weights": [0.6, 0.2, 0.2],
        "temp_threshold": 15,
        "top_n": 3,
    },
}


class Config:
    """配置中心：加载 settings.yaml 并合并默认值，提供类型化访问。"""

    def __init__(self, path: Optional[str] = None):
        self._data = copy.deepcopy(_DEFAULTS)
        self._load_user_config(path or _CONFIG_PATH)

    def _load_user_config(self, path: str) -> None:
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                user = yaml.safe_load(f) or {}
            self._merge(self._data, user)
        except Exception as e:
            # 配置文件损坏时回退默认值（但仍提示，避免用户误以为自定义配置生效）
            print(f"⚠️ 配置文件解析失败，已回退默认配置: {path}: {e}", file=sys.stderr)

    @staticmethod
    def _merge(base: Dict, override: Dict) -> None:
        """递归合并：用户配置覆盖默认值（嵌套 dict 深合并）"""
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                Config._merge(base[key], value)
            else:
                base[key] = value

    def section(self, name: str) -> Dict:
        """取整个配置段（dict）"""
        return self._data.get(name, {})

    def get(self, section: str, key: str, default: Any = None) -> Any:
        """取配置段内单个值"""
        return self._data.get(section, {}).get(key, default)

    def get_db_path(self) -> str:
        """数据库路径：相对路径基于项目根目录解析，避免随 CWD 漂移"""
        p = self.get("data", "db_path", "data/fund_quant.db")
        if not os.path.isabs(p):
            return os.path.normpath(os.path.join(_ROOT, p))
        return p


# 模块级单例：所有模块共用同一份配置，避免重复加载
_config_instance: Optional[Config] = None


def get_config() -> Config:
    """获取全局配置单例"""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance
