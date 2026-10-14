"""Persistent opt-in bias notifications; unchanged readings stay quiet."""

from contextlib import closing
import sqlite3
import time

from event_futures import EVENT_HORIZONS, analyze_event, break_even_rate


MIN_ALERT_INTERVAL = 300
PAYOUT_ASSUMPTION_LIFETIME = 3600


class EventAlerts:
    def __init__(self, db_path="event_alerts.db"):
        self.db_path = db_path
        with closing(self._connect()) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS event_alerts (
                id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, scope_id TEXT NOT NULL,
                channel_id INTEGER NOT NULL, symbol TEXT NOT NULL, horizon TEXT NOT NULL,
                up_payout REAL, down_payout REAL, updated_at REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                revision INTEGER NOT NULL DEFAULT 1, last_state TEXT NOT NULL DEFAULT '',
                last_sent REAL NOT NULL DEFAULT 0,
                UNIQUE(user_id, scope_id, symbol, horizon))""")

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def enable(self, user_id, scope_id, channel_id, symbol, horizon="all", payout=None, down_payout=None, now=None):
        now = time.time() if now is None else now
        horizon = horizon.lower()
        horizons = list(EVENT_HORIZONS) if horizon == "all" else [horizon]
        if any(h not in EVENT_HORIZONS for h in horizons):
            raise ValueError("Horizon must be 10m, 30m, 1h, 1d or all.")
        for value in (payout, down_payout):
            if value is not None:
                break_even_rate(value)
        down_payout = payout if down_payout is None else down_payout
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = {(r["symbol"], r["horizon"]) for r in conn.execute(
                "SELECT symbol,horizon FROM event_alerts WHERE user_id=? AND scope_id=? AND active=1",
                (str(user_id), str(scope_id)))}
            if len(existing | {(symbol, h) for h in horizons}) > 16:
                raise ValueError("Limit: 16 active pair/horizon alerts per user in this server. Turn some off first.")
            for h in horizons:
                conn.execute("""INSERT INTO event_alerts
                    (user_id, scope_id, channel_id, symbol, horizon, up_payout, down_payout, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id,scope_id,symbol,horizon) DO UPDATE SET
                    channel_id=excluded.channel_id, up_payout=excluded.up_payout, down_payout=excluded.down_payout,
                    updated_at=excluded.updated_at, active=1, revision=event_alerts.revision+1,
                    last_state='', last_sent=0""",
                    (str(user_id), str(scope_id), channel_id, symbol, h, payout, down_payout, now))
        return horizons

    def disable(self, user_id, scope_id, symbol=None, horizon="all"):
        if horizon != "all" and horizon not in EVENT_HORIZONS:
            raise ValueError("Horizon must be 10m, 30m, 1h, 1d or all.")
        query = "UPDATE event_alerts SET active=0,revision=revision+1 WHERE user_id=? AND scope_id=? AND active=1"
        args = [str(user_id), str(scope_id)]
        if symbol:
            query += " AND symbol=?"
            args.append(symbol)
        if horizon != "all":
            query += " AND horizon=?"
            args.append(horizon)
        with closing(self._connect()) as conn, conn:
            return conn.execute(query, args).rowcount

    def active(self, user_id=None, scope_id=None):
        query, args = "SELECT * FROM event_alerts WHERE active=1", []
        if user_id is not None:
            query += " AND user_id=? AND scope_id=?"
            args = [str(user_id), str(scope_id)]
        with closing(self._connect()) as conn:
            return [dict(r) for r in conn.execute(query+" ORDER BY id", args)]

    def is_current(self, row):
        with closing(self._connect()) as conn:
            return conn.execute("SELECT 1 FROM event_alerts WHERE id=? AND revision=? AND active=1",
                                (row["id"], row["revision"])).fetchone() is not None

    def mark_sent(self, row, state, now):
        with closing(self._connect()) as conn, conn:
            conn.execute("""UPDATE event_alerts SET last_state=?,last_sent=?
                WHERE id=? AND revision=? AND active=1""", (state, now, row["id"], row["revision"]))


def alert_state(signal):
    return f"{signal.bias}:{signal.verdict}:{int(signal.data_ready)}"


async def poll_event_alerts(store, client, send, now=None):
    """Send the initial state, then changes only, at most once per watch every 5m."""
    clock = time.time if now is None else lambda: now
    groups = {}
    for row in store.active():
        groups.setdefault((row["symbol"], row["horizon"]), []).append(row)
    errors = []
    for (symbol, horizon), rows in groups.items():
        expiry, candle = EVENT_HORIZONS[horizon]
        interval = {1: "1m", 5: "5m", 60: "1h"}[candle]
        try:
            history = await client.get_event_index_history(symbol, interval=interval)
            quote = await client.get_event_index_quote(symbol)
        except Exception as exc:
            # Missing data is a state change, never a silent continuing entry approval.
            history, quote = None, None
            errors.append(f"{symbol}/{horizon}: {exc}")
        for row in rows:
            current = clock()
            payouts_valid = 0 <= current-row["updated_at"] <= PAYOUT_ASSUMPTION_LIFETIME
            signal = analyze_event(history, quote["price"] if quote else None, quote["time"] if quote else None,
                                   row["up_payout"] if payouts_valid else None,
                                   row["down_payout"] if payouts_valid else None,
                                   now=current, expiry_minutes=expiry, candle_minutes=candle)
            if not payouts_valid and signal.data_ready:
                signal.reason = "Saved payout assumptions expired after 1 hour. Bias only; update payouts to assess entry."
            state = alert_state(signal)
            withdrawal = (row["last_state"] and row["last_state"].split(":")[1] in ("UP", "DOWN")
                          and signal.verdict == "WAIT")
            if (state == row["last_state"] or
                    (row["last_state"] and not withdrawal and current-row["last_sent"] < MIN_ALERT_INTERVAL)
                    or not store.is_current(row)):
                continue
            try:
                await send(row, signal, payouts_valid)
            except Exception as exc:
                errors.append(f"Delivery for alert #{row['id']}: {exc}")
                continue
            store.mark_sent(row, state, clock())
    return errors
