"""推荐系统批次 4 回归测试：排序/评分/候选池/采样/口径。

对应任务书：
- 4.1 综合评分 + mgt_fee 缺失排最后（不再"数据缺失当零费率排最前"）
- 4.4 类型分组（债基不再系统性挤掉权益）
- 4.5 候选池显式随机抽样（不随 SQL 返回顺序变化）
- 4.6 自然月末采样（采样间隔 = 前瞻窗口 = 1 个月）
- 4.7 无风险利率单一真源
- 4.8 判定走结构化 level，不在 emoji 字符串上做业务判断
- 4.10 回撤 1 年口径 / 托管费缺失标注 / 分数范围 [0,100]
"""
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.analysis.historical_recommender as hr_mod
import src.analysis.fund_scorer as fs_mod
from src.analysis.fund_scorer import FundScreener, type_bucket
from src.analysis.historical_recommender import HistoricalRecommender
from src.analysis.risk_free import RISK_FREE_ANNUAL
from src.data.database import Database

NAV_DAYS = 260          # >252 才进候选池；>60 才进筛选池


def _seed(db, code, name, ftype, daily_ret=0.001, mgt_fee=None, purchase_status="开放申购"):
    db.upsert_fund_info({"fund_code": code, "fund_name": name, "fund_type": ftype,
                         "mgt_fee": mgt_fee, "purchase_status": purchase_status,
                         "establish_date": "2015-01-01"})
    nav, rows = 1.0, []
    start = pd.Timestamp("2025-06-02")
    for i in range(NAV_DAYS):
        nav *= (1 + daily_ret)
        d = (start + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
        rows.append((code, d, round(nav, 6), round(nav, 6), 0.0))
    db.conn.executemany(
        "INSERT OR REPLACE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return) "
        "VALUES (?,?,?,?,?)", rows)
    db.conn.commit()


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "recsys.db"))
    yield d
    d.close()


# ==================== 4.1 排序与综合评分 ====================

class TestSortNotArbitrary:
    def test_fee_missing_sorts_last_within_same_label(self, db):
        """同风险等级内：费率缺失排在**最后**，不得当 0 排最前（4.1 验收）。"""
        s = FundScreener(db)
        _seed(db, "F01", "有费率的好基金A", "混合型", daily_ret=0.001, mgt_fee=0.6)
        _seed(db, "F02", "无费率的好基金A", "混合型", daily_ret=0.001, mgt_fee=None)
        _seed(db, "F03", "有费率的好基金B", "混合型", daily_ret=0.001, mgt_fee=1.2)
        df = s.screen_funds(fund_types=["混合型"], max_results=10)
        fees = list(df["mgt_fee"])
        assert not any(f == 0 for f in fees if f is not None), "缺失被写成了 0"
        # F02（缺失→NaN）必须排在两只费率已知的基金之后
        codes = list(df["fund_code"])
        assert codes.index("F02") > max(codes.index("F01"), codes.index("F03"))

    def test_higher_sharpe_ranks_higher_within_same_label(self, db):
        """同为稳健：夏普更高的基金排前面（类型桶内归一化评分生效）。"""
        s = FundScreener(db)
        _seed(db, "G01", "平庸基金A", "混合型", daily_ret=0.0005)
        _seed(db, "G02", "优秀基金A", "混合型", daily_ret=0.002)
        df = s.screen_funds(fund_types=["混合型"], max_results=10)
        codes = list(df["fund_code"])
        assert codes[0] == "G02", "桶内夏普更高的基金应排最前"
        assert "quality_score" in df.columns and df["quality_score"].notna().all()

    def test_quality_score_is_percentile_scaled(self, db):
        s = FundScreener(db)
        _seed(db, "H01", "差基金A", "混合型", daily_ret=-0.0005)
        _seed(db, "H02", "中基金A", "混合型", daily_ret=0.0008)
        _seed(db, "H03", "好基金A", "混合型", daily_ret=0.002)
        df = s.screen_funds(fund_types=["混合型"], max_results=10)
        sc = {r["fund_code"]: r["quality_score"] for _, r in df.iterrows()}
        assert sc["H03"] > sc["H02"] > sc["H01"]
        assert 0 <= min(sc.values()) and max(sc.values()) <= 100

    def test_type_bucket_normalization(self, db):
        """类型分组归一化：债基的低回撤不再天然压过权益（桶内比较）。"""
        s = FundScreener(db)
        # 债基：波动极小；权益：波动大但夏普好
        _seed(db, "B01", "稳债C", "债券型-长债", daily_ret=0.0002)
        _seed(db, "E01", "锐意进取混合A", "混合型", daily_ret=0.002)
        df = s.screen_funds(fund_types=["混合型", "债券型"], max_results=10)
        sc = {r["fund_code"]: r["quality_score"] for _, r in df.iterrows()}
        # 两者都在各自的桶内 → 都应是桶内第 1 名附近（不再同池被债基碾压）
        assert sc["E01"] > 50 and sc["B01"] > 50


