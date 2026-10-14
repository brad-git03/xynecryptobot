import importlib
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from event_direction import CHECK_STRATEGY_VERSION, check_direction, normalize_direction, summarize_pressure
from event_futures import EventSignal
from event_tracking import EventTracker
from mexc_client import MEXCClient
from test_event_futures import NOW, Response


def base(side="UP", verdict=None):
    return EventSignal(verdict=side if verdict is None else verdict, bias=side, payout_pct=80,
                       index_price=100, index_time=NOW, samples=50, wins=40, losses=10,
                       lower_win_rate=.66, historical_ev=.44)


def pressure(bid=65, buy=70):
    return {"bid_pct": bid, "taker_buy_pct": buy, "trade_count": 25,
            "book_time": NOW, "latest_trade_time": NOW}


def trades(count=25, side=1, timestamp=NOW):
    return [{"t": timestamp*1000, "v": 2, "T": side, "M": 2} for _ in range(count)]


class DirectionTests(unittest.TestCase):
    def test_normalize_and_reject_invalid_request(self):
        self.assertEqual(normalize_direction(" up "), "UP")
        with self.assertRaises(ValueError):
            normalize_direction("long")
        with self.assertRaises(ValueError):
            check_direction(base(), "UP", float("nan"), pressure(), NOW)

    def test_both_sides_can_pass_with_agreeing_pressure(self):
        for side, context in (("UP", pressure()), ("DOWN", pressure(35, 30))):
            result = check_direction(base(side), side, 80, context, NOW)
            self.assertEqual(result.verdict, side)
            self.assertEqual(result.strategy_version, CHECK_STRATEGY_VERSION)
            self.assertEqual(result.research_context["requested_direction"], side)

    def test_opposite_signal_does_not_approve_requested_side(self):
        result = check_direction(base("DOWN"), "UP", 80, pressure(), NOW)
        self.assertEqual(result.verdict, "WAIT")
        self.assertIn("against your chosen side", result.reason)

    def test_pressure_cannot_upgrade_wait(self):
        result = check_direction(base(verdict="WAIT"), "UP", 80, pressure(90, 90), NOW)
        self.assertEqual(result.verdict, "WAIT")

    def test_conflicting_and_balanced_pressure_veto(self):
        for context in (pressure(50, 70), pressure(65, 50), pressure(30, 20)):
            self.assertEqual(check_direction(base(), "UP", 80, context, NOW).verdict, "WAIT")

    def test_missing_stale_and_malformed_pressure_veto(self):
        cases = [None, {}, pressure(bid=float("nan")), pressure(bid=101)]
        for key, value in (("book_time", NOW-11), ("latest_trade_time", NOW+3), ("trade_count", 19)):
            context = pressure()
            context[key] = value
            cases.append(context)
        for context in cases:
            self.assertEqual(check_direction(base(), "UP", 80, context, NOW).verdict, "WAIT")

    def test_original_chart_signal_is_unchanged(self):
        original = base()
        check_direction(original, "UP", 80, pressure(20, 20), NOW)
        self.assertEqual(original.verdict, "UP")
        self.assertIsNone(original.research_context)

    def test_volume_is_quantity_weighted_and_stale_self_trades_are_excluded(self):
        depth = {"timestamp": NOW*1000, "bids": [[99, 60]], "asks": [[101, 40]]}
        flow = trades(20)+[{"t": NOW*1000, "v": 40, "T": 2, "M": 2}]
        flow += trades(100, timestamp=NOW-61)+[{"t": NOW*1000, "v": 900, "T": 1, "M": 1}]
        result = summarize_pressure(depth, flow, NOW)
        self.assertEqual(result["bid_pct"], 60)
        self.assertEqual(result["taker_buy_pct"], 50)
        self.assertEqual(result["trade_count"], 21)

    def test_few_or_stale_trades_are_not_evidence(self):
        for flow in (trades(19), trades(25, timestamp=NOW-11), trades(25, timestamp=NOW+3)):
            self.assertIsNone(summarize_pressure(None, flow, NOW)["taker_buy_pct"])

    def test_small_exchange_clock_skew_is_tolerated(self):
        depth = {"timestamp": (NOW+1)*1000, "bids": [[99, 60]], "asks": [[101, 40]]}
        context = summarize_pressure(depth, trades(timestamp=NOW+1), NOW)
        self.assertEqual(context["bid_pct"], 60)
        self.assertEqual(check_direction(base(), "UP", 80, context, NOW).verdict, "UP")

    def test_missing_stale_crossed_and_bad_books_are_not_evidence(self):
        cases = [None, {}, {"timestamp": (NOW-11)*1000, "bids": [[99, 60]], "asks": [[101, 40]]},
                 {"timestamp": NOW*1000, "bids": [[102, 60]], "asks": [[101, 40]]},
                 {"timestamp": NOW*1000, "bids": [[99, float("inf")]], "asks": [[101, 40]]}]
        for book in cases:
            self.assertIsNone(summarize_pressure(book, [], NOW)["bid_pct"])

    def test_paper_strategy_is_separate_and_snapshot_keeps_pressure(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = EventTracker(str(Path(directory)/"events.db"))
            result = check_direction(base(), "UP", 80, pressure(), NOW)
            paper, message = tracker.record("u", "g", "BTC/USDT", result, NOW)
            self.assertIn("!eventcheckstats", message)
            self.assertEqual(tracker.report("u", "g")["total"], 0)
            report = tracker.report("u", "g", version=CHECK_STRATEGY_VERSION)
            self.assertEqual(report["total"], 1)
            self.assertIn('"taker_buy_pct": 70', report["recent"][0]["snapshot"])
            tracker.settle(paper, {"price": 101, "time": NOW+600}, NOW+600)
            self.assertEqual(tracker.report("u", "g", version=CHECK_STRATEGY_VERSION)["wins"], 1)


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_requested_payout_is_never_assigned_to_opposite_side(self):
        with patch("paper_trading.PaperTradingManager"), patch("event_tracking.EventTracker"):
            module = importlib.import_module("bot")
        for side in ("UP", "DOWN"):
            with patch.object(module.mexc_client, "get_event_index_history", AsyncMock(return_value=None)), \
                 patch.object(module.mexc_client, "get_event_market_context", AsyncMock(return_value=pressure())), \
                 patch.object(module.mexc_client, "get_event_index_quote", AsyncMock(return_value={"price": 100, "time": NOW})), \
                 patch.object(module, "analyze_event", return_value=base(side)) as analyzer, \
                 patch("event_direction.time.time", return_value=NOW):
                result = await module.get_direction_check("BTC", side, 80)
                self.assertEqual(analyzer.call_args.args[-2:], (80, None) if side == "UP" else (None, 80))
                self.assertEqual(result.research_context["requested_direction"], side)

    async def test_context_api_is_futures_only_and_handles_failure(self):
        client = MEXCClient()
        class Session:
            closed = False
            urls = []
            def get(self, url, **kwargs):
                self.urls.append(url)
                return Response({"success": False})
        session = Session()
        client._session = session
        result = await client.get_event_market_context("BTC")
        self.assertIsNone(result["bid_pct"])
        self.assertIsNone(result["taker_buy_pct"])
        self.assertEqual(len(session.urls), 2)
        self.assertTrue(all("contract.mexc.com" in url for url in session.urls))

    async def test_check_commands_register_and_embed_explains_requested_side(self):
        with patch("paper_trading.PaperTradingManager"), patch("event_tracking.EventTracker"):
            module = importlib.import_module("bot")
        result = check_direction(base(verdict="WAIT"), "UP", 80, pressure(), NOW)
        embed = module.build_direction_check_embed("BTC/USDT", result, 1, 2)
        self.assertIn("Check UP", embed.title)
        self.assertIn("WAIT", embed.description)
        self.assertIn("Futures executed volume", " ".join(f.name for f in embed.fields))
        self.assertLess(len(embed), 6000)
        for name in ("eventcheck", "eventcheckstats"):
            self.assertIsNotNone(module.bot.get_command(name))
            self.assertIsNotNone(module.bot.tree.get_command(name))


if __name__ == "__main__":
    unittest.main()
