import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from event_futures import EventSignal
from event_tracking import EventTracker, settle_due_events


NOW = 1_800_000_000.0


def signal(direction="UP", payout=80, price=100, timestamp=NOW):
    return EventSignal(verdict=direction, bias=direction, payout_pct=payout,
                       index_price=price, index_time=timestamp)


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name)/"events.db")
        self.tracker = EventTracker(self.path)

    def record(self, direction="UP", payout=80, user="u", scope="g", symbol="BTC/USDT", timestamp=NOW):
        return self.tracker.record(user, scope, symbol, signal(direction, payout, timestamp=timestamp), timestamp)[0]

    def test_wait_missing_payout_stale_and_invalid_entry_are_not_recorded(self):
        invalid = [EventSignal(), signal(payout=None), signal(timestamp=NOW-6),
                   signal(timestamp=NOW+1), signal(price=float("nan")), signal(price=0)]
        for candidate in invalid:
            self.assertIsNone(self.tracker.record("u", "g", "BTC/USDT", candidate, NOW)[0])
        self.assertEqual(self.tracker.report("u", "g")["total"], 0)

    def test_no_overlap_even_when_direction_changes(self):
        first = self.record()
        other = self.record(direction="DOWN")
        self.assertEqual(first, other)
        self.assertEqual(self.tracker.report("u", "g")["total"], 1)
        self.assertEqual(len(self.tracker.due(NOW+599)), 0)
        self.assertEqual(len(self.tracker.due(NOW+600)), 1)

    def test_settlement_wins_losses_ties_and_payout_math(self):
        for side, exit_price, status, pnl in (("UP", 101, "WIN", .87), ("UP", 99, "LOSS", -1),
                                              ("DOWN", 99, "WIN", .87), ("DOWN", 101, "LOSS", -1),
                                              ("UP", 100, "TIE", 0)):
            with self.subTest(side=side, exit_price=exit_price):
                user = f"{side}{exit_price}"
                paper = self.record(side, 87, user=user)
                self.assertEqual(self.tracker.settle(paper, {"price": exit_price, "time": NOW+602}, NOW+603), status)
                report = self.tracker.report(user, "g")
                self.assertAlmostEqual(report["pnl"], pnl)
                self.assertEqual(report["settled"], 1)
                if status == "TIE":
                    self.assertIsNone(report["win_rate"])

    def test_no_early_stale_future_or_late_settlement(self):
        paper = self.record()
        for quote, now in (({"price": 101, "time": NOW+599}, NOW+599),
                           ({"price": 101, "time": NOW+599}, NOW+601),
                           ({"price": 101, "time": NOW+606}, NOW+606),
                           ({"price": 101, "time": NOW+605}, NOW+604),
                           ({"price": float("nan"), "time": NOW+601}, NOW+601),
                           ({"price": 101, "time": NOW+600}, NOW+610)):
            self.assertIsNone(self.tracker.settle(paper, quote, now))
        self.assertEqual(self.tracker.report("u", "g")["open"], 1)

    def test_missed_expiry_is_excluded_and_cannot_be_rewritten(self):
        paper = self.record()
        self.assertEqual(self.tracker.settle(paper, {"price": 101, "time": NOW+620}, NOW+620), "MISSED")
        self.assertIsNone(self.tracker.settle(paper, {"price": 101, "time": NOW+601}, NOW+602))
        r = self.tracker.report("u", "g")
        self.assertEqual((r["missed"], r["settled"], r["pnl"]), (1, 0, 0))
        self.assertIsNone(r["win_rate"])
        self.assertIsNone(r["roi"])

    def test_restart_persistence_and_idempotence(self):
        paper = self.record()
        restarted = EventTracker(self.path)
        self.assertEqual(len(restarted.due(NOW+600)), 1)
        self.assertEqual(restarted.settle(paper, {"price": 101, "time": NOW+600}, NOW+600), "WIN")
        self.assertIsNone(restarted.settle(paper, {"price": 90, "time": NOW+601}, NOW+601))
        self.assertEqual(self.tracker.report("u", "g")["pnl"], .8)
        self.assertNotEqual(self.record(timestamp=NOW+601), paper)

    def test_reports_isolate_users_servers_symbols_and_versions(self):
        self.record()
        self.record(user="other")
        self.record(scope="other")
        self.record(symbol="ETH/USDT")
        self.assertEqual(self.tracker.report("u", "g")["total"], 2)
        self.assertEqual(self.tracker.report("u", "g", "BTC/USDT")["total"], 1)
        with self.tracker._connect() as conn:
            conn.execute("UPDATE event_papers SET version='future-version' WHERE symbol='ETH/USDT'")
        conn.close()
        self.assertEqual(self.tracker.report("u", "g")["total"], 1)

    def test_return_drawdown_streak_and_side_breakdown(self):
        for i, (side, exit_price, payout) in enumerate((("UP", 101, 80), ("UP", 99, 80),
                                                      ("DOWN", 101, 80), ("DOWN", 99, 90))):
            start = NOW+i*601
            paper = self.record(side, payout, timestamp=start)
            self.tracker.settle(paper, {"price": exit_price, "time": start+600}, start+600)
        r = self.tracker.report("u", "g")
        self.assertEqual((r["wins"], r["losses"], r["worst_loss_streak"]), (2, 2, 2))
        self.assertAlmostEqual(r["pnl"], -.3)
        self.assertAlmostEqual(r["roi"], -.075)
        self.assertAlmostEqual(r["max_drawdown"], 2)
        self.assertEqual(r["by_side"]["DOWN"]["trades"], 2)

    def test_discord_embeds_and_command_registration(self):
        # Import without opening the bot's real account databases or connecting Discord.
        with patch("paper_trading.PaperTradingManager"), patch("event_tracking.EventTracker"):
            module = importlib.import_module("bot")
        with patch.object(module, "event_tracker", self.tracker):
            paper = self.record()
            self.tracker.settle(paper, {"price": 101, "time": NOW+601}, NOW+601)
            embed = module.build_event_report_embed("u", "g")
            self.assertIn("Forward outcomes", [f.name for f in embed.fields])
            self.assertIn("1 settled", embed.fields[0].value)
            self.assertLess(len(embed), 6000)
            with patch("event_tracking.time.time", return_value=NOW+602):
                tracked = module.build_tracked_event_embed("BTC/USDT", signal(timestamp=NOW+602), "u", "g")
            self.assertIn("Automatic paper tracking", [f.name for f in tracked.fields])
            self.assertEqual(self.tracker.report("u", "g")["open"], 1)
        for name in ("event", "eventstats"):
            self.assertIsNotNone(module.bot.tree.get_command(name))
            self.assertIsNotNone(module.bot.get_command(name))


class SettlementLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_quotes_isolated_failures_and_overdue_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = EventTracker(str(Path(directory)/"events.db"))
            for user, symbol in (("a", "BTC/USDT"), ("b", "BTC/USDT"), ("c", "ETH/USDT")):
                tracker.record(user, "g", symbol, signal(), NOW)
            tracker.record("late", "g", "SOL/USDT", signal(timestamp=NOW-100), NOW-100)
            class Client:
                get_event_index_quote = AsyncMock(side_effect=[RuntimeError("BTC unavailable"),
                                                               {"price": 101, "time": NOW+601}])
            client = Client()
            await settle_due_events(tracker, client, NOW+602)
            self.assertEqual(client.get_event_index_quote.await_count, 2)
            self.assertEqual(tracker.report("a", "g")["open"], 1)
            self.assertEqual(tracker.report("c", "g")["wins"], 1)
            self.assertEqual(tracker.report("late", "g")["missed"], 1)
            await settle_due_events(tracker, client, NOW+620)
            self.assertEqual(tracker.report("a", "g")["missed"], 1)
            self.assertEqual(client.get_event_index_quote.await_count, 2)


if __name__ == "__main__":
    unittest.main()
