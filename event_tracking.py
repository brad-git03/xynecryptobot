"""Persistent forward paper results. Missed expiry quotes never become wins/losses."""

from contextlib import closing
from dataclasses import asdict
import json
import math
import sqlite3
import time

from event_futures import EXPIRY_MINUTES, EventSignal, break_even_rate, wilson_lower


STRATEGY_VERSION = "event-v1"
ENTRY_MAX_AGE = 5.0
SETTLEMENT_WINDOW = 5.0
SETTLEMENT_DEADLINE = 15.0


class EventTracker:
    def __init__(self, db_path="event_tracking.db"):
        self.db_path = db_path
        with closing(self._connect()) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS event_papers (
                id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, scope_id TEXT NOT NULL,
                symbol TEXT NOT NULL, version TEXT NOT NULL, direction TEXT NOT NULL,
                entry_price REAL NOT NULL, entry_time REAL NOT NULL, entry_quote_time REAL NOT NULL,
                expires_at REAL NOT NULL, payout_pct REAL NOT NULL, snapshot TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN', exit_price REAL, exit_quote_time REAL,
                pnl REAL, completed_at REAL)""")
            conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS event_one_open
                ON event_papers(user_id, scope_id, symbol) WHERE status = 'OPEN'""")
            conn.execute("CREATE INDEX IF NOT EXISTS event_due ON event_papers(status, expires_at)")

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, user_id, scope_id, symbol, signal: EventSignal, now=None):
        now = time.time() if now is None else now
        if signal.verdict not in ("UP", "DOWN"):
            return None, "WAIT signals do not open paper trades."
        if signal.payout_pct is None:
            return None, "Paper tracking needs a payout."
        break_even_rate(signal.payout_pct)
        if (signal.index_price is None or not math.isfinite(signal.index_price) or signal.index_price <= 0
                or signal.index_time is None or not math.isfinite(signal.index_time)
                or not 0 <= now-signal.index_time <= ENTRY_MAX_AGE):
            return None, "Not paper-tracked: entry index quote must be no more than 5 seconds old."
        with closing(self._connect()) as conn, conn:
            # Serialize deduplication with completion; opposite directions also cannot overlap.
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("""SELECT id FROM event_papers
                WHERE user_id=? AND scope_id=? AND symbol=? AND status='OPEN'""",
                (str(user_id), str(scope_id), symbol)).fetchone()
            if existing:
                return existing["id"], f"Already tracking paper #{existing['id']} for this pair; no duplicate entry."
            cursor = conn.execute("""INSERT INTO event_papers
                (user_id, scope_id, symbol, version, direction, entry_price, entry_time,
                 entry_quote_time, expires_at, payout_pct, snapshot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(user_id), str(scope_id), symbol, STRATEGY_VERSION, signal.verdict,
                 signal.index_price, now, signal.index_time, now+EXPIRY_MINUTES*60,
                 signal.payout_pct, json.dumps(asdict(signal))))
            paper_id = cursor.lastrowid
        return paper_id, f"Paper #{paper_id} opened: 1 USDT virtual stake, 10-minute expiry. Use !eventstats for results."

    def due(self, now=None):
        now = time.time() if now is None else now
        with closing(self._connect()) as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM event_papers WHERE status='OPEN' AND expires_at<=? ORDER BY expires_at", (now,))]

    def settle(self, paper_id, quote=None, now=None):
        """Use the first observed fresh index at/after expiry within 5s, or mark MISSED.

        This is an explicitly approximate paper fill, not the exchange's exact settlement.
        """
        now = time.time() if now is None else now
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM event_papers WHERE id=? AND status='OPEN'", (paper_id,)).fetchone()
            if row is None or now < row["expires_at"]:
                return None
            valid = False
            if quote:
                price, stamp = quote.get("price"), quote.get("time")
                valid = (isinstance(price, (int, float)) and math.isfinite(price) and price > 0
                         and isinstance(stamp, (int, float)) and math.isfinite(stamp)
                         and 0 <= now-stamp <= ENTRY_MAX_AGE
                         and 0 <= stamp-row["expires_at"] <= SETTLEMENT_WINDOW
                         and now <= row["expires_at"]+SETTLEMENT_DEADLINE)
            if valid:
                move = (price-row["entry_price"])*(1 if row["direction"] == "UP" else -1)
                status = "WIN" if move > 0 else ("LOSS" if move < 0 else "TIE")
                pnl = row["payout_pct"]/100 if move > 0 else (-1.0 if move < 0 else 0.0)
                conn.execute("""UPDATE event_papers SET status=?, exit_price=?, exit_quote_time=?,
                    pnl=?, completed_at=? WHERE id=? AND status='OPEN'""",
                    (status, price, stamp, pnl, now, paper_id))
                return status
            if now > row["expires_at"]+SETTLEMENT_DEADLINE:
                conn.execute("UPDATE event_papers SET status='MISSED', completed_at=? WHERE id=? AND status='OPEN'",
                             (now, paper_id))
                return "MISSED"
        return None

    def report(self, user_id, scope_id, symbol=None):
        with closing(self._connect()) as conn:
            query = "SELECT * FROM event_papers WHERE user_id=? AND scope_id=? AND version=?"
            args = [str(user_id), str(scope_id), STRATEGY_VERSION]
            if symbol:
                query += " AND symbol=?"
                args.append(symbol)
            rows = [dict(r) for r in conn.execute(query+" ORDER BY entry_time, id", args)]
        settled = [r for r in rows if r["status"] in ("WIN", "LOSS", "TIE")]
        wins = sum(r["status"] == "WIN" for r in settled)
        losses = sum(r["status"] == "LOSS" for r in settled)
        ties = len(settled)-wins-losses
        equity = peak = drawdown = 0.0
        streak = worst_streak = 0
        for r in settled:
            equity += r["pnl"]
            peak = max(peak, equity)
            drawdown = max(drawdown, peak-equity)
            streak = streak+1 if r["status"] == "LOSS" else 0
            worst_streak = max(worst_streak, streak)
        by_side = {}
        for side in ("UP", "DOWN"):
            group = [r for r in settled if r["direction"] == side]
            by_side[side] = {"trades": len(group), "wins": sum(r["status"] == "WIN" for r in group),
                             "losses": sum(r["status"] == "LOSS" for r in group),
                             "pnl": sum(r["pnl"] for r in group)}
        return {"total": len(rows), "settled": len(settled), "wins": wins, "losses": losses, "ties": ties,
                "open": sum(r["status"] == "OPEN" for r in rows),
                "missed": sum(r["status"] == "MISSED" for r in rows),
                "win_rate": wins/(wins+losses) if wins+losses else None,
                "lower_win_rate": wilson_lower(wins, wins+losses) if wins+losses else None,
                "pnl": equity, "roi": equity/len(settled) if settled else None,
                "max_drawdown": drawdown, "worst_loss_streak": worst_streak,
                "by_side": by_side, "recent": list(reversed(rows[-5:]))}


async def settle_due_events(tracker, client, now=None):
    """Share one public quote across due entries for a symbol; isolate symbol failures."""
    clock = time.time if now is None else lambda: now
    pending = tracker.due(clock())
    groups = {}
    for row in pending:
        if clock() > row["expires_at"]+SETTLEMENT_DEADLINE:
            tracker.settle(row["id"], now=clock())
        else:
            groups.setdefault(row["symbol"], []).append(row)
    for symbol, rows in groups.items():
        try:
            quote = await client.get_event_index_quote(symbol)
        except Exception:
            quote = None
        for row in rows:
            tracker.settle(row["id"], quote, clock())
