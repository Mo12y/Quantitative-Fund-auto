"""
数据库模块: SQLite 数据库的创建、读写操作。

所有数据存储在单个 SQLite 文件中，无需安装数据库服务。
"""

import sqlite3
import os
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

        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row  # 让查询结果可以通过列名访问
        self._create_tables()

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

        self.conn.commit()

    # ========== 基金信息操作 ==========

    def upsert_fund_info(self, fund: dict):
        """
        插入或更新基金基本信息。

        Args:
            fund: 基金信息字典
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO fund_info (
                fund_code, fund_name, fund_type, establish_date,
                fund_size, mgt_fee, custodian_fee, purchase_fee,
                redeem_fee, manager_name, manager_tenure,
                company_name, purchase_status,
                risk_level, investment_style, benchmark, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(fund_code) DO UPDATE SET
                fund_name=excluded.fund_name,
                fund_type=excluded.fund_type,
                fund_size=excluded.fund_size,
                mgt_fee=excluded.mgt_fee,
                manager_name=excluded.manager_name,
                manager_tenure=excluded.manager_tenure,
                purchase_status=excluded.purchase_status,
                establish_date=excluded.establish_date,
                company_name=excluded.company_name,
                benchmark=excluded.benchmark,
                updated_at=datetime('now','localtime')
        """, (
            fund.get("fund_code"),
            fund.get("fund_name"),
            fund.get("fund_type"),
            fund.get("establish_date"),
            fund.get("fund_size"),
            fund.get("mgt_fee"),
            fund.get("custodian_fee"),
            fund.get("purchase_fee"),
            fund.get("redeem_fee"),
            fund.get("manager_name"),
            fund.get("manager_tenure"),
            fund.get("company_name"),
            fund.get("purchase_status"),
            fund.get("risk_level"),
            fund.get("investment_style"),
            fund.get("benchmark"),
        ))
        self.conn.commit()

    def get_all_funds(self) -> list:
        """获取所有基金基本信息"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM fund_info ORDER BY fund_code")
        return [dict(row) for row in cursor.fetchall()]

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
        cursor = self.conn.cursor()
        cursor.executemany("""
            INSERT OR IGNORE INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return)
            VALUES (?, ?, ?, ?, ?)
        """, nav_records)
        self.conn.commit()

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

    def get_latest_nav_date(self) -> Optional[str]:
        """获取最新的净值日期"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT MAX(nav_date) as latest FROM fund_nav")
        row = cursor.fetchone()
        return row["latest"] if row else None

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

    # ========== 持仓操作 ==========

    def add_holding(self, holding: dict):
        """添加一条持仓记录"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO holdings (fund_code, fund_name, buy_date, buy_amount, buy_nav, shares, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            holding.get("fund_code"),
            holding.get("fund_name"),
            holding.get("buy_date"),
            holding.get("buy_amount"),
            holding.get("buy_nav"),
            holding.get("shares"),
            holding.get("notes", ""),
        ))
        self.conn.commit()

    def get_current_holdings(self) -> list:
        """获取当前持仓（status='holding'）"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM holdings WHERE status = 'holding' ORDER BY buy_date")
        return [dict(row) for row in cursor.fetchall()]

    def sell_holding(self, holding_id: int, sell_date: str, sell_amount: float):
        """标记持仓已卖出"""
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE holdings SET status='sold', sell_date=?, sell_amount=?
            WHERE id=?
        """, (sell_date, sell_amount, holding_id))
        self.conn.commit()

    def get_total_invested(self) -> float:
        """计算总投资金额"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COALESCE(SUM(buy_amount), 0) as total FROM holdings WHERE status='holding'")
        return cursor.fetchone()["total"]

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
