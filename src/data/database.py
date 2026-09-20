"""
数据库模块: SQLite 数据库的创建、读写操作。

所有数据存储在单个 SQLite 文件中，无需安装数据库服务。
"""

import contextlib
import json
import sqlite3
import os
import time
from typing import Optional


class Database:
    """SQLite 数据库管理类"""

    def __init__(self, db_path: str = "data/fund_quant.db"):
        """
        初始化数据库连接。

        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path

        # 确保数据目录存在
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        # isolation_level=None → 自动提交：事务边界由我们显式控制。
        # 原因：sqlite3 默认会在 DML 前隐式 BEGIN，与显式 "BEGIN IMMEDIATE" 冲突
        # （报 "cannot start a transaction within a transaction"）。
        # 单条写各自成事务；多步读改写用 immediate() 包成一个原子事务。
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row  # 让查询结果可以通过列名访问
        # WAL：读不阻塞写、写不阻塞读，降低 Flask threaded=True 下并发写报 "database is locked" 的概率。
        # 放在建表之前设置，随后的 CREATE/ALTER 都走 WAL。
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
        except Exception:
            pass
        self._create_tables()

    # ========== 删除前自动备份（防误删） ==========

    # ========== 分析结果快照（跨进程重启，端点是纯 SELECT） ==========

    def get_analysis_snapshot(self, key: str, max_age_sec: float = None):
        """读快照；不存在或过期返回 None。"""
        try:
            row = self.conn.execute(
                "SELECT computed_at, payload FROM analysis_snapshot WHERE key = ?",
                (key,)).fetchone()
        except Exception:
            return None
        if not row:
            return None
        if max_age_sec and (time.time() - float(row["computed_at"])) > max_age_sec:
            return None
        try:
            return json.loads(row["payload"])
        except Exception:
            return None

    def set_analysis_snapshot(self, key: str, payload) -> None:
        """写快照（失败不影响主流程）"""
        try:
            self.conn.execute(
                "INSERT INTO analysis_snapshot (key, computed_at, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET computed_at=excluded.computed_at, "
                "payload=excluded.payload",
                (key, time.time(), json.dumps(payload, ensure_ascii=False, default=str)))
        except Exception:
            pass

    def clear_analysis_snapshots(self, keys=None, prefix: str = None) -> int:
        """失效快照：按 key 列表或前缀。写操作后调用，保证端点不会读到旧账。"""
        cur = self.conn.cursor()
        try:
            if keys:
                cur.execute("DELETE FROM analysis_snapshot WHERE key IN (%s)"
                            % ",".join("?" * len(keys)), list(keys))
            elif prefix:
                cur.execute("DELETE FROM analysis_snapshot WHERE key LIKE ?", (prefix + "%",))
            else:
                cur.execute("DELETE FROM analysis_snapshot")
            return cur.rowcount
        except Exception:
            return 0

    @contextlib.contextmanager
    def immediate(self):
        """BEGIN IMMEDIATE 事务：多步读改写要么全成要么全滚。

        IMMEDIATE 一上来就取写锁，避免两个连接各自读到旧值再互相覆盖（丢失更新）。
        """
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except Exception:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()

    def _backup_rows(self, table: str, rows: list):
        """把即将删除的行写到 <db目录>/backups/<table>_<时间>.json，便于事后恢复。
        备份失败绝不影响删除本身。"""
        if not rows:
            return
        try:
            import json as _json
            import time as _time
            base = os.path.dirname(os.path.abspath(self.db_path)) or "."
            d = os.path.join(base, "backups")
            os.makedirs(d, exist_ok=True)
            fn = os.path.join(d, f"{table}_{_time.strftime('%Y%m%d_%H%M%S')}.json")
            with open(fn, "w", encoding="utf-8") as f:
                _json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2, default=str)
        except Exception:
            pass

    def _create_tables(self):
        """创建所有数据表（如果不存在）"""
        cursor = self.conn.cursor()

        # 基金基本信息表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fund_info (
                fund_code TEXT PRIMARY KEY,
                fund_name TEXT NOT NULL,
                fund_type TEXT,
                establish_date TEXT,
                fund_size REAL,
                mgt_fee REAL,
                custodian_fee REAL,
                purchase_fee REAL,
                redeem_fee TEXT,
                manager_name TEXT,
                manager_tenure REAL,
                company_name TEXT,
                purchase_status TEXT,
                risk_level TEXT,
                investment_style TEXT,
                benchmark TEXT,
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        # 基金净值历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fund_nav (
                fund_code TEXT NOT NULL,
                nav_date TEXT NOT NULL,
                unit_nav REAL,
                acc_nav REAL,
                daily_return REAL,
                PRIMARY KEY (fund_code, nav_date)
            )
        """)

        # 基金净值索引（加速按日期查询）
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_fund_nav_date
            ON fund_nav(nav_date)
        """)

        # 指数行情表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS index_daily (
                index_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                close REAL,
                volume REAL,
                pe REAL,
                pb REAL,
                pe_percentile REAL,
                pb_percentile REAL,
                PRIMARY KEY (index_code, trade_date)
            )
        """)

        # 指数估值快照表（最新的一条）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS index_valuation (
                index_code TEXT PRIMARY KEY,
                index_name TEXT,
                pe REAL,
                pe_percentile REAL,
                pb REAL,
                pb_percentile REAL,
                dividend_yield REAL,
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        # 用户持仓表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fund_code TEXT NOT NULL,
                fund_name TEXT,
                buy_date TEXT NOT NULL,
                buy_amount REAL NOT NULL,
                buy_nav REAL,
                shares REAL,
                status TEXT DEFAULT 'holding',
                sell_date TEXT,
                sell_amount REAL,
                notes TEXT
            )
        """)

        # 每周信号记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS weekly_signals (
                week_start TEXT PRIMARY KEY,
                market_temp REAL,
                temp_level TEXT,
                action_suggestion TEXT,
                target_equity_pct REAL,
                report_text TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        # 数据采集日志表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS data_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_type TEXT NOT NULL,
                last_collected TEXT,
                status TEXT,
                record_count INTEGER,
                error_msg TEXT
            )
        """)

        # 定投计划表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dca_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fund_code TEXT NOT NULL,
                fund_name TEXT,
                amount_per_period REAL NOT NULL,
                frequency TEXT DEFAULT 'weekly',
                start_date TEXT NOT NULL,
                next_run_date TEXT,
                total_periods INTEGER DEFAULT 0,
                total_amount REAL DEFAULT 0,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        # 交易日历表（T+1 确认 / 定投跳过非交易日）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_calendar (
                trade_date TEXT PRIMARY KEY,
                is_open    INTEGER NOT NULL DEFAULT 1
            )
        """)

        # 交易流水表（买入/卖出，含申请-确认-起算；支持部分卖出）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                holding_id INTEGER,
                fund_code TEXT,
                kind TEXT,                  -- buy | sell
                apply_date TEXT,            -- 申请日
                apply_after_cutoff INTEGER DEFAULT 0,
                confirm_date TEXT,          -- 确认日
                confirm_nav REAL,           -- 确认净值
                accrual_start TEXT,         -- 收益起算日
                shares REAL,                -- 份额
                amount REAL,                -- 金额
                fee REAL DEFAULT 0,
                status TEXT,                -- pending_confirm | confirmed | settled
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        self.conn.commit()

        # 分析结果快照表：把「温度/筛选池/板块总榜」等重计算结果落库，
        # 端点命中快照即为纯 SELECT；进程重启后依然有效（内存缓存做不到）。
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analysis_snapshot (
                key         TEXT PRIMARY KEY,
                computed_at REAL NOT NULL,
                payload     TEXT NOT NULL
            )
        """)
        self.conn.commit()

        # 定投期次表（对账基础：应投/已投/待补录）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dca_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL,
                period_no INTEGER,
                planned_date TEXT,
                planned_amount REAL,
                status TEXT DEFAULT 'pending',
                executed_date TEXT,
                holding_id INTEGER,
                UNIQUE(plan_id, period_no)
            )
        """)

        self.conn.commit()

        # 兼容迁移：dca_plans 增列（auto_sync / last_synced_at）
        try:
            have = {r[1] for r in self.conn.execute("PRAGMA table_info(dca_plans)")}
            if "auto_sync" not in have:
                self.conn.execute("ALTER TABLE dca_plans ADD COLUMN auto_sync INTEGER DEFAULT 1")
            if "last_synced_at" not in have:
                self.conn.execute("ALTER TABLE dca_plans ADD COLUMN last_synced_at TEXT")
            self.conn.commit()
        except Exception:
            pass

        # 投资计划（可维护：目标/金额/周期/风险偏好 + 基金条目）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS investment_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                goal TEXT,
                total_capital REAL,
                cash_reserve REAL,
                start_date TEXT,
                horizon TEXT,
                risk_pref TEXT,
                notes TEXT,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS investment_plan_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL,
                fund_code TEXT,
                fund_name TEXT,
                role TEXT,
                target_amount REAL,
                target_pct REAL,
                cadence TEXT,
                dca_daily REAL,
                tranches TEXT,
                notes TEXT
            )
        """)

        self.conn.commit()

        # 兼容迁移：给 holdings 补 T+1/T+2 相关列（老库自动升级，不重建表）
        self._migrate_holdings_t1()

        # 常用过滤/排序列补索引（幂等，加速持仓查询与候选采样；对已有大库首开建一次）
        for ddl in (
            "CREATE INDEX IF NOT EXISTS idx_holdings_status ON holdings(status)",
            "CREATE INDEX IF NOT EXISTS idx_fund_info_mgt_fee ON fund_info(mgt_fee)",
            "CREATE INDEX IF NOT EXISTS idx_fund_nav_date ON fund_nav(nav_date)",
            "CREATE INDEX IF NOT EXISTS idx_transactions_holding ON transactions(holding_id)",
        ):
            try:
                self.conn.execute(ddl)
            except Exception:
                pass
        self.conn.commit()

    def _migrate_holdings_t1(self):
        """幂等给 holdings 添加 T+1/T+2 字段（存在则跳过）"""
        want = {
            "apply_date": "TEXT",
            "apply_after_cutoff": "INTEGER DEFAULT 0",
            "confirm_date": "TEXT",
            "confirm_nav": "REAL",
            "accrual_start": "TEXT",
        }
        try:
            have = {r[1] for r in self.conn.execute("PRAGMA table_info(holdings)")}
        except Exception:
            return
        for col, decl in want.items():
            if col not in have:
                try:
                    self.conn.execute(f"ALTER TABLE holdings ADD COLUMN {col} {decl}")
                except Exception:
                    pass
        self.conn.commit()

    # ========== 基金信息操作 ==========

    # upsert 允许写入的字段清单（局部更新语义，见 upsert_fund_info）
    _FUND_INFO_FIELDS = (
        "fund_name", "fund_type", "establish_date", "fund_size",
        "mgt_fee", "custodian_fee", "purchase_fee", "redeem_fee",
        "manager_name", "manager_tenure", "company_name",
        "purchase_status", "risk_level", "investment_style", "benchmark",
    )
    # 数字字段里 0 视为「未采到」：费率/规模/年限为 0 没有业务意义，
    # 且批量路径缺列时会把默认 "0%" 解析成 0 —— COALESCE 拦得住 NULL 拦不住 0
    _FUND_INFO_NUMERIC = frozenset(
        {"fund_size", "mgt_fee", "custodian_fee", "purchase_fee", "manager_tenure"})

    def _has_new_value(self, field: str, v) -> bool:
        """该字段的新值是否足以覆盖旧值：非空；数字字段还要求非 0。"""
        if v is None or v == "":
            return False
        if field in self._FUND_INFO_NUMERIC:
            try:
                if float(v) == 0:
                    return False
            except (TypeError, ValueError):
                return True           # 非数字内容按文本的「非空」规则处理
        return True

    def upsert_fund_info(self, fund: dict, commit: bool = True):
        """
        插入或更新基金基本信息（**局部更新**语义，批次 4.2 根因修复）。

        背景：两条采集路径各采一半字段 —— 批量列表路径有费率/申购状态，
        enrich 详情路径有规模/公司/经理/成立日。旧版全字段覆盖导致
        **谁后跑谁清空对方的数据**（实测两集合完美互斥：费率>0 且有规模的 = 0）。

        现在的规则：
        - INSERT：未提供的字段写 NULL（新基金没有旧值可保护）；
        - ON CONFLICT UPDATE：只 SET 本次**真正采到值**的字段 ——
          NULL / 空串一律不覆盖；数字字段（费率/规模/年限）的 0 同样视为
          未采到、不覆盖。`redeem_fee` 原有的 COALESCE 行为被本规则包含。

        代价（已知且接受）：已入库的字段无法通过 upsert「清空」，只能被
        新的非空值覆盖；确需清空请显式 UPDATE。

        commit=False：调用方负责事务（如 cmd_snapshot 两万行包成一个
        immediate() 事务，避免逐行 commit 的 fsync 开销）。
        """
        cols = ["fund_code"] + list(self._FUND_INFO_FIELDS) + ["updated_at"]
        placeholders = ["?"] * (len(cols) - 1) + ["datetime('now','localtime')"]
        sets = [f"{f}=excluded.{f}" for f in self._FUND_INFO_FIELDS
                if self._has_new_value(f, fund.get(f))]
        sets.append("updated_at=datetime('now','localtime')")
        # fund_name 有 NOT NULL 约束：新基金没给名字时写空串
        # （只发生在 INSERT；更新路径空串不会覆盖，见 _has_new_value）
        name_val = fund.get("fund_name")
        params = [fund.get("fund_code")] + [
            ("" if (f == "fund_name" and name_val is None) else fund.get(f))
            for f in self._FUND_INFO_FIELDS]
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO fund_info (" + ",".join(cols) + ") "
            "VALUES (" + ",".join(placeholders) + ") "
            "ON CONFLICT(fund_code) DO UPDATE SET " + ", ".join(sets),
            params)
        if commit:
            self.conn.commit()

    def get_all_funds(self) -> list:
        """获取所有基金基本信息"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM fund_info ORDER BY fund_code")
        return [dict(row) for row in cursor.fetchall()]

    def get_fund_info(self, fund_code: str) -> Optional[dict]:
        """按代码取基金基本信息（单行主键查询，替代 get_all_funds 全表扫描）"""
        row = self.conn.execute(
            "SELECT * FROM fund_info WHERE fund_code = ?", (fund_code,)).fetchone()
        return dict(row) if row else None

    def get_funds_by_type(self, fund_type: str) -> list:
        """按类型获取基金（如 '股票型', '混合型'）"""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM fund_info WHERE fund_type LIKE ? ORDER BY fund_code",
            (f"%{fund_type}%",)
        )
        return [dict(row) for row in cursor.fetchall()]

    # ========== 净值数据操作 ==========

    def insert_nav_batch(self, nav_records: list):
        """
        批量插入净值数据。

        Args:
            nav_records: [(fund_code, nav_date, unit_nav, acc_nav, daily_return), ...]
        """
        if not nav_records:
            return
        # 自动提交模式下逐条 INSERT 会是 N 个事务，批量写入必须显式包成一个事务
        with self.immediate():
            self.conn.executemany("""
                INSERT OR IGNORE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return)
                VALUES (?, ?, ?, ?, ?)
            """, nav_records)

    def get_fund_nav(self, fund_code: str, start_date: str = None, end_date: str = None) -> list:
        """获取单只基金的净值历史"""
        cursor = self.conn.cursor()
        query = "SELECT * FROM fund_nav WHERE fund_code = ?"
        params = [fund_code]
        if start_date:
            query += " AND nav_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND nav_date <= ?"
            params.append(end_date)
        query += " ORDER BY nav_date ASC"
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_latest_fund_nav(self, fund_code: str) -> Optional[dict]:
        """取单只基金最新一条净值（LIMIT 1，避免把整段历史读进内存）"""
        row = self.conn.execute(
            "SELECT nav_date, unit_nav, acc_nav FROM fund_nav "
            "WHERE fund_code = ? ORDER BY nav_date DESC LIMIT 1", (fund_code,)).fetchone()
        return dict(row) if row else None

    def get_recent_fund_nav(self, fund_code: str, limit: int = 30) -> list:
        """取单只基金最近 limit 条净值（升序返回）"""
        rows = self.conn.execute(
            "SELECT nav_date, unit_nav FROM fund_nav WHERE fund_code = ? "
            "ORDER BY nav_date DESC LIMIT ?", (fund_code, int(limit))).fetchall()
        return [dict(r) for r in reversed(rows)]

    def closest_fund_nav(self, fund_code: str, target_date: str) -> Optional[dict]:
        """取最接近 target_date 的一条净值（单条查询，替代全量加载后线性扫描）"""
        row = self.conn.execute(
            "SELECT nav_date, unit_nav, "
            "ABS(julianday(nav_date) - julianday(?)) AS diff_days "
            "FROM fund_nav WHERE fund_code = ? "
            "ORDER BY diff_days ASC, nav_date DESC LIMIT 1",
            (str(target_date)[:10], fund_code)).fetchone()
        return dict(row) if row else None

    def get_latest_nav_date(self) -> Optional[str]:
        """获取最新的净值日期"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT MAX(nav_date) as latest FROM fund_nav")
        row = cursor.fetchone()
        return row["latest"] if row else None

    def get_all_fund_codes(self) -> set:
        """获取所有有净值数据的基金代码集合（供分析层复用，避免裸 SQL）"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT fund_code FROM fund_nav")
        return {row[0] for row in cursor.fetchall()}

    # ========== 指数数据操作 ==========

    def upsert_index_valuation(self, val: dict):
        """更新指数估值快照"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO index_valuation (
                index_code, index_name, pe, pe_percentile, pb, pb_percentile,
                dividend_yield, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(index_code) DO UPDATE SET
                pe=excluded.pe,
                pe_percentile=excluded.pe_percentile,
                pb=excluded.pb,
                pb_percentile=excluded.pb_percentile,
                dividend_yield=excluded.dividend_yield,
                updated_at=datetime('now','localtime')
        """, (
            val.get("index_code"),
            val.get("index_name"),
            val.get("pe"),
            val.get("pe_percentile"),
            val.get("pb"),
            val.get("pb_percentile"),
            val.get("dividend_yield"),
        ))
        self.conn.commit()

    def get_index_valuation(self) -> list:
        """获取所有指数估值快照"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM index_valuation ORDER BY index_code")
        return [dict(row) for row in cursor.fetchall()]

    def get_index_daily(self, index_code: str) -> list:
        """获取某指数日线行情(close/volume)，用于离线计算温度，避免每次联网 akshare"""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT trade_date, close, volume, pe_percentile, pb_percentile "
            "FROM index_daily WHERE index_code = ? ORDER BY trade_date ASC", (index_code,))
        return [dict(row) for row in cursor.fetchall()]

    # ========== 持仓操作 ==========

    def add_holding(self, holding: dict):
        """添加一条持仓记录（含 T+1 确认/起算字段）"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO holdings (
                fund_code, fund_name, buy_date, buy_amount, buy_nav, shares, notes,
                apply_date, apply_after_cutoff, confirm_date, confirm_nav, accrual_start, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            holding.get("fund_code"),
            holding.get("fund_name"),
            holding.get("buy_date"),
            holding.get("buy_amount"),
            holding.get("buy_nav"),
            holding.get("shares"),
            holding.get("notes", ""),
            holding.get("apply_date") or holding.get("buy_date"),
            int(holding.get("apply_after_cutoff") or 0),
            holding.get("confirm_date"),
            holding.get("confirm_nav"),
            holding.get("accrual_start"),
            holding.get("status", "holding"),
        ))
        self.conn.commit()
        return cursor.lastrowid

    def get_current_holdings(self) -> list:
        """获取当前持仓（含待确认/待卖出，便于前端体现 T+1 状态）"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM holdings WHERE status IN ('holding','pending_confirm','sell_pending') "
                       "ORDER BY buy_date")
        return [dict(row) for row in cursor.fetchall()]

    def sell_holding(self, holding_id: int, sell_date: str, sell_amount: float):
        """标记持仓已卖出"""
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE holdings SET status='sold', sell_date=?, sell_amount=?
            WHERE id=?
        """, (sell_date, sell_amount, holding_id))
        self.conn.commit()

    def update_holding(self, holding_id: int, **fields) -> bool:
        """
        更新持仓记录的指定字段（如 buy_amount / buy_date / shares）。

        Returns:
            bool: 是否有记录被更新
        """
        allowed = {"buy_amount", "buy_date", "buy_nav", "shares", "notes", "status",
                   "sell_date", "sell_amount", "apply_date", "apply_after_cutoff",
                   "confirm_date", "confirm_nav", "accrual_start"}
        updates = []
        params = []
        for col, val in fields.items():
            if col in allowed and val is not None:
                updates.append(f"{col} = ?")
                params.append(val)
        if not updates:
            return False
        params.append(holding_id)
        cursor = self.conn.cursor()
        cursor.execute(f"UPDATE holdings SET {', '.join(updates)} WHERE id = ?", params)
        self.conn.commit()
        return cursor.rowcount > 0

    def delete_holding(self, holding_id: int) -> bool:
        """删除持仓记录（用于更正重复录入等；删除前自动备份）"""
        row = self.conn.execute("SELECT * FROM holdings WHERE id = ?", (holding_id,)).fetchone()
        self._backup_rows("holdings", [row] if row else [])
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def get_total_invested(self) -> float:
        """计算总投资金额"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COALESCE(SUM(buy_amount), 0) as total FROM holdings WHERE status='holding'")
        return cursor.fetchone()["total"]

    # ========== 定投计划操作 ==========

    def add_dca_plan(self, plan: dict):
        """新增定投计划"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO dca_plans (fund_code, fund_name, amount_per_period, frequency, start_date, next_run_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            plan["fund_code"],
            plan.get("fund_name", plan["fund_code"]),
            plan["amount_per_period"],
            plan.get("frequency", "weekly"),
            plan["start_date"],
            plan.get("next_run_date"),
        ))
        self.conn.commit()

    def get_dca_plans(self, status: str = None) -> list:
        """获取定投计划列表"""
        cursor = self.conn.cursor()
        if status:
            cursor.execute("SELECT * FROM dca_plans WHERE status = ? ORDER BY id", (status,))
        else:
            cursor.execute("SELECT * FROM dca_plans ORDER BY id")
        return [dict(row) for row in cursor.fetchall()]

    def update_dca_plan(self, plan_id: int, **fields) -> bool:
        """更新定投计划字段（status / amount / frequency 等）"""
        allowed = {"status", "amount_per_period", "frequency", "next_run_date", "total_periods", "total_amount"}
        updates = []
        params = []
        for col, val in fields.items():
            if col in allowed and val is not None:
                updates.append(f"{col} = ?")
                params.append(val)
        if not updates:
            return False
        params.append(plan_id)
        cursor = self.conn.cursor()
        cursor.execute(f"UPDATE dca_plans SET {', '.join(updates)} WHERE id = ?", params)
        self.conn.commit()
        return cursor.rowcount > 0

    def delete_dca_plan(self, plan_id: int) -> bool:
        """删除定投计划（用于 Web 端管理；删除前把计划与期次一并备份）"""
        plan = self.conn.execute("SELECT * FROM dca_plans WHERE id = ?", (plan_id,)).fetchone()
        periods = self.conn.execute("SELECT * FROM dca_periods WHERE plan_id = ?", (plan_id,)).fetchall()
        if plan:
            self._backup_rows("dca_plan_and_periods", [plan] + list(periods))
        cursor = self.conn.cursor()
        with self.immediate():          # 两张表必须一起删，避免删了一半
            cursor.execute("DELETE FROM dca_periods WHERE plan_id = ?", (plan_id,))
            cursor.execute("DELETE FROM dca_plans WHERE id = ?", (plan_id,))
        return cursor.rowcount > 0

    # ---------- 定投期次（对账） ----------

    def get_dca_periods(self, plan_id: int) -> list:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM dca_periods WHERE plan_id = ? ORDER BY period_no", (plan_id,))]

    def upsert_dca_period(self, plan_id: int, period_no: int, planned_date: str,
                          planned_amount: float) -> int:
        """写入/更新某一期（不改动已执行期的状态）"""
        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO dca_periods (plan_id, period_no, planned_date, planned_amount, status)
            VALUES (?, ?, ?, ?, 'pending')
            ON CONFLICT(plan_id, period_no) DO UPDATE SET
                planned_date=excluded.planned_date,
                planned_amount=excluded.planned_amount
        """, (plan_id, period_no, planned_date, planned_amount))
        self.conn.commit()
        cur.execute("SELECT id FROM dca_periods WHERE plan_id=? AND period_no=?", (plan_id, period_no))
        row = cur.fetchone()
        return row[0] if row else 0

    def update_dca_period(self, period_id: int, **fields) -> bool:
        allowed = {"status", "executed_date", "holding_id", "planned_amount", "planned_date"}
        updates, params = [], []
        for col, val in fields.items():
            if col in allowed and val is not None:
                updates.append(f"{col} = ?"); params.append(val)
        if not updates:
            return False
        params.append(period_id)
        cur = self.conn.cursor()
        cur.execute(f"UPDATE dca_periods SET {', '.join(updates)} WHERE id = ?", params)
        self.conn.commit()
        return cur.rowcount > 0

    def sum_dca_periods(self, plan_id: int, status: str = "executed") -> float:
        """按状态汇总期次金额（供派生 total_amount，避免依赖可能过期的计划字段）"""
        row = self.conn.execute(
            "SELECT COALESCE(SUM(planned_amount), 0) FROM dca_periods "
            "WHERE plan_id = ? AND status = ?", (plan_id, status)).fetchone()
        return float(row[0]) if row else 0.0

    def link_dca_period(self, period_id: int, holding_id: int) -> bool:
        """给期次补上买入凭证（holding_id）—— 只补空值，不覆盖已有凭证。"""
        cur = self.conn.cursor()
        cur.execute("UPDATE dca_periods SET holding_id = ? WHERE id = ? AND holding_id IS NULL",
                    (holding_id, period_id))
        return cur.rowcount > 0

    def count_dca_periods(self, plan_id: int, status: str = None) -> int:
        q = "SELECT COUNT(*) FROM dca_periods WHERE plan_id = ?"
        params = [plan_id]
        if status:
            q += " AND status = ?"; params.append(status)
        row = self.conn.execute(q, params).fetchone()
        return int(row[0]) if row else 0

    def get_fund_name(self, fund_code: str) -> Optional[str]:
        """按代码取基金名称（供买入录入补全，避免手工输入错名）"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT fund_name FROM fund_info WHERE fund_code = ?", (fund_code,))
        row = cursor.fetchone()
        return row["fund_name"] if row and row["fund_name"] else None

    # ========== 交易日历 ==========

    def upsert_trade_dates(self, dates, is_open: int = 1):
        """批量写入交易日历（幂等）"""
        rows = [(str(d)[:10], int(is_open)) for d in (dates or []) if d]
        if not rows:
            return
        self.conn.executemany(
            "INSERT INTO trade_calendar (trade_date, is_open) VALUES (?, ?) "
            "ON CONFLICT(trade_date) DO UPDATE SET is_open=excluded.is_open",
            rows,
        )
        self.conn.commit()

    def get_trade_dates(self, start: str = None, end: str = None) -> list:
        """取交易日列表（升序）"""
        q = "SELECT trade_date FROM trade_calendar WHERE is_open = 1"
        params = []
        if start:
            q += " AND trade_date >= ?"; params.append(start)
        if end:
            q += " AND trade_date <= ?"; params.append(end)
        q += " ORDER BY trade_date"
        return [r[0] for r in self.conn.execute(q, params)]

    def get_trade_date_set(self) -> set:
        """取交易日集合（供 trade_rules 注入，O(1) 判定）"""
        return set(self.get_trade_dates())

    def count_trade_dates(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM trade_calendar WHERE is_open = 1").fetchone()
        return int(row[0]) if row else 0

    def clear_trade_calendar(self):
        self.conn.execute("DELETE FROM trade_calendar")
        self.conn.commit()

    # ========== 交易流水（buy/sell，支持部分卖出） ==========

    def add_transaction(self, tx: dict) -> int:
        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO transactions (
                holding_id, fund_code, kind, apply_date, apply_after_cutoff,
                confirm_date, confirm_nav, accrual_start, shares, amount, fee, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tx.get("holding_id"), tx.get("fund_code"), tx.get("kind"),
            tx.get("apply_date"), int(tx.get("apply_after_cutoff") or 0),
            tx.get("confirm_date"), tx.get("confirm_nav"), tx.get("accrual_start"),
            tx.get("shares"), tx.get("amount"), tx.get("fee", 0),
            tx.get("status", "confirmed"), tx.get("notes", ""),
        ))
        self.conn.commit()
        return cur.lastrowid

    def get_transactions(self, holding_id: int = None, fund_code: str = None) -> list:
        q, params = "SELECT * FROM transactions", []
        conds = []
        if holding_id is not None:
            conds.append("holding_id = ?"); params.append(holding_id)
        if fund_code:
            conds.append("fund_code = ?"); params.append(fund_code)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY id"
        return [dict(r) for r in self.conn.execute(q, params)]

    def update_transaction(self, tx_id: int, **fields) -> bool:
        """更新流水（如确认净值/状态）"""
        allowed = {"confirm_nav", "status", "shares", "amount", "fee", "holding_id"}
        updates, params = [], []
        for col, val in fields.items():
            if col in allowed and val is not None:
                updates.append(f"{col} = ?"); params.append(val)
        if not updates:
            return False
        params.append(tx_id)
        cur = self.conn.cursor()
        cur.execute(f"UPDATE transactions SET {', '.join(updates)} WHERE id = ?", params)
        self.conn.commit()
        return cur.rowcount > 0

    # ========== 投资计划（可维护） ==========

    PLAN_FIELDS = ("name", "goal", "total_capital", "cash_reserve", "start_date",
                   "horizon", "risk_pref", "notes", "status")
    ITEM_FIELDS = ("plan_id", "fund_code", "fund_name", "role", "target_amount",
                   "target_pct", "cadence", "dca_daily", "tranches", "notes")

    def get_active_plan(self) -> Optional[dict]:
        """取当前生效计划（含条目）；无则返回 None"""
        row = self.conn.execute(
            "SELECT * FROM investment_plans WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        plan = dict(row)
        plan["items"] = self.get_plan_items(plan["id"])
        return plan

    def create_plan(self, plan: dict) -> int:
        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO investment_plans (name, goal, total_capital, cash_reserve, start_date,
                                          horizon, risk_pref, notes, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
        """, (plan.get("name"), plan.get("goal"), plan.get("total_capital"),
              plan.get("cash_reserve"), plan.get("start_date"), plan.get("horizon"),
              plan.get("risk_pref"), plan.get("notes"), plan.get("status", "active")))
        self.conn.commit()
        return cur.lastrowid

    def update_plan(self, plan_id: int, **fields) -> bool:
        updates, params = [], []
        for col, val in fields.items():
            if col in self.PLAN_FIELDS and val is not None:
                updates.append(f"{col} = ?"); params.append(val)
        if not updates:
            return False
        params.append(plan_id)
        cur = self.conn.cursor()
        cur.execute(f"UPDATE investment_plans SET {', '.join(updates)}, "
                    "updated_at = datetime('now','localtime') WHERE id = ?", params)
        self.conn.commit()
        return cur.rowcount > 0

    def delete_plan(self, plan_id: int) -> bool:
        plan = self.conn.execute("SELECT * FROM investment_plans WHERE id = ?", (plan_id,)).fetchone()
        items = self.conn.execute("SELECT * FROM investment_plan_items WHERE plan_id = ?", (plan_id,)).fetchall()
        if plan:
            self._backup_rows("investment_plan_and_items", [plan] + list(items))
        cur = self.conn.cursor()
        with self.immediate():          # 两张表必须一起删，避免删了一半
            cur.execute("DELETE FROM investment_plan_items WHERE plan_id = ?", (plan_id,))
            cur.execute("DELETE FROM investment_plans WHERE id = ?", (plan_id,))
        return cur.rowcount > 0

    def get_plan_items(self, plan_id: int) -> list:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM investment_plan_items WHERE plan_id = ? ORDER BY id", (plan_id,))]

    def add_plan_item(self, item: dict) -> int:
        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO investment_plan_items (plan_id, fund_code, fund_name, role,
                                               target_amount, target_pct, cadence, dca_daily, tranches, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (item.get("plan_id"), item.get("fund_code"), item.get("fund_name"), item.get("role"),
              item.get("target_amount"), item.get("target_pct"), item.get("cadence"),
              item.get("dca_daily"), item.get("tranches"), item.get("notes")))
        self.conn.commit()
        return cur.lastrowid

    def update_plan_item(self, item_id: int, **fields) -> bool:
        updates, params = [], []
        for col, val in fields.items():
            if col in self.ITEM_FIELDS and col != "plan_id" and val is not None:
                updates.append(f"{col} = ?"); params.append(val)
        if not updates:
            return False
        params.append(item_id)
        cur = self.conn.cursor()
        cur.execute(f"UPDATE investment_plan_items SET {', '.join(updates)} WHERE id = ?", params)
        self.conn.commit()
        return cur.rowcount > 0

    def delete_plan_item(self, item_id: int) -> bool:
        row = self.conn.execute("SELECT * FROM investment_plan_items WHERE id = ?", (item_id,)).fetchone()
        self._backup_rows("investment_plan_items", [row] if row else [])
        cur = self.conn.cursor()
        cur.execute("DELETE FROM investment_plan_items WHERE id = ?", (item_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def seed_plan_if_empty(self, plan: dict, items: list) -> Optional[int]:
        """库中无计划时，把既有硬编码计划写入（一次性迁移，不覆盖用户后续修改）"""
        if self.get_active_plan():
            return None
        pid = self.create_plan(plan)
        for it in items:
            it = dict(it)
            it["plan_id"] = pid
            self.add_plan_item(it)
        return pid

    # ========== 信号记录 ==========

    def save_weekly_signal(self, signal: dict):
        """保存本周信号"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO weekly_signals (
                week_start, market_temp, temp_level, action_suggestion,
                target_equity_pct, report_text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'))
        """, (
            signal.get("week_start"),
            signal.get("market_temp"),
            signal.get("temp_level"),
            signal.get("action_suggestion"),
            signal.get("target_equity_pct"),
            signal.get("report_text"),
        ))
        self.conn.commit()

    def get_recent_signals(self, limit: int = 12) -> list:
        """获取最近的周度信号"""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM weekly_signals ORDER BY week_start DESC LIMIT ?",
            (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    # ========== 工具方法 ==========

    def log_data_collection(self, data_type: str, status: str, record_count: int = 0, error_msg: str = ""):
        """记录数据采集日志"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO data_log (data_type, last_collected, status, record_count, error_msg)
            VALUES (?, datetime('now','localtime'), ?, ?, ?)
        """, (data_type, status, record_count, error_msg))
        self.conn.commit()

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
