"""🚀 量化基金系统 v3.0

用法:
    python src/main.py                          # 默认生成周报
    python src/main.py <命令> [--help]           # 命令后加 --help/-h 查看总帮助

数据准备（首次/日常）:
    init        一键初始化
    test        数据源连通自检
    collect     采集全市场基金列表 + 指数估值
    index       更新指数估值(PE/PB)
    nav         采集候选基金净值历史（评分需要）
    snapshot    全市场当日净值快照（1 次请求约 10 秒，日常增量主路径）
    enrich      补充基金详情（经理/费率等）
    hithink     同花顺 HiThink 连通测试
    calendar    刷新交易日历（T+1 确认 / 定投跳过节假日）

每日决策:
    temp        市场温度 + 仓位建议
    score       基金质量筛选(🟢🟡🔴 排除有坑的)
    sector      31 个行业板块排名 + 推荐
    sentiment   消息面监控(宏观 LPR/PMI / 基金公告)
    recommend   历史验证基金推荐(回测)
    strategy    策略引擎(温度阈值/是否调仓)
    backtest3   严谨回测 v3(防未来函数/多基准/显著性)
    backtest4   严谨回测 v4
    backtest / backtest2   旧版回测(兼容, 已被 v3/v4 取代)

持仓 / 组合管理:
    portfolio   持仓盈亏与资产配置
    buy         录入买入
    sell        卖出
    update      修改持仓(金额/日期)
    delete      删除持仓(重复录入等)
    rebalance   持仓调仓建议
    dca         定投管理(list/add/run/pause/resume)
    plan        投资计划 + 进度
    report      周度报告

看板 / 自动化:
    web         启动 Web 仪表盘(http://localhost:5020；端口可用 QFA_PORT=5021 或 `web 5021` 覆盖)
    precompute  预计算快照(温度/筛选池/板块总榜)，让 Web 首屏免冷算
    schedule    定时调度(每周日 20:00 自动生成周报)
"""

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
        # 子命令带 --help/-h 时输出总帮助，避免误进交互式流程(如 buy --help)
        if any(a in ("-h", "--help", "help") for a in sys.argv[2:]):
            print_help()
            return
        COMMANDS[cmd]()
    else:
        print(f"❌ 未知命令: {cmd}")
        print_help()


if __name__ == "__main__":
    main()
