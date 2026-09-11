"""
构建交易日历（A股）—— 供 T+1 确认日、定投期次推算使用。

背景：akshare 的交易日历接口在部分网络环境不可用，但东方财富的**基金历史净值**接口稳定，
而公募基金只在交易日公布净值 —— 因此用“净值日期”作为交易日探针，是可靠且零新依赖的做法。

用法：
    python scripts/build_trade_calendar.py

产物：
    data/trade_calendar.csv     （一列 trade_date，可供 `python src/main.py calendar` 兜底导入）
    data/fund_quant.db          → trade_calendar 表

注意：净值有 1 天滞后（当天净值收盘后才公布），因此日历的**最后几天会缺失**。
`src/analysis/trade_rules.is_trade_day()` 已处理：日历覆盖区间内按日历判断，
区间外（今天/未来）回退“跳过周末”，不会被过期日历误判成休市。
"""
import csv
import io
import json
import subprocess
import sys
import time

sys.path.insert(0, ".")
from src.data.database import Database  # noqa: E402

# 用两三只成立久、每日公布净值的境内基金做探针（并集更稳）
PROBES = ["007029", "014143", "018392"]
SINCE = "2024-06-01"


def _lsjz(code: str, page: int, size: int = 50, retries: int = 3):
    url = (f"https://api.fund.eastmoney.com/f10/lsjz"
           f"?fundCode={code}&pageIndex={page}&pageSize={size}")
    for _ in range(retries):
        p = subprocess.run(
            ["curl.exe", "-sS", "-m", "25", "--ssl-no-revoke", "-A", "Mozilla/5.0",
             "-H", "Referer: http://fundf10.eastmoney.com/", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode == 0 and p.stdout:
            try:
                return json.loads(p.stdout)
            except Exception:
                pass
        time.sleep(0.5)
    return None


def fetch_dates(code: str, since: str = SINCE) -> set:
    out = set()
    for page in range(1, 60):
        j = _lsjz(code, page)
        if not j or not j.get("Data"):
            break
        rows = j["Data"].get("LSJZList") or []
        if not rows:
            break
        oldest = None
        for r in rows:
            d = r.get("FSRQ")
            if d:
                oldest = d
                if d >= since:
                    out.add(d)
        if oldest and oldest < since:
            break
        time.sleep(0.25)
    return out


def main() -> int:
    dates = set()
    for code in PROBES:
        got = fetch_dates(code)
        print(f"  探针 {code}: {len(got)} 个净值日")
        dates |= got
    if not dates:
        print("❌ 未取到净值日期（网络不可用？）")
        return 1
    dates = sorted(dates)
    print(f"交易日集合 {len(dates)} 个: {dates[0]} ~ {dates[-1]}")

    with io.open("data/trade_calendar.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["trade_date"])
        for d in dates:
            w.writerow([d])
    print("已写 data/trade_calendar.csv")

    db = Database("data/fund_quant.db")
    db.upsert_trade_dates(dates)
    print("trade_calendar 表现有交易日:", db.count_trade_dates())
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
