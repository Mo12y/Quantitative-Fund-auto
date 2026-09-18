"""
`HiThinkCollector.ms_to_date` 的时区回归测试（批次 2.3）。

同花顺返回的是**北京时间零点**的毫秒时间戳。旧实现用
`datetime.fromtimestamp(ms/1000)` —— 取的是**本机时区**：
本机 UTC+8 时恰好正确，但换到 UTC 机器（Docker 默认）会整体早一天，
直接污染 T+1 确认与 `effective_apply_date`。
"""
import os
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.hithink_collector import HiThinkCollector

# 同花顺实测样例：北京时间 2026-09-04 00:00
MS = 1788451200000
EXPECTED = "2026-09-04"


class TestMsToDate(unittest.TestCase):
    def test_known_sample(self):
        self.assertEqual(HiThinkCollector.ms_to_date(MS), EXPECTED)

    def test_same_instant_differs_between_timezones(self):
        """证明这个时间戳在 UTC 下确实是**前一天** —— 所以机器时区必须被排除在外"""
        utc_day = datetime.fromtimestamp(MS / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        cn_day = datetime.fromtimestamp(MS / 1000, tz=ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        self.assertEqual(utc_day, "2026-09-03")
        self.assertEqual(cn_day, EXPECTED)
        # 我们返回的是北京时间那一天，而不是 UTC 那一天
        self.assertEqual(HiThinkCollector.ms_to_date(MS), cn_day)
        self.assertNotEqual(HiThinkCollector.ms_to_date(MS), utc_day)

    def test_midnight_boundary(self):
        """北京时间 00:00 与 23:59:59 应落在同一天，且不受 UTC 偏移影响"""
        base = MS
        self.assertEqual(HiThinkCollector.ms_to_date(base), EXPECTED)
        self.assertEqual(HiThinkCollector.ms_to_date(base + 86_399_000), EXPECTED)   # 23:59:59

    def test_bad_input_returns_empty(self):
        self.assertEqual(HiThinkCollector.ms_to_date(None), "")


class TestIndependentOfMachineTimezone(unittest.TestCase):
    """关键回归：在 TZ=UTC 的子进程里必须仍返回北京时间那一天。

    Windows 的 Python 没有 `time.tzset()`，无法在进程内改时区，
    所以这里用**子进程 + TZ 环境变量**来复现"部署到 UTC 机器"的场景。
    """

    def _run_with_tz(self, tz):
        code = (
            "import sys; sys.path.insert(0, r'%s');"
            "from src.data.hithink_collector import HiThinkCollector as H;"
            "print(H.ms_to_date(%d))" % (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), MS)
        )
        env = dict(os.environ)
        env["TZ"] = tz
        p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, env=env, timeout=120)
        return (p.stdout or "").strip()

    def test_utc_machine_still_returns_beijing_date(self):
        self.assertEqual(self._run_with_tz("UTC"), EXPECTED)

    def test_other_timezone_still_returns_beijing_date(self):
        self.assertEqual(self._run_with_tz("America/New_York"), EXPECTED)


if __name__ == "__main__":
    unittest.main()
