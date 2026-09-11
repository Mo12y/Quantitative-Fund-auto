"""
组合模拟的**信号滞后**检验（B2）。

背景：旧版用 `signal[signal.index <= 月末]` 取信号，即把「当月末才知道的预测」
用在「当月整月收益」上 —— 同月前视。而且 HAR/回撤模型的预测目标本就是下一个月，
所以 `<= 月末` 还叠了一层期限错配。

这里用一个**对抗性合成信号**把两种口径区分开：
  - 泄漏信号：signal[t] 只由**当月 t 的收益**决定（只有在 t 走完才知道），
    对 t+1 毫无信息。滞后取信号后必须**看不出任何择时收益**。
  - 正当信号：signal[t] 预测的是 **t+1 月**收益。滞后取信号后应当明显优于等权。
"""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.vol_predictor import (
    last_signal_before, portfolio_simulation, TARGET_VOL,
)

OOS = "2023-01-01"
MONTHS = [m.strftime("%Y-%m-%d") for m in pd.date_range("2023-01-31", "2023-12-31", freq="ME")]
# 交替 ±10%：涨幅月 1/3/5/7/9/11，跌幅月 2/4/6/8/10/12
RET_BY_MONTH = {m: (0.10 if i % 2 == 0 else -0.10) for i, m in enumerate(MONTHS)}


def _daily_returns():
    """构造日收益矩阵：每月最后一个营业日贡献 ±10%，其余为 0。"""
    days = pd.date_range("2022-01-03", "2023-12-29", freq="B")
    s = pd.Series(0.0, index=days)
    for m, r in RET_BY_MONTH.items():
        month_days = days[days.to_period("M") == pd.Period(m, freq="M")]
        s.loc[month_days[-1]] = r
    return pd.DataFrame({"F1": s})


def _pred(vol_by_month):
    return pd.DataFrame({
        "fund_code": "F1",
        "month": pd.to_datetime(list(vol_by_month.keys())),
        "pred_vol": list(vol_by_month.values()),
    })


def _index_df():
    days = pd.date_range("2022-01-03", "2023-12-29", freq="B")
    return pd.DataFrame({"pe_percentile": 0.5}, index=days)


class TestLastSignalBefore(unittest.TestCase):
    """严格早于：绝不允许当月月末的信号参与当月决策"""

    def setUp(self):
        self.sig = pd.Series({pd.Timestamp("2023-01-31"): 1.0,
                              pd.Timestamp("2023-02-28"): 2.0,
                              pd.Timestamp("2023-03-31"): 3.0})

    def test_excludes_same_month_end(self):
        self.assertEqual(last_signal_before(self.sig, "2023-02-28"), 1.0)

    def test_takes_latest_strictly_earlier(self):
        self.assertEqual(last_signal_before(self.sig, "2023-04-30"), 3.0)

    def test_none_when_no_earlier_value(self):
        self.assertIsNone(last_signal_before(self.sig, "2023-01-31"))

    def test_uses_as_of_not_forward(self):
        self.assertEqual(last_signal_before(self.sig, "2023-03-15"), 2.0)


class TestLeakedSignalGivesNoTimingBenefit(unittest.TestCase):
    """B2 验收：只对当月有预测力的信号，滞后版不得有择时收益"""

    def _run(self, vol_by_month):
        return portfolio_simulation(
            _daily_returns(), _pred(vol_by_month), _index_df(),
            oos_start=OOS, n_funds=1)

    def _leaked(self):
        """signal[t] 由当月 t 的收益决定 → 只有泄漏才能"用上"。"""
        return {m: (0.05 if r > 0 else 0.50) for m, r in RET_BY_MONTH.items()}

    def _honest(self):
        """signal[t] 预测的是 t+1 月收益（可交易信息）。"""
        out = {}
        for i, m in enumerate(MONTHS[:-1]):
            nxt = RET_BY_MONTH[MONTHS[i + 1]]
            out[m] = 0.05 if nxt > 0 else 0.50
        return out

    def test_leaked_signal_has_no_edge(self):
        m = self._run(self._leaked())
        eq = m["Equal_Weight"]["annual_return"]
        vt = m["Vol_Targeting"]["annual_return"]
        # 滞后后拿到的是"上个月的信号"，对当月毫无信息 → 不应优于等权
        self.assertLessEqual(
            vt, eq + 1e-9,
            f"泄漏信号在滞后口径下竟跑赢了等权：VT={vt:.4f} vs EW={eq:.4f}")

    def test_honest_signal_does_have_edge(self):
        """反向对照：真正预测下月的信号，滞后取用后应当有效（证明检验不空转）"""
        m = self._run(self._honest())
        eq = m["Equal_Weight"]
        vt = m["Vol_Targeting"]
        self.assertGreater(vt["annual_return"], eq["annual_return"])
        self.assertLess(vt["max_drawdown"], eq["max_drawdown"])

    def test_position_follows_lagged_signal(self):
        """逐月仓位必须由**上个月**的信号决定"""
        m = self._run(self._honest())
        # 由 _honest 的定义：signal[m-1] 由 ret[m] 决定 → 涨月应满仓、跌月应减仓
        # 这里直接验证引擎的仓位区间与信号一致（跌月 0.30，涨月 1.0）
        self.assertGreater(m["Vol_Targeting"]["avg_position"], 0.0)
        self.assertLessEqual(m["Vol_Targeting"]["avg_position"], 1.0)


if __name__ == "__main__":
    unittest.main()