class TestTypeBucket:
    def test_equity_keywords(self):
        assert type_bucket("混合型-灵活配置") == "equity"
        assert type_bucket("股票型") == "equity"
        assert type_bucket("指数型-股票") == "equity"
        assert type_bucket("QDII") == "equity"

    def test_bond_and_unknown(self):
        assert type_bucket("债券型-长债") == "bond"
        assert type_bucket("") == "bond"
        assert type_bucket(None) == "bond"


# ==================== 4.8 结构化判定 ====================

class TestStructuredChecks:
    def test_score_series_returns_levels(self, db):
        s = FundScreener(db)
        _seed(db, "L01", "正常基金A", "混合型", daily_ret=0.001, mgt_fee=0.6)
        r = s._score_series(s._nav_values("L01"), db.get_fund_info("L01"))
        assert set(r["check_levels"]) == set(r["quality_checks"])
        assert all(lv in ("pass", "warn", "fail", "info", "unknown")
                   for lv in r["check_levels"].values())

    def test_unknown_checks_do_not_degrade_label(self, db):
        """「无数据」不算 warn —— 结构化 level 生效（老实现按 ⚠️ 前缀计数，
        emoji 一改判定就静默失效）。"""
        s = FundScreener(db)
        _seed(db, "U01", "无数据基金A", "混合型", daily_ret=0.001, mgt_fee=None)
        info = db.get_fund_info("U01")
        info["establish_date"] = ""          # 年龄：无数据
        r = s._score_series(s._nav_values("U01"), info)
        # 年龄/费率都缺 → unknown，不产生 warning → 不该是 🟡 注意
        assert r["risk_label"] == "🟢 稳健"
        assert r["check_levels"]["成立时间"] == "unknown"
        assert r["check_levels"]["费率"] == "unknown"

    def test_fee_check_requires_both_mgt_and_custodian(self, db):
        """A2 改口径（2026-09-22）：指标改为 **TER = 管理费 + 托管费 + 销售服务费**。

        旧版只给管理费也算 pass（会把 TER 系统性算低）。新契约：缺必收项 → **不可算**，
        显式声明缺哪项（铁律 5），绝不写 0 顶替。
        """
        s = FundScreener(db)
        lv, text, warn, ter = s._check_fee({"mgt_fee": 0.6, "custodian_fee": 0})
        assert lv == "unknown" and ter is None
        assert "托管费" in text, "必须显式说明缺的是托管费（而不是只写字段名或写 0）"

    def test_drawdown_check_level(self, db):
        s = FundScreener(db)
        assert s._check_drawdown(np.full(80, 1.0))[0] == "pass"
        assert s._check_drawdown(np.linspace(1.0, 0.5, 80))[0] == "fail"


# ==================== 4.5 候选池抽样 ====================

class TestCandidateSampling:
    def _seed_pool(self, db, order):
        codes_eq = ["9%03d" % i for i in range(1, 11)]     # 混合型（权益）
        codes_bd = ["8%03d" % i for i in range(1, 6)]     # 债券
        for i, c in enumerate(order):
            ftype = "混合型" if c.startswith("9") else "债券型-长债"
            _seed(db, c, "基金" + c, ftype, daily_ret=0.001 + 0.00001 * i)

    def test_pool_independent_of_sql_order(self, tmp_path):
        """验收要求：候选池不随 SQL 顺序变化（乱序插入 → 同一抽样结果）。"""
        codes_eq = ["9%03d" % i for i in range(1, 11)]
        codes_bd = ["8%03d" % i for i in range(1, 6)]
        all_codes = codes_eq + codes_bd
        results = []
        for k, order in enumerate([all_codes, list(reversed(all_codes)),
                                    all_codes[::2] + all_codes[1::2]]):
            d = Database(str(tmp_path / ("cand%d.db" % k)))
            self._seed_pool(d, order)
            hr = HistoricalRecommender(d)
            got = sorted(hr._get_candidates(per_group=4))
            results.append(got)
            d.close()
        assert results[0] == results[1] == results[2]
        # 每组最多 per_group 只
        got_eq = [c for c in results[0] if c.startswith("9")]
        got_bd = [c for c in results[0] if c.startswith("8")]
        assert len(got_eq) == 4 and len(got_bd) == 4

    def test_pool_is_reproducible(self, db):
        self._seed_pool(db, ["9%03d" % i for i in range(1, 11)]
                        + ["8%03d" % i for i in range(1, 6)])
        hr = HistoricalRecommender(db)
        assert hr._get_candidates(per_group=4) == hr._get_candidates(per_group=4)


# ==================== 4.6 月末采样 ====================

