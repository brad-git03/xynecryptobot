"""Offline checks: python -m unittest test_event_futures -v"""

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from event_futures import analyze_event, break_even_rate, _features
from mexc_client import MEXCClient


NOW = 1_800_000_000.0


def history(count=500, step=0.01):
    prices = 100 + np.arange(count)*step
    return pd.DataFrame({
        "open_time": (NOW-count*60+np.arange(count)*60)*1000,
        "open": prices, "high": prices+0.5, "low": prices-0.5, "close": prices,
    })


def features_up(df):
    return pd.Series(1, index=df.index), pd.Series(1.0, index=df.index)


class EventAnalysisTests(unittest.TestCase):
    def test_payout_math_and_invalid_inputs(self):
        self.assertAlmostEqual(break_even_rate(80), 1/1.8)
        self.assertAlmostEqual(break_even_rate(87), 1/1.87)
        for value in (0, -1, 101, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                break_even_rate(value)

    def test_fresh_index_is_required(self):
        for price, timestamp in ((None, NOW), (100, NOW-31), (100, NOW+1),
                                 (float("nan"), NOW), (100, None)):
            self.assertEqual(analyze_event(history(), price, timestamp, 80, 80, NOW).verdict, "WAIT")

    def test_gaps_duplicates_and_bad_prices_wait(self):
        df = history()
        for bad in (df.drop(index=200), pd.concat([df, df.iloc[[200]]])):
            self.assertIn("gaps", analyze_event(bad, 104.99, NOW, 80, 80, NOW).reason)
        df.loc[200, "close"] = float("inf")
        self.assertIn("Invalid", analyze_event(df, 104.99, NOW, 80, 80, NOW).reason)

    def test_stale_candles_wait(self):
        df = history()
        df["open_time"] -= 180_000
        self.assertIn("stale", analyze_event(df, 104.99, NOW, 80, 80, NOW).reason)

    @patch("event_futures._features", side_effect=features_up)
    def test_nonoverlapping_expiry_labels_and_threshold(self, _):
        df = history()
        result = analyze_event(df, df.close.iloc[-1], NOW, 80, 80, NOW)
        self.assertEqual(result.verdict, "UP")
        self.assertEqual(result.samples, 44)  # starts at 50..480, each 10m apart
        self.assertEqual(result.wins, 44)
        self.assertAlmostEqual(result.historical_ev, 0.8)

    @patch("event_futures._features", side_effect=features_up)
    def test_losing_analogues_and_ties_wait(self, _):
        for step in (-0.01, 0):
            df = history(step=step)
            result = analyze_event(df, df.close.iloc[-1], NOW, 80, 80, NOW)
            self.assertEqual(result.verdict, "WAIT")
            self.assertEqual(result.wins, 0)
            self.assertAlmostEqual(result.historical_ev, -1 if step else 0)

    @patch("event_futures._features", side_effect=features_up)
    def test_insufficient_samples_missing_payout_and_entry_drift_wait(self, _):
        df = history(200)
        self.assertIn("at least 30", analyze_event(df, df.close.iloc[-1], NOW, 80, 80, NOW).reason)
        df = history()
        self.assertIn("payout", analyze_event(df, df.close.iloc[-1], NOW, now=NOW).reason)
        self.assertIn("half an ATR", analyze_event(df, df.close.iloc[-1]+1, NOW, 80, 80, NOW).reason)

    @patch("event_futures._features")
    def test_down_uses_its_own_payout(self, mocked):
        df = history(step=-0.01)
        mocked.return_value = pd.Series(-1, index=df.index), pd.Series(1.0, index=df.index)
        result = analyze_event(df, df.close.iloc[-1], NOW, 10, 87, NOW)
        self.assertEqual(result.verdict, "DOWN")
        self.assertEqual(result.payout_pct, 87)
        self.assertAlmostEqual(result.historical_ev, 0.87)

    @patch("event_futures._features", side_effect=features_up)
    def test_incomplete_candle_cannot_change_result(self, _):
        df = history()
        extra = pd.DataFrame({"open_time": [NOW*1000], "open": [1], "high": [1], "low": [1], "close": [1]})
        before = analyze_event(df, df.close.iloc[-1], NOW, 80, 80, NOW)
        after = analyze_event(pd.concat([df, extra]), df.close.iloc[-1], NOW, 80, 80, NOW)
        self.assertEqual(before, after)

    def test_indicators_do_not_read_future_prices(self):
        df = history()
        df["close"] += np.sin(np.arange(len(df))/4)*0.1
        full_directions, full_atr = _features(df)
        past_directions, past_atr = _features(df.iloc[:300])
        pd.testing.assert_series_equal(full_directions.iloc[:300], past_directions)
        pd.testing.assert_series_equal(full_atr.iloc[:300], past_atr)

    def test_low_price_index_is_not_rounded_to_zero(self):
        df = history()
        df[["open", "high", "low", "close"]] *= 1e-7
        result = analyze_event(df, float(df.close.iloc[-1]), NOW, 80, 80, NOW)
        self.assertGreater(result.index_price, 0)
        self.assertEqual(result.index_price, df.close.iloc[-1])


class Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self.payload


class Session:
    closed = False

    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return Response(self.payload)


class IndexClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_quote_uses_index_not_last_trade(self):
        client = MEXCClient()
        client._session = Session({"success": True, "data": {"indexPrice": 100, "lastPrice": 120, "timestamp": NOW*1000}})
        self.assertEqual(await client.get_event_index_quote("BTC"), {"price": 100, "time": NOW})

    async def test_unavailable_index_never_falls_back_to_spot(self):
        client = MEXCClient()
        session = Session({"success": False})
        client._session = session
        self.assertIsNone(await client.get_event_index_quote("BTC"))
        self.assertIsNone(await client.get_event_index_history("BTC"))
        self.assertEqual(len(session.urls), 2)
        self.assertTrue(all("contract.mexc.com" in url for url in session.urls))

    async def test_index_history_schema(self):
        client = MEXCClient()
        client._session = Session({"success": True, "data": {
            "time": [180, 120], "open": [3, 2], "high": [3, 2], "low": [3, 2], "close": [3, 2]}})
        df = await client.get_event_index_history("BTC")
        self.assertEqual(df.open_time.tolist(), [120000, 180000])
        self.assertEqual(df.close.tolist(), [2, 3])


if __name__ == "__main__":
    unittest.main()
