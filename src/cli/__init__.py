"""
CLI 命令注册表：main.py 的分发入口。
"""
from src.cli.data_cmds import (
    cmd_test,
    cmd_init,
    cmd_index,
    cmd_collect,
    cmd_nav,
    cmd_enrich,
    cmd_hithink,
)
from src.cli.analysis_cmds import (
    cmd_score,
    cmd_temp,
    cmd_sentiment,
    cmd_portfolio,
    cmd_rebalance,
    cmd_sector,
    cmd_recommend,
    cmd_plan,
)
from src.cli.backtest_cmds import (
    cmd_backtest,
    cmd_backtest2,
    cmd_backtest3,
    cmd_backtest4,
    cmd_strategy,
)
from src.cli.output_cmds import (
    cmd_report,
    cmd_buy,
    cmd_sell,
    cmd_update,
    cmd_delete,
    cmd_web,
    cmd_schedule,
)

COMMANDS = {
    "test": cmd_test,
    "init": cmd_init,
    "index": cmd_index,
    "collect": cmd_collect,
    "nav": cmd_nav,
    "enrich": cmd_enrich,
    "hithink": cmd_hithink,
    "score": cmd_score,
    "temp": cmd_temp,
    "sentiment": cmd_sentiment,
    "portfolio": cmd_portfolio,
    "rebalance": cmd_rebalance,
    "sector": cmd_sector,
    "recommend": cmd_recommend,
    "plan": cmd_plan,
    "backtest": cmd_backtest,
    "backtest2": cmd_backtest2,
    "backtest3": cmd_backtest3,
    "backtest4": cmd_backtest4,
    "strategy": cmd_strategy,
    "report": cmd_report,
    "buy": cmd_buy,
    "sell": cmd_sell,
    "update": cmd_update,
    "delete": cmd_delete,
    "web": cmd_web,
    "schedule": cmd_schedule,
}
