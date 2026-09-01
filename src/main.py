"""🚀 量化基金系统 v3.0

用法:
    python src/main.py              # 每周报告(温度+仓位建议)
    python src/main.py temp         # 市场温度详情
    python src/main.py score        # 基金质量筛选(🟢🟡🔴)
    python src/main.py sector       # 31行业板块排名+推荐
    python src/main.py recommend    # 历史验证基金推荐
    python src/main.py rebalance    # 持仓调仓建议
    python src/main.py sentiment    # 消息面监控
    python src/main.py portfolio    # 持仓盈亏
    python src/main.py buy/sell     # 买入/卖出记录
    python src/main.py update       # 修改持仓（金额/日期）
    python src/main.py delete       # 删除持仓（重复录入等）
    python src/main.py plan         # 投资计划+进度
    python src/main.py web          # 启动Web仪表盘
    python src/main.py schedule     # 定时调度(每周日自动生成周报)
    python src/main.py init           # 一键初始化(首次使用)
    python src/main.py collect/nav/enrich  # 分步数据采集
    python src/main.py backtest3   # 严谨回测v3(防未来函数/多基准/显著性)"""

import sys
import os

# 修复 Windows GBK 编码问题：强制 stdout/stderr 使用 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cli import COMMANDS


def print_help():
    """打印帮助信息"""
    print(__doc__)


def main():
    """主入口: 根据命令行参数分发到不同子命令"""
    if len(sys.argv) < 2:
        # 无参数: 默认生成报告
        COMMANDS["report"]()
        return

    cmd = sys.argv[1].lower()
    if cmd in ("help", "-h", "--help"):
        print_help()
    elif cmd in COMMANDS:
        COMMANDS[cmd]()
    else:
        print(f"❌ 未知命令: {cmd}")
        print_help()


if __name__ == "__main__":
    main()
