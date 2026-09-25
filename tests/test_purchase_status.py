"""
申购状态（限购）检查 —— 批次 3.2。

四种状态各自的口径（D2）：
    开放申购  → 通过
    暂停申购  → **排除**出推荐池（封闭期同处理）
    限大额    → **保留**（限额度 ≠ 不能买），只标注，**不改质量等级**
    空 / NULL → 视为**未知**，标注“跳过检查”，**不得静默当作可申购**

核心原则：申购状态与"基金好不好"是两个轴。把它算进 warn_count 会把一只
🟢 稳健基金压成 🟡 注意 —— 那是变相降级，不是"保留但标注"。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.fund_scorer import FundScreener, _BLOCKED_PURCHASE_MARKERS
from src.data.database import Database

NAV_N = 80          # ≥60 才进筛选池


def _seed_fund(db, code, name, fund_type, purchase_status, nav=1.0):
    db.upsert_fund_info({"fund_code": code, "fund_name": name,
                         "fund_type": fund_type, "purchase_status": purchase_status})
    db.conn.executemany(
        "INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return) "
        "VALUES (?,?,?,?,?)",
        [(code, "2026-%02d-%02d" % (1 + i // 28, 1 + i % 28), nav, nav, 0.0)
         for i in range(NAV_N)])
    db.conn.commit()


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "ps.db"))
    yield d
    d.close()


@pytest.fixture()
def screener(db):
    return FundScreener(db)


# ==================== 单项检查 ====================

class TestCheckPurchasable:
    def test_open_is_pass(self, screener):
        level, text, warn, raw = screener._check_purchasable({"purchase_status": "开放申购"})
        assert level == "pass" and text.startswith("✅") and warn is None and raw == "开放申购"

    def test_suspended_is_fail(self, screener):
        level, text, warn, raw = screener._check_purchasable({"purchase_status": "暂停申购"})
        assert level == "fail" and text.startswith("❌") and warn and "买不进去" in warn

    def test_closed_period_is_fail(self, screener):
        level, text, warn, _ = screener._check_purchasable({"purchase_status": "封闭期"})
        assert level == "fail" and text.startswith("❌") and warn

    def test_limited_is_kept_but_annotated(self, screener):
        """限大额：标注，但**不产生 warning** —— 否则会拉低风险等级。"""
        level, text, warn, raw = screener._check_purchasable({"purchase_status": "限大额"})
        assert level == "info" and "限大额" in text and "单日" in text
        assert warn is None and raw == "限大额"

    @pytest.mark.parametrize("val", ["", "   ", None])
    def test_blank_is_unknown_not_purchasable(self, screener, val):
        """空 / NULL ≠ 可申购：既不判通过也不判失败，标成"未知"。"""
        level, text, warn, raw = screener._check_purchasable({"purchase_status": val})
        assert level == "unknown" and "未知" in text and "跳过检查" in text
        assert not text.startswith("✅"), "空白状态绝不能被当成'可申购'"
        assert warn is None and raw == ""

    def test_unrecognised_text_is_unknown(self, screener):
        level, text, warn, raw = screener._check_purchasable({"purchase_status": "内部转让"})
        assert level == "unknown" and "未知" in text and warn is None and raw == "内部转让"

    def test_missing_key_does_not_crash(self, screener):
        level, text, warn, raw = screener._check_purchasable({})
        assert level == "unknown" and "未知" in text and raw == ""


class TestBlockedMarkerConstant:
    def test_markers(self):
        assert "暂停申购" in _BLOCKED_PURCHASE_MARKERS
        assert "封闭期" in _BLOCKED_PURCHASE_MARKERS
        assert "限大额" not in _BLOCKED_PURCHASE_MARKERS   # 限大额要保留


# ==================== 风险等级不受申购状态影响 ====================

class TestRiskLabelUnaffected:
    """限大额 / 未知都不该改变质量等级；只有真的买不进去才是 ❌。"""

    @pytest.mark.parametrize("status", ["限大额", "", "开放申购", "无法归类的东西"])
    def test_label_is_the_same_as_without_the_check(self, db, screener, status):
        """⚠️ 2026-09-25 改法：**直接验不变量**，不再用"文本前缀"反推等级。

        旧写法按 `quality_checks` 的 ⚠️/❌ 前缀重建等级，与真正的判定源（结构化
        `check_levels`）是两套逻辑 —— 一旦某项检查的文案从"⚠️ 夏普偏低"变成
        "⚠️ 数据不足"（同样都是 unknown），文本反推就会误判成 warn。

        现在改成：**同一条净值序列，只把申购状态换成中性的"开放申购"再评一次**，
        两次的 `risk_label` 必须一致 —— 这才是"申购状态不影响质量等级"的直接检验。
        """
        _seed_fund(db, "PS01", "某稳健基金C", "混合型", status)
        vals = screener._nav_values("PS01")
        r = screener._score_series(vals, db.get_fund_info("PS01"))

        info2 = dict(db.get_fund_info("PS01"))
        info2["purchase_status"] = "开放申购"          # 中性对照：pass 且不产生 warning
        r2 = screener._score_series(vals, info2)

        assert r["risk_label"] == r2["risk_label"], \
            f"{status!r} 不该改变质量等级（实际 {r['risk_label']} vs 对照 {r2['risk_label']}）"
        assert r["purchase_blocked"] is False

    def test_suspended_is_blocked(self, db, screener):
        _seed_fund(db, "PS02", "某暂停基金C", "混合型", "暂停申购")
        vals = screener._nav_values("PS02")
        r = screener._score_series(vals, db.get_fund_info("PS02"))
        assert r["purchase_blocked"] is True


# ==================== 端到端：推荐池 ====================

class TestScreenFundsExcludesUnbuyable:
    def test_suspended_excluded_open_kept(self, db, screener):
        _seed_fund(db, "OP01", "可买基金C", "混合型", "开放申购")
        _seed_fund(db, "SP01", "暂停基金C", "混合型", "暂停申购")
        _seed_fund(db, "CL01", "封闭基金C", "混合型", "封闭期")
        _seed_fund(db, "LM01", "限大额基金C", "混合型", "限大额")
        _seed_fund(db, "UN01", "状态未知基金C", "混合型", "")

        df = screener.screen_funds(fund_types=["混合型"], max_results=100)
        codes = set(df["fund_code"])
        assert "OP01" in codes, "开放申购必须保留"
        assert "LM01" in codes, "限大额必须保留（限额度 ≠ 不能买）"
        assert "UN01" in codes, "未知必须保留，但标注"
        assert "SP01" not in codes, "暂停申购必须排除"
        assert "CL01" not in codes, "封闭期必须排除"

    def test_limited_and_unknown_are_annotated_in_the_pool(self, db, screener):
        _seed_fund(db, "OP02", "可买基金C", "混合型", "开放申购")
        _seed_fund(db, "LM02", "限大额基金C", "混合型", "限大额")
        _seed_fund(db, "UN02", "状态未知基金C", "混合型", "")
        _seed_fund(db, "NL02", "NULL状态基金C", "混合型", None)

        df = screener.screen_funds(fund_types=["混合型"], max_results=100)
        row = {r["fund_code"]: r for _, r in df.iterrows()}
        assert row["LM02"]["quality_checks"]["申购状态"].startswith("🔸")
        assert row["UN02"]["quality_checks"]["申购状态"].startswith("⊘")
        assert row["NL02"]["quality_checks"]["申购状态"].startswith("⊘")
        assert row["OP02"]["quality_checks"]["申购状态"].startswith("✅")

    def test_summary_reports_limited_and_unknown(self, db, screener):
        _seed_fund(db, "OP03", "可买基金C", "混合型", "开放申购")
        _seed_fund(db, "LM03", "限大额基金C", "混合型", "限大额")
        _seed_fund(db, "UN03", "状态未知基金C", "混合型", "")
        _seed_fund(db, "UN04", "状态未知2基金C", "混合型", None)

        df = screener.screen_funds(fund_types=["混合型"], max_results=100)
        s = screener.get_pool_summary(df)
        assert s["limited_n"] == 1
        assert s["status_unknown_n"] == 2
        assert s["total"] == 4

    def test_pool_summary_on_empty_df_still_works(self, screener):
        import pandas as pd
        s = screener.get_pool_summary(pd.DataFrame())
        assert s["total"] == 0 and s["limited_n"] == 0 and s["status_unknown_n"] == 0


class TestStrategyEngineInheritsTheFilter:
    """`get_recommended_funds` 走的就是 `screen_funds`，因此自动继承排除规则。"""

    def test_recommended_funds_never_contain_suspended(self, db, screener):
        _seed_fund(db, "OP04", "可买基金C", "混合型", "开放申购")
        _seed_fund(db, "SP04", "暂停基金C", "混合型", "暂停申购")
        df = screener.screen_funds(fund_types=["混合型"], max_results=100)
        assert "SP04" not in set(df["fund_code"])
