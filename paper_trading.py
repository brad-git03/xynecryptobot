import sqlite3
import time
import os
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass

DB_PATH = "paper_trading.db"
STARTING_BALANCE = 10000.00


@dataclass
class TrackedTrade:
    id: int
    user_id: str
    user_name: str
    channel_id: int
    symbol: str
    direction: str  # "LONG" or "SHORT"
    entry_price: float
    timeframe: str  # "1m", "5m", "1h", "4h"
    stop_loss: Optional[float]
    take_profit: Optional[float]
    highest_price: float
    lowest_price: float
    last_advice: str
    created_at: float
    last_checked_at: float
    last_notified_at: float
    is_active: int
    leverage: int = 1
    margin_usd: float = 0.0
    liquidation_price: Optional[float] = None


def calculate_liquidation_price(entry_price: float, direction: str, leverage: int = 1) -> float:
    """
    Calculates estimated futures liquidation price with ~0.5% MEXC maintenance margin rate.
    """
    if leverage <= 1:
        return 0.0 if direction.upper() == "LONG" else round(entry_price * 1.995, 4)

    mmr = 0.005  # 0.5% maintenance margin
    if direction.upper() == "LONG":
        liq = entry_price * (1.0 - (1.0 / leverage) + mmr)
        return max(0.0, round(liq, 4))
    else:  # SHORT
        liq = entry_price * (1.0 + (1.0 / leverage) - mmr)
        return round(liq, 4)


@dataclass
class Position:
    id: int
    user_id: str
    user_name: str
    symbol: str
    direction: str
    entry_price: float
    amount_usd: float
    leverage: int
    size: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    opened_at: float


@dataclass
class AccountSummary:
    user_id: str
    user_name: str
    cash_balance: float
    equity: float
    unrealized_pnl: float
    margin_used: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    total_realized_pnl: float
    best_trade_pnl: float
    worst_trade_pnl: float


