"""A2 正名测试：**手续费（申购费）≠ 管理费**。

背景（2026-09-22 实测）：
- `fund_open_fund_daily_em` 宽表的最后一列按源码注释是**手续费**（申购费，打折后）；
- 历史实现把它写进了 `fund_info.mgt_fee` → 产生"偏股混合管理费中位数 0.15%"的假分布
  （真相：0.15 = 1.5% 申购费打 1 折）；
- 对照实测（akshare `fund_fee_em`）：000021 华夏优势增长混合 真管理费 **1.20%**、
  托管费 0.20%；而 007029 易方达中证500联接C 的真管理费恰好也是 0.15%（指数基金）。

本文件钉住：① 采集侧不再把手续费写进 mgt_fee；② 真费率的解析；③ 缺数据时**不写 0 冒充**。
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QFA_MARKET_LIVE", "0")

from src.data.collector import DataCollector
from src.data.database import Database


@pytest.fixture()
def col(tmp_path):
    db = Database(str(tmp_path / "fee.db"))
    yield DataCollector(db), db
    db.close()


class TestSnapshotParserNaming:
    def test_has_purchase_fee_not_mgt_fee(self):
        from src.data.collector import parse_daily_snapshot
        cols = ["基金代码", "基金简称"] + [f"{d}日增长率" for d in ("2026-09-17", "2026-09-18")]
        cols += ["2026-09-17单位净值", "2026-09-18单位净值",
                 "2026-09-17累计净值", "2026-09-18累计净值",
                 "申购状态", "赎回状态", "手续费"]
        df = pd.DataFrame([["000021", "华夏优势增长混合"] + [0.0] * 2
                           + [1.0, 1.0, 1.0, 1.0, "开放申购", "开放赎回", "0.15%"]], columns=cols)
        _, info, _ = parse_daily_snapshot(df)
        row = info[0]
        assert "mgt_fee" not in row, "手续费不得再写进 mgt_fee（那是管理费的位置）"
        assert row["purchase_fee"] == pytest.approx(0.15)


class TestCollectFundFee:
    def _fake(self, monkeypatch, row):
        import akshare as ak
        monkeypatch.setattr(ak, "fund_fee_em", lambda symbol, indicator: pd.DataFrame([row]))

    def test_parses_three_rates(self, col, monkeypatch):
        c, _ = col
        self._fake(monkeypatch, ["管理费率", "1.20%（每年）", "托管费率", "0.20%（每年）",
                                 "销售服务费率", "0.60%（每年）"])
        r = c.collect_fund_fee("016371")
        assert r["mgt_fee"] == pytest.approx(1.20)
        assert r["custodian_fee"] == pytest.approx(0.20)
        assert r["sales_service_fee"] == pytest.approx(0.60)

    def test_dash_placeholder_is_none_not_zero(self, col, monkeypatch):
        """'---'（无销售服务费，如 A 类）→ None；**不得写 0** 冒充。"""
        c, _ = col
        self._fake(monkeypatch, ["管理费率", "1.20%（每年）", "托管费率", "0.20%（每年）",
                                 "销售服务费率", "---"])
        r = c.collect_fund_fee("000021")
        assert r["mgt_fee"] == pytest.approx(1.20)
        assert r["sales_service_fee"] is None

    def test_all_missing_returns_none(self, col, monkeypatch):
        c, _ = col
        self._fake(monkeypatch, ["其他", "---"])
        assert c.collect_fund_fee("000000") is None

    def test_network_error_returns_none(self, col, monkeypatch):
        c, _ = col
        import akshare as ak
        def boom(**k):
            raise RuntimeError("network down")
        monkeypatch.setattr(ak, "fund_fee_em", boom)
        assert c.collect_fund_fee("000021") is None


class TestFeeCheckDoesNotUsePurchaseFee:
    def test_screener_fee_check_reads_mgt_fee_only(self):
        """质量筛选的费率检查只认 `mgt_fee`（管理费），不得拿申购费顶替。"""
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src/analysis/fund_scorer.py"), encoding="utf-8").read()
        i = src.find("def _check_fee")
        seg = src[i:i + 2600]
        # 走 TER 单一实现（管理费+托管费+销售服务费）
        assert "fund_fee.compute_ter" in seg, "费率检查必须走 TER 单一实现"
        # 且**不得读取** purchase_fee（申购费受平台折扣影响、是交易费用，不属运作费用）
        # 注意：docstring 里提到 purchase_fee 是**说明历史坑**，不算违规；这里查的是实际取值
        for bad in ('get("purchase_fee")', "get('purchase_fee')", '["purchase_fee"]'):
            assert bad not in seg, "申购费不得参与 TER 计算：%s" % bad
