import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import pandas as pd

from event_alerts import EventAlerts, poll_event_alerts
from event_futures import EVENT_HORIZONS, EventSignal, analyze_event
from mexc_client import MEXCClient
from test_event_futures import NOW, history, features_up, Response


def reading(side="UP", verdict="UP", ready=True):
    return EventSignal(bias=side, verdict=verdict, data_ready=ready, index_price=100,
                       index_time=NOW, reason="Test signal", payout_pct=80)


class HorizonTests(unittest.TestCase):
    @patch("event_futures._features", side_effect=features_up)
    def test_each_horizon_uses_correct_nonoverlapping_future_labels(self, _):
        for horizon, (expiry, candle) in EVENT_HORIZONS.items():
            with self.subTest(horizon=horizon):
                df = history(2000)
                df["open_time"] = (NOW-2000*candle*60+pd.Series(range(2000))*candle*60)*1000
                r = analyze_event(df, df.close.iloc[-1], NOW, 80, 80, NOW, expiry, candle)
                bars = expiry//candle
                expected = len(range(50, 2000-bars, bars))
                self.assertEqual(r.samples, expected)
                self.assertEqual(r.wins, expected)
                self.assertEqual(r.expiry_minutes, expiry)
                self.assertTrue(r.data_ready)
                self.assertEqual(r.verdict, "UP")

    def test_wrong_candle_spacing_and_invalid_horizon_wait_or_reject(self):
        df = history()
        self.assertIn("gaps", analyze_event(df, 104.99, NOW, 80, 80, NOW, 60, 5).reason)
        with self.assertRaises(ValueError):
            analyze_event(df, 104.99, NOW, 80, 80, NOW, 1440, 1)


class AlertStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name)/"alerts.db")
        self.store = EventAlerts(self.path)

    def test_enable_all_persist_update_and_disable_owned_watches(self):
        self.assertEqual(self.store.enable("u", "g", 123, "BTC/USDT", "all", 80, 75, NOW), list(EVENT_HORIZONS))
        self.assertEqual(len(EventAlerts(self.path).active()), 4)
        first = self.store.active()[0]
        self.store.mark_sent(first, "UP:UP:1", NOW)
        self.store.enable("u", "g", 124, "BTC/USDT", "10m", 85, now=NOW+1)
        updated = self.store.active()[0]
        self.assertEqual(updated["down_payout"], 85)
        self.assertEqual(updated["channel_id"], 124)
        self.assertEqual(updated["last_state"], "")
        self.assertFalse(self.store.is_current(first))
        self.store.enable("other", "g", 123, "BTC/USDT", "10m")
        self.store.enable("u", "other", 123, "BTC/USDT", "10m")
        self.assertEqual(self.store.disable("u", "g", horizon="30m"), 1)
        self.assertEqual(self.store.disable("u", "g"), 3)
        self.assertEqual(len(self.store.active()), 2)

    def test_invalid_settings_and_watch_limit(self):
        for horizon, payout in (("5m", 80), ("10m", float("nan")), ("10m", 0)):
            with self.assertRaises(ValueError):
                self.store.enable("u", "g", 1, "BTC/USDT", horizon, payout)
        for i in range(4):
            self.store.enable("u", "g", 1, f"PAIR{i}/USDT")
        with self.assertRaises(ValueError):
            self.store.enable("u", "g", 1, "EXTRA/USDT")
        self.assertEqual(len(self.store.active()), 16)


class AlertPollingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = EventAlerts(str(Path(self.temp.name)/"alerts.db"))
        self.store.enable("u", "g", 123, "BTC/USDT", "10m", 80, now=NOW)
        self.client = type("Client", (), {})()
        self.client.get_event_index_history = AsyncMock(return_value=None)
        self.client.get_event_index_quote = AsyncMock(return_value={"price": 100, "time": NOW})
        self.send = AsyncMock()

    async def poll(self, signal=None, at=NOW):
        with patch("event_alerts.analyze_event", return_value=signal or reading()):
            return await poll_event_alerts(self.store, self.client, self.send, at)

    async def test_initial_then_quiet_unchanged_and_cooldown(self):
        await self.poll()
        await self.poll(at=NOW+60)
        await self.poll(reading("DOWN", "DOWN"), NOW+60)
        self.assertEqual(self.send.await_count, 1)
        await self.poll(reading("DOWN", "DOWN"), NOW+301)
        self.assertEqual(self.send.await_count, 2)

    async def test_withdraw_entry_approval_immediately(self):
        await self.poll()
        await self.poll(reading("UP", "WAIT"), NOW+60)
        self.assertEqual(self.send.await_count, 2)

    async def test_shared_fetch_for_identical_pair_horizon(self):
        self.store.enable("other", "g", 123, "BTC/USDT", "10m", 80, now=NOW)
        await self.poll()
        self.assertEqual(self.client.get_event_index_history.await_count, 1)
        self.assertEqual(self.client.get_event_index_quote.await_count, 1)
        self.assertEqual(self.send.await_count, 2)

    async def test_failed_send_is_retried_not_marked_sent(self):
        self.send.side_effect = RuntimeError("Channel unavailable")
        errors = await self.poll()
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.store.active()[0]["last_state"], "")
        self.send.side_effect = None
        await self.poll(at=NOW+60)
        self.assertEqual(self.send.await_count, 2)

    async def test_disabled_during_fetch_does_not_send(self):
        async def fetch(*args, **kwargs):
            self.store.disable("u", "g")
        self.client.get_event_index_history.side_effect = fetch
        await self.poll()
        self.send.assert_not_awaited()

    async def test_payout_assumptions_expire_and_horizon_is_passed(self):
        self.store.enable("u", "g", 123, "ETH/USDT", "1d", 80, 70, NOW)
        with patch("event_alerts.analyze_event", return_value=reading(verdict="WAIT")) as analyze:
            await poll_event_alerts(self.store, self.client, self.send, NOW+3601)
        for call in analyze.call_args_list:
            self.assertEqual(call.args[3:5], (None, None))
        self.assertEqual(analyze.call_args.kwargs["expiry_minutes"], 1440)
        self.assertEqual(analyze.call_args.kwargs["candle_minutes"], 60)
        self.assertFalse(self.send.call_args.args[2])
        self.assertIn("expired", self.send.call_args.args[1].reason)

    async def test_fetch_failure_withdraws_approval_without_affecting_other_pairs(self):
        await self.poll()
        self.store.enable("u", "g", 123, "ETH/USDT", "30m", now=NOW)
        self.client.get_event_index_history.side_effect = [RuntimeError("Unavailable"), None]
        errors = await poll_event_alerts(self.store, self.client, self.send, NOW+60)
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.send.await_count, 3)
        self.assertEqual(self.send.call_args_list[1].args[1].verdict, "WAIT")

    async def test_client_uses_requested_index_interval(self):
        client = MEXCClient()
        class Session:
            closed = False
            calls = []
            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response({"success": False})
        session = Session()
        client._session = session
        await client.get_event_index_history("BTC", interval="1h")
        self.assertEqual(session.calls[0][1]["params"]["interval"], "Min60")
        self.assertIn("index_price", session.calls[0][0])
        with self.assertRaises(ValueError):
            await client.get_event_index_history("BTC", interval="1d")

    async def test_bot_toggle_registration_settings_and_delivery(self):
        with patch("paper_trading.PaperTradingManager"), patch("event_tracking.EventTracker"), patch("event_alerts.EventAlerts"):
            module = importlib.import_module("bot")
        with patch.object(module, "event_alert_settings", self.store):
            msg = module.configure_event_alerts("on", 123, 456, 789, "BTC", "all", None, None)
            self.assertIn("10m, 30m, 1h, 1d", msg)
            self.assertIn("bias only", module.configure_event_alerts("status", 123, 456, 789, None, "all", None, None))
            row = self.store.active(123, 456)[0]
            channel = type("Channel", (), {"send": AsyncMock()})()
            r = reading(verdict="WAIT")
            r.expiry_minutes = 1440
            row["horizon"] = "1d"
            with patch.object(module.bot, "get_channel", return_value=channel):
                await module.send_event_bias_alert(row, r, False)
            self.assertIn("1d", channel.send.call_args.kwargs["content"])
            embed = channel.send.call_args.kwargs["embed"]
            self.assertLess(len(embed), 6000)
            self.assertIn("1 day", " ".join(f.value for f in embed.fields))
            self.assertIn("Turned off 4", module.configure_event_alerts("off", 123, 456, 789, None, "all", None, None))
        self.assertIsNotNone(module.bot.get_command("eventalerts"))
        self.assertIsNotNone(module.bot.tree.get_command("eventalerts"))


if __name__ == "__main__":
    unittest.main()