class PaperTradingManager:
    """Manages virtual trading accounts, positions, auto-TP/SL, and active trade copilot sentinel."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    user_id TEXT PRIMARY KEY,
                    user_name TEXT,
                    cash_balance REAL DEFAULT 10000.0,
                    total_realized_pnl REAL DEFAULT 0.0,
                    total_trades INTEGER DEFAULT 0,
                    winning_trades INTEGER DEFAULT 0,
                    losing_trades INTEGER DEFAULT 0,
                    created_at REAL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    user_name TEXT,
                    symbol TEXT,
                    direction TEXT,
                    entry_price REAL,
                    amount_usd REAL,
                    leverage INTEGER DEFAULT 1,
                    size REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    opened_at REAL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    user_name TEXT,
                    symbol TEXT,
                    direction TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    amount_usd REAL,
                    leverage INTEGER,
                    pnl_usd REAL,
                    pnl_pct REAL,
                    close_reason TEXT,
                    opened_at REAL,
                    closed_at REAL
                )
                """
            )
            # Active Trade Copilot Tracking Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tracked_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    user_name TEXT,
                    channel_id INTEGER,
                    symbol TEXT,
                    direction TEXT,
                    entry_price REAL,
                    timeframe TEXT,
                    stop_loss REAL,
                    take_profit REAL,
                    highest_price REAL,
                    lowest_price REAL,
                    last_advice TEXT,
                    created_at REAL,
                    last_checked_at REAL,
                    last_notified_at REAL DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    leverage INTEGER DEFAULT 1,
                    margin_usd REAL DEFAULT 0.0,
                    liquidation_price REAL DEFAULT 0.0
                )
                """
            )
            # Automatic schema migration for existing DB
            for col_def in [
                ("leverage", "INTEGER DEFAULT 1"),
                ("margin_usd", "REAL DEFAULT 0.0"),
                ("liquidation_price", "REAL DEFAULT 0.0"),
            ]:
                try:
                    cursor.execute(f"ALTER TABLE tracked_trades ADD COLUMN {col_def[0]} {col_def[1]}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    def start_trade_tracking(
        self,
        user_id: str,
        user_name: str,
        channel_id: int,
        symbol: str,
        direction: str,
        entry_price: float,
        timeframe: str = "5m",
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        leverage: int = 1,
        margin_usd: float = 0.0,
    ) -> Tuple[bool, str, TrackedTrade]:
        direction = direction.upper()
        if direction not in ("LONG", "SHORT"):
            return False, "Direction must be 'LONG' or 'SHORT'.", None

        now = time.time()
        leverage = max(1, min(125, int(leverage)))
        liq_price = calculate_liquidation_price(entry_price, direction, leverage)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tracked_trades SET is_active = 0 WHERE user_id = ? AND is_active = 1", (user_id,))
            
            cursor.execute(
                """
                INSERT INTO tracked_trades (user_id, user_name, channel_id, symbol, direction, entry_price, timeframe, stop_loss, take_profit, highest_price, lowest_price, last_advice, created_at, last_checked_at, last_notified_at, is_active, leverage, margin_usd, liquidation_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    user_id,
                    user_name,
                    channel_id,
                    symbol,
                    direction,
                    entry_price,
                    timeframe,
                    stop_loss,
                    take_profit,
                    entry_price,
                    entry_price,
                    "Trade Copilot Engaged",
                    now,
                    now,
                    now,
                    leverage,
                    margin_usd,
                    liq_price,
                ),
            )
            track_id = cursor.lastrowid
            conn.commit()

            trade = TrackedTrade(
                id=track_id,
                user_id=user_id,
                user_name=user_name,
                channel_id=channel_id,
                symbol=symbol,
                direction=direction,
                entry_price=entry_price,
                timeframe=timeframe,
                stop_loss=stop_loss,
                take_profit=take_profit,
                highest_price=entry_price,
                lowest_price=entry_price,
                last_advice="Trade Copilot Engaged",
                created_at=now,
                last_checked_at=now,
                last_notified_at=now,
                is_active=1,
                leverage=leverage,
                margin_usd=margin_usd,
                liquidation_price=liq_price,
            )
            margin_str = f" (${margin_usd:,.2f} Margin)" if margin_usd > 0 else ""
            return True, f"AI Copilot is now actively watching **{symbol} {direction}** ({leverage}x{margin_str}) from entry `${entry_price:,.2f}` on `{timeframe}` timeframe!", trade

    def get_active_tracked_trade(self, user_id: str) -> Optional[TrackedTrade]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tracked_trades WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1", (user_id,))
            r = cursor.fetchone()
            if not r:
                return None
            keys = r.keys()
            return TrackedTrade(
                id=r["id"],
                user_id=r["user_id"],
                user_name=r["user_name"],
                channel_id=r["channel_id"],
                symbol=r["symbol"],
                direction=r["direction"],
                entry_price=r["entry_price"],
                timeframe=r["timeframe"],
                stop_loss=r["stop_loss"],
                take_profit=r["take_profit"],
                highest_price=r["highest_price"],
                lowest_price=r["lowest_price"],
                last_advice=r["last_advice"],
                created_at=r["created_at"],
                last_checked_at=r["last_checked_at"],
                last_notified_at=r["last_notified_at"] if "last_notified_at" in keys and r["last_notified_at"] else r["created_at"],
                is_active=r["is_active"],
                leverage=r["leverage"] if "leverage" in keys and r["leverage"] else 1,
                margin_usd=r["margin_usd"] if "margin_usd" in keys and r["margin_usd"] else 0.0,
                liquidation_price=r["liquidation_price"] if "liquidation_price" in keys and r["liquidation_price"] else None,
            )

    def get_all_active_tracked_trades(self) -> List[TrackedTrade]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tracked_trades WHERE is_active = 1 ORDER BY id DESC")
            rows = cursor.fetchall()
            trades = []
            for r in rows:
                keys = r.keys()
                trades.append(
                    TrackedTrade(
                        id=r["id"],
                        user_id=r["user_id"],
                        user_name=r["user_name"],
                        channel_id=r["channel_id"],
                        symbol=r["symbol"],
                        direction=r["direction"],
                        entry_price=r["entry_price"],
                        timeframe=r["timeframe"],
                        stop_loss=r["stop_loss"],
                        take_profit=r["take_profit"],
                        highest_price=r["highest_price"],
                        lowest_price=r["lowest_price"],
                        last_advice=r["last_advice"],
                        created_at=r["created_at"],
                        last_checked_at=r["last_checked_at"],
                        last_notified_at=r["last_notified_at"] if "last_notified_at" in keys and r["last_notified_at"] else r["created_at"],
                        is_active=r["is_active"],
                        leverage=r["leverage"] if "leverage" in keys and r["leverage"] else 1,
                        margin_usd=r["margin_usd"] if "margin_usd" in keys and r["margin_usd"] else 0.0,
                        liquidation_price=r["liquidation_price"] if "liquidation_price" in keys and r["liquidation_price"] else None,
                    )
                )
            return trades

    def update_tracked_trade_stats(self, track_id: int, current_price: float, advice: str, update_notified: bool = False):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = time.time()
            if update_notified:
                cursor.execute(
                    """
                    UPDATE tracked_trades
                    SET highest_price = MAX(highest_price, ?),
                        lowest_price = MIN(lowest_price, ?),
                        last_advice = ?,
                        last_checked_at = ?,
                        last_notified_at = ?
                    WHERE id = ?
                    """,
                    (current_price, current_price, advice, now, now, track_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE tracked_trades
                    SET highest_price = MAX(highest_price, ?),
                        lowest_price = MIN(lowest_price, ?),
                        last_advice = ?,
                        last_checked_at = ?
                    WHERE id = ?
                    """,
                    (current_price, current_price, advice, now, track_id),
                )
            conn.commit()

    def stop_trade_tracking(self, user_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tracked_trades SET is_active = 0 WHERE user_id = ? AND is_active = 1", (user_id,))
            conn.commit()
            return cursor.rowcount > 0

    def get_or_create_account(self, user_id: str, user_name: str) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM accounts WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if not row:
                now = time.time()
                cursor.execute(
                    """
                    INSERT INTO accounts (user_id, user_name, cash_balance, total_realized_pnl, total_trades, winning_trades, losing_trades, created_at)
                    VALUES (?, ?, ?, 0.0, 0, 0, 0, ?)
                    """,
                    (user_id, user_name, STARTING_BALANCE, now),
                )
                conn.commit()
                cursor.execute("SELECT * FROM accounts WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()
            else:
                if row["user_name"] != user_name:
                    cursor.execute("UPDATE accounts SET user_name = ? WHERE user_id = ?", (user_name, user_id))
                    conn.commit()
            return dict(row)

    def reset_account(self, user_id: str, user_name: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM positions WHERE user_id = ?", (user_id,))
            cursor.execute(
                """
                UPDATE accounts 
                SET cash_balance = ?, total_realized_pnl = 0.0, total_trades = 0, winning_trades = 0, losing_trades = 0
                WHERE user_id = ?
                """,
                (STARTING_BALANCE, user_id),
            )
            conn.commit()
            return True

    def open_position(
        self,
        user_id: str,
        user_name: str,
        symbol: str,
        direction: str,
        amount_usd: float,
        entry_price: float,
        leverage: int = 1,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Tuple[bool, str, Optional[Position]]:
        account = self.get_or_create_account(user_id, user_name)
        cash = float(account["cash_balance"])

        if amount_usd <= 0:
            return False, "Trade amount must be greater than $0.", None

        if amount_usd > cash:
            return False, f"Insufficient funds. Available cash: `${cash:,.2f}`, Requested margin: `${amount_usd:,.2f}`", None

        if entry_price <= 0:
            return False, "Invalid market entry price.", None

        leverage = max(1, min(100, leverage))
        direction = direction.upper()
        if direction not in ("LONG", "SHORT"):
            return False, "Direction must be 'LONG' or 'SHORT'.", None

        if stop_loss is not None and stop_loss > 0:
            if direction == "LONG" and stop_loss >= entry_price:
                return False, f"For LONG positions, Stop Loss (${stop_loss:,.2f}) must be BELOW entry price (${entry_price:,.2f}).", None
            if direction == "SHORT" and stop_loss <= entry_price:
                return False, f"For SHORT positions, Stop Loss (${stop_loss:,.2f}) must be ABOVE entry price (${entry_price:,.2f}).", None

        if take_profit is not None and take_profit > 0:
            if direction == "LONG" and take_profit <= entry_price:
                return False, f"For LONG positions, Take Profit (${take_profit:,.2f}) must be ABOVE entry price (${entry_price:,.2f}).", None
            if direction == "SHORT" and take_profit >= entry_price:
                return False, f"For SHORT positions, Take Profit (${take_profit:,.2f}) must be BELOW entry price (${entry_price:,.2f}).", None

        notional_value = amount_usd * leverage
        size = notional_value / entry_price
        now = time.time()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            new_cash = cash - amount_usd
            cursor.execute("UPDATE accounts SET cash_balance = ? WHERE user_id = ?", (new_cash, user_id))

            cursor.execute(
                """
                INSERT INTO positions (user_id, user_name, symbol, direction, entry_price, amount_usd, leverage, size, stop_loss, take_profit, opened_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    user_name,
                    symbol,
                    direction,
                    entry_price,
                    amount_usd,
                    leverage,
                    size,
                    stop_loss,
                    take_profit,
                    now,
                ),
            )
            pos_id = cursor.lastrowid
            conn.commit()

            pos = Position(
                id=pos_id,
                user_id=user_id,
                user_name=user_name,
                symbol=symbol,
                direction=direction,
                entry_price=entry_price,
                amount_usd=amount_usd,
                leverage=leverage,
                size=size,
                stop_loss=stop_loss,
                take_profit=take_profit,
                opened_at=now,
            )
            return True, f"Successfully opened **{direction}** on **{symbol}** with ${amount_usd:,.2f} at ${entry_price:,.2f} ({leverage}x leverage)!", pos

    def update_tpsl(
        self,
        position_id: int,
        user_id: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Tuple[bool, str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions WHERE id = ? AND user_id = ?", (position_id, user_id))
            row = cursor.fetchone()
            if not row:
                return False, f"Position #{position_id} was not found in your open trades."

            direction = row["direction"]
            entry_price = float(row["entry_price"])

            if stop_loss is not None and stop_loss > 0:
                if direction == "LONG" and stop_loss >= entry_price:
                    return False, f"For LONG, Stop Loss must be BELOW entry (${entry_price:,.2f})."
                if direction == "SHORT" and stop_loss <= entry_price:
                    return False, f"For SHORT, Stop Loss must be ABOVE entry (${entry_price:,.2f})."

            if take_profit is not None and take_profit > 0:
                if direction == "LONG" and take_profit <= entry_price:
                    return False, f"For LONG, Take Profit must be ABOVE entry (${entry_price:,.2f})."
                if direction == "SHORT" and take_profit >= entry_price:
                    return False, f"For SHORT, Take Profit must be BELOW entry (${entry_price:,.2f})."

            cursor.execute(
                "UPDATE positions SET stop_loss = COALESCE(?, stop_loss), take_profit = COALESCE(?, take_profit) WHERE id = ?",
                (stop_loss, take_profit, position_id),
            )
            conn.commit()
            return True, f"Updated Position #{position_id} targets: SL=`${stop_loss if stop_loss else row['stop_loss']}` | TP=`${take_profit if take_profit else row['take_profit']}`"

    def get_open_positions(self, user_id: Optional[str] = None) -> List[Position]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if user_id:
                cursor.execute("SELECT * FROM positions WHERE user_id = ? ORDER BY opened_at DESC", (user_id,))
            else:
                cursor.execute("SELECT * FROM positions ORDER BY opened_at DESC")
            rows = cursor.fetchall()
            positions = []
            for r in rows:
                positions.append(
                    Position(
                        id=r["id"],
                        user_id=r["user_id"],
                        user_name=r["user_name"],
                        symbol=r["symbol"],
                        direction=r["direction"],
                        entry_price=r["entry_price"],
                        amount_usd=r["amount_usd"],
                        leverage=r["leverage"],
                        size=r["size"],
                        stop_loss=r["stop_loss"],
                        take_profit=r["take_profit"],
                        opened_at=r["opened_at"],
                    )
                )
            return positions

    def calculate_position_pnl(self, position: Position, current_price: float) -> Tuple[float, float]:
        if position.direction == "LONG":
            price_diff = current_price - position.entry_price
        else:
            price_diff = position.entry_price - current_price

        pnl_usd = price_diff * position.size
        pnl_pct = (pnl_usd / position.amount_usd) * 100.0
        return pnl_usd, pnl_pct

    def close_position(
        self,
        position_id: int,
        exit_price: float,
        reason: str = "MANUAL",
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions WHERE id = ?", (position_id,))
            row = cursor.fetchone()
            if not row:
                return False, f"Position #{position_id} not found.", None

            pos = Position(
                id=row["id"],
                user_id=row["user_id"],
                user_name=row["user_name"],
                symbol=row["symbol"],
                direction=row["direction"],
                entry_price=row["entry_price"],
                amount_usd=row["amount_usd"],
                leverage=row["leverage"],
                size=row["size"],
                stop_loss=row["stop_loss"],
                take_profit=row["take_profit"],
                opened_at=row["opened_at"],
            )

            pnl_usd, pnl_pct = self.calculate_position_pnl(pos, exit_price)
            returned_funds = max(0.0, pos.amount_usd + pnl_usd)
            now = time.time()

            is_win = pnl_usd > 0
            cursor.execute("SELECT * FROM accounts WHERE user_id = ?", (pos.user_id,))
            acc = cursor.fetchone()
            current_cash = float(acc["cash_balance"])
            new_cash = current_cash + returned_funds
            new_realized_pnl = float(acc["total_realized_pnl"]) + pnl_usd
            total_trades = int(acc["total_trades"]) + 1
            winning_trades = int(acc["winning_trades"]) + (1 if is_win else 0)
            losing_trades = int(acc["losing_trades"]) + (0 if is_win else 1)

            cursor.execute(
                """
                UPDATE accounts
                SET cash_balance = ?, total_realized_pnl = ?, total_trades = ?, winning_trades = ?, losing_trades = ?
                WHERE user_id = ?
                """,
                (new_cash, new_realized_pnl, total_trades, winning_trades, losing_trades, pos.user_id),
            )

            cursor.execute(
                """
                INSERT INTO trade_history (user_id, user_name, symbol, direction, entry_price, exit_price, amount_usd, leverage, pnl_usd, pnl_pct, close_reason, opened_at, closed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pos.user_id,
                    pos.user_name,
                    pos.symbol,
                    pos.direction,
                    pos.entry_price,
                    exit_price,
                    pos.amount_usd,
                    pos.leverage,
                    pnl_usd,
                    pnl_pct,
                    reason,
                    pos.opened_at,
                    now,
                ),
            )

            cursor.execute("DELETE FROM positions WHERE id = ?", (position_id,))
            conn.commit()

            trade_summary = {
                "symbol": pos.symbol,
                "direction": pos.direction,
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "amount_usd": pos.amount_usd,
                "leverage": pos.leverage,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "reason": reason,
                "user_id": pos.user_id,
            }
            return True, f"Closed #{position_id} {pos.symbol} {pos.direction} at ${exit_price:,.2f} | PnL: ${pnl_usd:+,.2f} ({pnl_pct:+.2f}%)", trade_summary

    def get_portfolio_summary(
        self, user_id: str, user_name: str, live_prices: Dict[str, float]
    ) -> AccountSummary:
        account = self.get_or_create_account(user_id, user_name)
        positions = self.get_open_positions(user_id)

        cash_balance = float(account["cash_balance"])
        margin_used = sum(p.amount_usd for p in positions)

        unrealized_pnl = 0.0
        for p in positions:
            curr_p = live_prices.get(p.symbol, p.entry_price)
            pnl_u, _ = self.calculate_position_pnl(p, curr_p)
            unrealized_pnl += pnl_u

        equity = cash_balance + margin_used + unrealized_pnl
        total_trades = int(account["total_trades"])
        winning_trades = int(account["winning_trades"])
        losing_trades = int(account["losing_trades"])
        win_rate = (winning_trades / total_trades * 100.0) if total_trades > 0 else 0.0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(pnl_usd) as best, MIN(pnl_usd) as worst FROM trade_history WHERE user_id = ?", (user_id,))
            stat_row = cursor.fetchone()
            best_trade = float(stat_row["best"]) if stat_row and stat_row["best"] is not None else 0.0
            worst_trade = float(stat_row["worst"]) if stat_row and stat_row["worst"] is not None else 0.0

        return AccountSummary(
            user_id=user_id,
            user_name=user_name,
            cash_balance=cash_balance,
            equity=equity,
            unrealized_pnl=unrealized_pnl,
            margin_used=margin_used,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate_pct=win_rate,
            total_realized_pnl=float(account["total_realized_pnl"]),
            best_trade_pnl=best_trade,
            worst_trade_pnl=worst_trade,
        )

    def get_trade_history(self, user_id: str, limit: int = 8) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM trade_history WHERE user_id = ? ORDER BY closed_at DESC LIMIT ?",
                (user_id, limit),
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_leaderboard(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, user_name, cash_balance,
                       total_realized_pnl, total_trades, winning_trades, losing_trades
                FROM accounts
                WHERE total_trades > 0
                ORDER BY total_realized_pnl DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(r) for r in cursor.fetchall()]
