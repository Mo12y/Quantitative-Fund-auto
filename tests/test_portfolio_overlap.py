"""相关性重叠（"伪分散"）计算模块测试 —— `portfolio_overlap`。

口径锁定：
1. 完全相同的净值序列 → r ≈ 1.00（同涨同跌必须被抓到）；
2. 独立序列 → r 低；
3. **周数不足**（< 20 周）→ 不产出结果（调用方按"未评估"处理，绝不猜）；
4. 候选就是已持有的那只 → 不参与相关性判定（交给 `holding_overlap` 的"已持有"）。
"""
import os
import random
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis import portfolio_overlap as po
from src.data.database import Database


def _seed_series(db, code, seed, n=400, start="2025-08-01"):
    """造一条确定性随机游走净值（同 seed → 完全同序列 → r=1.00）"""
    rnd = random.Random(seed)
    v = 1.0
    d0 = date.fromisoformat(start)
    rows = []
    for i in range(n):
        v *= (1 + rnd.gauss(0, 0.006))
        rows.append((code, (d0 + timedelta(days=i)).isoformat(), round(v, 6), round(v, 6), 0.0))
    db.insert_nav_batch(rows)


@pytest.fixture()
def conn(tmp_path):
    db = Database(str(tmp_path / "overlap.db"))
    _seed_series(db, "A0", seed=1)
    _seed_series(db, "B0", seed=1)          # 与 A0 同序列 → r = 1
    _seed_series(db, "C0", seed=99)         # 独立
    _seed_series(db, "D0", seed=5, n=30)    # 周数不足（约 5 周）
    yield db.conn
    db.close()


def test_weekly_returns_shape(conn):
    r = po.weekly_returns(conn, ["A0"])
    assert "A0" in r and 40 <= r["A0"].size <= 60, "400 天 ≈ 56 周"
    assert po.weekly_returns(conn, ["不存在"]) == {}


def test_same_series_is_highly_correlated(conn):
    m = po.overlap_map(conn, ["B0", "C0", "D0"], ["A0"])
    assert m["B0"]["max_r"] > 0.99 and m["B0"]["against"] == "A0"
    assert m["B0"]["n"] >= 20
    assert m["C0"]["max_r"] < 0.5, "独立序列不该被当成重叠"
    assert "D0" not in m, "周数不足 → 不产出结果（由调用方声明未评估）"


def test_self_match_excluded(conn):
    assert po.overlap_map(conn, ["A0"], ["A0"]) == {}, \
        "候选=已持有的那只 → 不由相关性判定（避免 r=1.00 的误导性理由）"


def test_empty_inputs(conn):
    assert po.overlap_map(conn, ["B0"], []) == {}
    assert po.overlap_map(conn, [], ["A0"]) == {}
    assert po.overlap_map(conn, None, None) == {}