class TestMonthlyDates:
    def test_dates_are_natural_month_ends(self, db):
        hr = HistoricalRecommender(db)
        db.get_latest_nav_date = lambda: "2026-09-15"
        dates = hr._get_monthly_dates(1)
        assert len(dates) >= 11
        for d in dates:
            dt = date.fromisoformat(d)
            nxt = dt + timedelta(days=1)
            assert nxt.day == 1, f"{d} 不是月末"
        # 采样间隔 ≈ 1 个月（不再重叠 50%）
        months = [date.fromisoformat(d).strftime("%Y-%m") for d in dates]
        assert len(months) == len(set(months)), "一个月出现两个采样点"


# ==================== 4.4 分组评分 ====================

class TestGroupedBacktest:
    def test_monthly_picks_include_both_buckets(self, db):
        """同池排序会让债基拿"低回撤免费分"挤掉权益；分组后两个桶各有代表。"""
        _seed(db, "9501", "权益弱鸡A", "混合型", daily_ret=0.0002)
        _seed(db, "9502", "权益优等A", "混合型", daily_ret=0.002)
        _seed(db, "8501", "稳债一只C", "债券型-长债", daily_ret=0.0003)
        _seed(db, "8502", "稳债二只C", "债券型-长债", daily_ret=0.0004)
        hr = HistoricalRecommender(db)
        nav_cache = {c: hr._load_nav_tuples(c) for c in ("9501", "9502", "8501", "8502")}
        last = nav_cache["9501"][-1][0]
        picks, stats = hr._run_monthly_backtest(nav_cache, [last], top_n=1)
        got = {p["code"] for p in picks[last]}
        assert "9502" in got, "权益桶 Top1 必须入选"
        assert "8502" in got, "非权益桶 Top1 必须入选"


# ==================== 4.7 无风险利率单一真源 ====================

class TestRiskFreeSingleSource:
    def test_all_modules_share_the_constant(self):
        from src.analysis import backtest as bt
        from src.analysis import vol_predictor as vp
        assert bt.compute_metrics.__defaults__ == (RISK_FREE_ANNUAL,)
        assert vp._portfolio_metrics.__defaults__ == (RISK_FREE_ANNUAL,)
        assert fs_mod.FundScreener.__init__.__defaults__ == (RISK_FREE_ANNUAL,)
        assert hr_mod.RISK_FREE_ANNUAL == RISK_FREE_ANNUAL == 0.02

    def test_score_uses_constant_not_hardcoded_003(self, db, monkeypatch):
        """historical_recommender 原来硬编码 0.03；现在跟随单一真源。"""
        # 构造有波动的序列（纯单调序列 std=0，夏普对 rf 不敏感）：
        # 奇数日 +2%、偶数日 −1.9% → 年化收益约 +13%，年化波动约 31%，
        # rf=0 时 sv≈0.4，rf=0.5 时 sv 深度为负 → 落在归一化的敏感区间内。
        navs, v = [], 1.0
        for i in range(80):
            v *= 1.02 if i % 2 == 0 else 0.981
            d = (pd.Timestamp("2026-01-01") + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
            navs.append((d, v))
        end = navs[-1][0]
        monkeypatch.setattr(hr_mod, "RISK_FREE_ANNUAL", 0.0)
        s0 = hr_mod.HistoricalRecommender._score_from_tuples(navs, end)
        monkeypatch.setattr(hr_mod, "RISK_FREE_ANNUAL", 0.5)
        s1 = hr_mod.HistoricalRecommender._score_from_tuples(navs, end)
        assert s0 != s1, "rf 应该影响分数（原实现硬编码 0.03，改常量无效）"
        assert s0 > s1, "rf 更高 → 夏普项得分更低"


# ==================== 4.10 打分口径 ====================

class TestScoreCalibration:
    def test_drawdown_uses_last_year_only(self, db):
        """远古回撤不再惩罚：全历史大跌但近1年平稳 → 回撤项接近满分。"""
        navs, v = [], 1.0
        for i in range(400):
            v *= 0.5 if 50 <= i < 60 else 1.0005     # 只在第 50~60 天崩 50%
            d = (pd.Timestamp("2024-06-03") + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
            navs.append((d, v))
        d_end = navs[-1][0]
        s = hr_mod.HistoricalRecommender._score_from_tuples(navs, d_end)
        # 若回撤从全历史起算，dd 项 ≈ (50-50)/50*100*0.3 = 0；近1年口径应 ≈ 30
        assert s > 40, f"回撤项被远古大跌拖累: score={s}"

    def test_score_range_is_0_to_100(self):
        flat = [("2026-%02d-%02d" % (1 + i // 28, 1 + i % 28), 1.0) for i in range(80)]
        s = hr_mod.HistoricalRecommender._score_from_tuples(flat, flat[-1][0])
        assert 0 <= s <= 100
