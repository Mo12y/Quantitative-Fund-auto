"""🚀 个人基金投资决策支持系统 CLI

用法:
    python src/main.py                # 打印本帮助
    python src/main.py <命令>          # 命令后加 --help/-h 查看本帮助

数据准备（首次/日常）:
    init        一键初始化（自检 → 采集列表/估值 → 日历 → 净值 → 详情）
    test        数据源连通自检
    collect     采集全市场基金列表 + 指数估值
    index       更新指数估值(PE/PB)
    nav         采集基金净值历史
    snapshot    全市场当日净值快照（日常增量主路径）
    enrich      补充基金详情（经理/费率等）
    hithink     同花顺 HiThink 连通自检（未接入采集链路）
    calendar    刷新交易日历（T+1 确认 / 定投跳过节假日）
    fees        补采真·运作费率（管理费/托管费/销售服务费；可断点续采）

行为审计（只读；回答「我自己的操作值多少」）:
    behavior        行为画像(追高 / 割肉 / 卖飞 / 交易频率 ↔ 收益)
    counterfactual  反事实归因(当时不动 / 只定投 / 照温度动)
    sell_rules      同标的卖出规则对比(冻结买入，只变卖出规则)
    breakeven       回本门槛(这笔操作要涨多少才不亏)
    drift           风格漂移检测(CUSUM-of-squares + PELT；名称与类型不变、风险特征变了)

持仓 / 计划:
    portfolio   持仓盈亏与资产配置
    buy         录入买入
    sell        卖出
    update      修改持仓(金额/日期)
    delete      删除持仓(重复录入等)
    dca         定投管理(list/add/run/pause/resume)
    plan        投资计划 + 进度

看板 / 自动化:
    web         启动 Web 界面(http://localhost:5020；端口可用 QFA_PORT=5021 或 `web 5021` 覆盖)
    precompute  预计算快照(聚合总览)，让 Web 首屏免冷算
    schedule    定时调度(每周日 20:00)

⚠️ 2026-10-16 T2.2/T2.3：旧命题的一批命令已随模块归档删除（清单见
`docs/05-重构/01_决策支持系统_执行计划书.md` §10 与 git 历史）。
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
        # 无参数：打印帮助并正常退出。
        # 2026-10-16 T2.3：原为"默认生成周报"（COMMANDS["report"]()）—— report 命令已随
        # 旧命题删除，无参数曾抛 KeyError；现改为打印帮助（退出码 0）。
        print_help()
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
