"""A2 测试：TER（总运作费率）计算与"组内分位"判定。

口径依据（见 docs/参照系接入执行报告 §8.5）：
- **TER = 管理费 + 托管费 + 销售服务费**（晨星 / 美国 SEC 的 Total Expense Ratio）；
- Morningstar 2016/2025：费率是**预测后续业绩最强的单变量**，但必须**同类内比较**；
- 申购费是**交易费用**（受平台折扣影响），**不属运作费用、不得混进 TER**。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QFA_MARKET_LIVE", "0")

from src.analysis import fund_fee
from src.analysis import peer_percentile as pp
from src.analysis.fund_scorer import FundScreener


class TestComputeTer:
    def test_three_components_summed(self):
        ter, missing = fund_fee.compute_ter(
            {"mgt_fee": 1.20, "custodian_fee": 0.20, "sales_service_fee": 0.60})
        assert ter == pytest.approx(2.00) and missing == []

    def test_sales_service_absent_counts_as_zero(self):
        """A 类不收销售服务费（源里是 '---'）→ 计 0，**不列为缺失**。"""
        ter, missing = fund_fee.compute_ter(
            {"mgt_fee": 1.20, "custodian_fee": 0.20, "sales_service_fee": None})
        assert ter == pytest.approx(1.40) and missing == []

    def test_missing_custodian_makes_ter_uncomputable(self):
        """托管费是必收项 → 缺它 TER 不可算（**不得当 0**，否则系统性算低）。"""
        ter, missing = fund_fee.compute_ter({"mgt_fee": 1.20, "custodian_fee": 0})
        assert ter is None and missing == ["custodian_fee"]

    def test_missing_both(self):
        ter, missing = fund_fee.compute_ter({})
        assert ter is None and set(missing) == {"mgt_fee", "custodian_fee"}

    def test_ter_text_explains_missing(self):
        assert "托管费" in fund_fee.ter_text({"mgt_fee": 1.2, "custodian_fee": None})
        assert "TER 1.40%" in fund_fee.ter_text(
            {"mgt_fee": 1.2, "custodian_fee": 0.2, "sales_service_fee": None})


class TestFeeDirectionAndScoring:
    def test_ter_is_lower_better(self):
        assert pp.DIRECTION["ter"] is False, "TER 越小越好（低费率→更好的后续表现）"

    def test_purchase_fee_is_not_in_ter_fields(self):
        assert "purchase_fee" not in fund_fee.TER_FIELDS, \
            "申购费是交易费用，不得进入 TER"

    def test_check_fee_declares_when_uncomputable(self):
        s = FundScreener.__new__(FundScreener)          # 不连库
        s._peer_cache, s._peer_loaded = None, True
        lv, text, warn, ter = s._check_fee({"mgt_fee": 1.2, "custodian_fee": None})
        assert lv == "unknown" and ter is None
        assert "托管费" in text

    def test_check_fee_declares_when_reference_not_ready(self):
        """TER 可算但参照系未就绪 → 声明，不凭绝对值硬判。"""
        s = FundScreener.__new__(FundScreener)
        s._peer_cache, s._peer_loaded = None, True      # load_cache() 返回 None
        lv, text, warn, ter = s._check_fee(
            {"fund_type": "混合型-偏股", "mgt_fee": 1.2, "custodian_fee": 0.2})
        assert lv == "unknown" and ter == pytest.approx(1.4)
        assert "未就绪" in text

    def test_cheap_fund_passes_expensive_warns(self):
        """组内分位生效：同组内 最便宜 → pass；最贵 10% → warn。"""
        cache = {"version": pp.CACHE_VERSION,
                 "groups": {"A 偏股混合": {"n": 3000, "quality_level": "full",
                                           "grid": {"ter": {"p10": 0.5, "p50": 1.2, "p75": 1.6,
                                                            "p90": 2.0, "p95": 2.4}}}}}
        s = FundScreener.__new__(FundScreener)
        s._peer_cache, s._peer_loaded = cache, True
        base = {"fund_type": "混合型-偏股", "custodian_fee": 0.2}
        lv_cheap, txt_c, _, _ = s._check_fee({**base, "mgt_fee": 0.35})   # TER 0.55
        lv_expensive, txt_e, w_e, _ = s._check_fee({**base, "mgt_fee": 2.6})  # TER 2.8
        assert lv_cheap == "pass" and "最便宜" in txt_c
        assert lv_expensive == "warn" and w_e, "同组最贵的 10% 应给费率警告"
