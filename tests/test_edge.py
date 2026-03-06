"""Tests for buffet_bot/edge.py — signal helpers and compute_edge_score()."""
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from buffet_bot.edge import (
    compute_insider_signal,
    compute_politician_signal,
    compute_earnings_signal,
    _analyst_to_score,
    compute_edge_score,
    DEFAULT_WEIGHTS,
    _COMPONENT_KEYS,
)


# ── compute_insider_signal ────────────────────────────────────────────────────

class TestComputeInsiderSignal:

    def test_no_transactions_returns_neutral(self):
        with patch('buffet_bot.edge.fetch_insider_transactions', return_value=[]):
            score = compute_insider_signal('AAPL')
        assert score == 50.0

    def test_bullish_sentiment_above_50(self):
        txns = [{'date': '2026-01-01', 'type': 'BUY', 'value': 600_000}]
        summary = {'net_sentiment': 'bullish', 'net_value': 600_000, 'buys': 1, 'sells': 0}
        with patch('buffet_bot.edge.fetch_insider_transactions', return_value=txns):
            with patch('buffet_bot.edge.insider_summary', return_value=summary):
                score = compute_insider_signal('AAPL')
        assert score > 50.0
        assert score <= 100.0

    def test_bullish_full_scale_at_1m(self):
        """Net value >= $1M should reach exactly 100."""
        txns = [{}]
        summary = {'net_sentiment': 'bullish', 'net_value': 1_000_000, 'buys': 1, 'sells': 0}
        with patch('buffet_bot.edge.fetch_insider_transactions', return_value=txns):
            with patch('buffet_bot.edge.insider_summary', return_value=summary):
                score = compute_insider_signal('AAPL')
        assert score == 100.0

    def test_bearish_sentiment_below_50(self):
        txns = [{'date': '2026-01-01', 'type': 'SELL', 'value': 500_000}]
        summary = {'net_sentiment': 'bearish', 'net_value': -500_000, 'buys': 0, 'sells': 1}
        with patch('buffet_bot.edge.fetch_insider_transactions', return_value=txns):
            with patch('buffet_bot.edge.insider_summary', return_value=summary):
                score = compute_insider_signal('AAPL')
        assert score < 50.0
        assert score >= 0.0

    def test_neutral_sentiment_returns_50(self):
        txns = [{}]
        summary = {'net_sentiment': 'neutral', 'net_value': 0, 'buys': 1, 'sells': 1}
        with patch('buffet_bot.edge.fetch_insider_transactions', return_value=txns):
            with patch('buffet_bot.edge.insider_summary', return_value=summary):
                score = compute_insider_signal('AAPL')
        assert score == 50.0

    def test_simulation_date_filters_future_transactions(self):
        """Transactions after simulation_date must be excluded."""
        txns = [
            {'date': '2025-06-01', 'type': 'BUY', 'value': 100_000},
            {'date': '2026-02-01', 'type': 'BUY', 'value': 900_000},  # after cutoff
        ]
        cutoff = datetime(2025, 12, 31)
        # Only first transaction passes; with net_value=100k summary should reflect that
        summary_one = {'net_sentiment': 'bullish', 'net_value': 100_000, 'buys': 1, 'sells': 0}
        with patch('buffet_bot.edge.fetch_insider_transactions', return_value=txns):
            with patch('buffet_bot.edge.insider_summary', return_value=summary_one) as mock_summary:
                score = compute_insider_signal('AAPL', simulation_date=cutoff)
                # insider_summary must have been called with only the non-future transaction
                args = mock_summary.call_args[0][0]
                assert len(args) == 1
                assert args[0]['date'] == '2025-06-01'
        assert score > 50.0

    def test_simulation_date_no_matching_transactions_returns_neutral(self):
        """All transactions after simulation_date → neutral 50."""
        txns = [{'date': '2026-05-01', 'type': 'BUY', 'value': 999_000}]
        cutoff = datetime(2025, 1, 1)
        with patch('buffet_bot.edge.fetch_insider_transactions', return_value=txns):
            score = compute_insider_signal('AAPL', simulation_date=cutoff)
        assert score == 50.0


# ── compute_politician_signal ─────────────────────────────────────────────────

class TestComputePoliticianSignal:

    def _patch_trades(self, house=None, fmp=None, merged=None):
        """Helper: patch all three politician fetch functions."""
        house   = house   or []
        fmp     = fmp     or []
        merged  = merged  if merged is not None else house + fmp
        return (
            patch('buffet_bot.edge.fetch_house_trades', return_value=house),
            patch('buffet_bot.edge.fetch_fmp_trades',   return_value=fmp),
            patch('buffet_bot.edge.merge_deduplicate',  return_value=merged),
        )

    def test_no_trades_returns_neutral(self):
        p1, p2, p3 = self._patch_trades()
        with p1, p2, p3:
            score = compute_politician_signal('AAPL')
        assert score == 50.0

    def test_all_purchases_returns_100(self):
        trades = [
            {'ticker': 'AAPL', 'date': '2026-01-01', 'name': 'A', 'action': 'Purchase', 'amount': '1001-15000', 'chamber': 'House', 'source': 'house'},
            {'ticker': 'AAPL', 'date': '2026-01-02', 'name': 'B', 'action': 'Purchase', 'amount': '1001-15000', 'chamber': 'House', 'source': 'house'},
        ]
        p1, p2, p3 = self._patch_trades(merged=trades)
        with p1, p2, p3:
            score = compute_politician_signal('AAPL')
        assert score == 100.0

    def test_all_sales_returns_0(self):
        trades = [
            {'ticker': 'AAPL', 'date': '2026-01-01', 'name': 'A', 'action': 'Sale', 'amount': '1001-15000', 'chamber': 'House', 'source': 'house'},
        ]
        p1, p2, p3 = self._patch_trades(merged=trades)
        with p1, p2, p3:
            score = compute_politician_signal('AAPL')
        assert score == 0.0

    def test_mixed_ratio_75_percent_buys(self):
        trades = [
            {'action': 'Purchase', 'date': '2026-01-01'},
            {'action': 'Purchase', 'date': '2026-01-02'},
            {'action': 'Purchase', 'date': '2026-01-03'},
            {'action': 'Sale',     'date': '2026-01-04'},
        ]
        p1, p2, p3 = self._patch_trades(merged=trades)
        with p1, p2, p3:
            score = compute_politician_signal('AAPL')
        assert abs(score - 75.0) < 0.01

    def test_unknown_action_excluded_from_ratio(self):
        """Only Purchase/Sale count; 'Exchange' or blank actions are neither buys nor sells."""
        trades = [
            {'action': 'Exchange', 'date': '2026-01-01'},
            {'action': 'Purchase', 'date': '2026-01-02'},
        ]
        p1, p2, p3 = self._patch_trades(merged=trades)
        with p1, p2, p3:
            score = compute_politician_signal('AAPL')
        # 1 purchase, 0 sales → total = 1 → 100%
        assert score == 100.0

    def test_simulation_date_filters_future_trades(self):
        trades = [
            {'action': 'Purchase', 'date': '2025-06-01'},
            {'action': 'Sale',     'date': '2026-03-01'},  # after cutoff
        ]
        p1, p2, p3 = self._patch_trades(merged=trades)
        with p1, p2, p3:
            score = compute_politician_signal('AAPL', simulation_date=datetime(2025, 12, 31))
        # Only the purchase passes → 100%
        assert score == 100.0


# ── compute_earnings_signal ───────────────────────────────────────────────────

class TestComputeEarningsSignal:

    def test_no_data_returns_neutral(self):
        with patch('buffet_bot.edge.get_earnings_history', return_value=[]):
            score = compute_earnings_signal('AAPL')
        assert score == 50.0

    def test_all_beats_returns_100(self):
        rows = [
            {'beat_miss': 'BEAT'}, {'beat_miss': 'BEAT'},
            {'beat_miss': 'BEAT'}, {'beat_miss': 'BEAT'},
        ]
        with patch('buffet_bot.edge.get_earnings_history', return_value=rows):
            score = compute_earnings_signal('AAPL')
        assert score == 100.0

    def test_all_misses_returns_0(self):
        rows = [
            {'beat_miss': 'MISS'}, {'beat_miss': 'MISS'},
            {'beat_miss': 'MISS'}, {'beat_miss': 'MISS'},
        ]
        with patch('buffet_bot.edge.get_earnings_history', return_value=rows):
            score = compute_earnings_signal('AAPL')
        assert score == 0.0

    def test_half_beat_rate_returns_50(self):
        rows = [
            {'beat_miss': 'BEAT'}, {'beat_miss': 'MISS'},
            {'beat_miss': 'BEAT'}, {'beat_miss': 'MISS'},
        ]
        with patch('buffet_bot.edge.get_earnings_history', return_value=rows):
            score = compute_earnings_signal('AAPL')
        assert score == 50.0

    def test_single_beat_returns_100(self):
        with patch('buffet_bot.edge.get_earnings_history', return_value=[{'beat_miss': 'BEAT'}]):
            assert compute_earnings_signal('AAPL') == 100.0

    def test_lookback_quarters_passed_to_db(self):
        mock_history = MagicMock(return_value=[])
        with patch('buffet_bot.edge.get_earnings_history', mock_history):
            compute_earnings_signal('AAPL', lookback_quarters=8)
        mock_history.assert_called_once_with('AAPL', limit=8)


# ── _analyst_to_score ─────────────────────────────────────────────────────────

class TestAnalystToScore:

    def test_empty_dict_returns_neutral(self):
        assert _analyst_to_score({}) == 50.0

    def test_none_consensus_returns_neutral(self):
        # None is falsy — treated as empty
        assert _analyst_to_score(None) == 50.0

    def test_missing_upside_pct_returns_neutral(self):
        assert _analyst_to_score({'rating': 'BUY'}) == 50.0

    def test_zero_upside_returns_50(self):
        assert _analyst_to_score({'upside_pct': 0.0}) == 50.0

    def test_positive_upside_above_50(self):
        score = _analyst_to_score({'upside_pct': 20.0})
        assert score == 70.0

    def test_negative_upside_below_50(self):
        score = _analyst_to_score({'upside_pct': -20.0})
        assert score == 30.0

    def test_clamped_at_100(self):
        score = _analyst_to_score({'upside_pct': 999.0})
        assert score == 100.0

    def test_clamped_at_0(self):
        score = _analyst_to_score({'upside_pct': -999.0})
        assert score == 0.0

    def test_exactly_50_upside_gives_100(self):
        assert _analyst_to_score({'upside_pct': 50.0}) == 100.0

    def test_exactly_minus_50_gives_0(self):
        assert _analyst_to_score({'upside_pct': -50.0}) == 0.0


# ── compute_edge_score ────────────────────────────────────────────────────────

def _mock_all_neutral():
    """Return a context-manager stack that patches every data dependency to neutral 50."""
    return [
        patch('buffet_bot.edge.get_buffett_metrics',    return_value={'score': 50}),
        patch('buffet_bot.edge.get_analyst_consensus',  return_value={'upside_pct': 0.0}),
        patch('buffet_bot.edge.fetch_insider_transactions', return_value=[]),
        patch('buffet_bot.edge.fetch_house_trades',     return_value=[]),
        patch('buffet_bot.edge.fetch_fmp_trades',       return_value=[]),
        patch('buffet_bot.edge.merge_deduplicate',      return_value=[]),
        patch('buffet_bot.edge.get_earnings_history',   return_value=[]),
    ]


class TestComputeEdgeScore:

    def _apply_patches(self, patches):
        for p in patches:
            p.start()
        return patches

    def _stop_patches(self, patches):
        for p in patches:
            p.stop()

    def test_return_shape(self):
        patches = _mock_all_neutral()
        for p in patches: p.start()
        try:
            result = compute_edge_score('AAPL')
        finally:
            for p in patches: p.stop()
        assert 'ticker'          in result
        assert 'edge_score'      in result
        assert 'components'      in result
        assert 'weights_used'    in result
        assert 'simulation_date' in result

    def test_ticker_uppercased(self):
        patches = _mock_all_neutral()
        for p in patches: p.start()
        try:
            result = compute_edge_score('aapl')
        finally:
            for p in patches: p.stop()
        assert result['ticker'] == 'AAPL'

    def test_all_neutral_components_give_50(self):
        patches = _mock_all_neutral()
        for p in patches: p.start()
        try:
            result = compute_edge_score('AAPL')
        finally:
            for p in patches: p.stop()
        assert abs(result['edge_score'] - 50.0) < 0.5

    def test_all_component_keys_present(self):
        patches = _mock_all_neutral()
        for p in patches: p.start()
        try:
            result = compute_edge_score('AAPL')
        finally:
            for p in patches: p.stop()
        for k in _COMPONENT_KEYS:
            assert k in result['components']

    def test_weight_normalisation(self):
        """Non-normalised custom weights are re-normalised to sum to 1.0."""
        patches = _mock_all_neutral()
        for p in patches: p.start()
        try:
            result = compute_edge_score('AAPL', weights={'buffett': 3.0, 'llm': 1.0,
                                                          'insider': 1.0, 'politician': 1.0,
                                                          'earnings': 1.0, 'analyst': 1.0})
        finally:
            for p in patches: p.stop()
        total = sum(result['weights_used'].values())
        assert abs(total - 1.0) < 1e-6

    def test_custom_weight_subset_normalised(self):
        """Partial override — remaining keys keep DEFAULT_WEIGHTS ratios after normalisation."""
        patches = _mock_all_neutral()
        for p in patches: p.start()
        try:
            result = compute_edge_score('AAPL', weights={'buffett': 0.9})
        finally:
            for p in patches: p.stop()
        total = sum(result['weights_used'].values())
        assert abs(total - 1.0) < 1e-6

    def test_high_buffett_score_raises_edge(self):
        """Buffett score of 100 with all others neutral should push edge_score above 50."""
        patches = [
            patch('buffet_bot.edge.get_buffett_metrics',       return_value={'score': 100}),
            patch('buffet_bot.edge.get_analyst_consensus',     return_value={'upside_pct': 0.0}),
            patch('buffet_bot.edge.fetch_insider_transactions', return_value=[]),
            patch('buffet_bot.edge.fetch_house_trades',        return_value=[]),
            patch('buffet_bot.edge.fetch_fmp_trades',          return_value=[]),
            patch('buffet_bot.edge.merge_deduplicate',         return_value=[]),
            patch('buffet_bot.edge.get_earnings_history',      return_value=[]),
        ]
        for p in patches: p.start()
        try:
            result = compute_edge_score('AAPL')
        finally:
            for p in patches: p.stop()
        assert result['edge_score'] > 50.0

    def test_llm_score_injected_into_components(self):
        patches = _mock_all_neutral()
        for p in patches: p.start()
        try:
            result = compute_edge_score('AAPL', llm_score=80.0)
        finally:
            for p in patches: p.stop()
        assert result['components']['llm'] == 80.0

    def test_llm_score_raises_edge_above_neutral(self):
        """llm_score=100 with all others neutral should push edge_score above 50."""
        patches = _mock_all_neutral()
        for p in patches: p.start()
        try:
            result = compute_edge_score('AAPL', llm_score=100.0)
        finally:
            for p in patches: p.stop()
        assert result['edge_score'] > 50.0

    def test_simulation_date_in_return(self):
        patches = _mock_all_neutral()
        for p in patches: p.start()
        sim = datetime(2025, 6, 1)
        try:
            result = compute_edge_score('AAPL', simulation_date=sim)
        finally:
            for p in patches: p.stop()
        assert result['simulation_date'] == sim.isoformat()

    def test_no_simulation_date_is_none(self):
        patches = _mock_all_neutral()
        for p in patches: p.start()
        try:
            result = compute_edge_score('AAPL')
        finally:
            for p in patches: p.stop()
        assert result['simulation_date'] is None

    def test_component_exception_falls_back_to_neutral(self):
        """If one fetcher raises an exception, its component defaults to 50."""
        patches = [
            patch('buffet_bot.edge.get_buffett_metrics',        side_effect=RuntimeError('boom')),
            patch('buffet_bot.edge.get_analyst_consensus',      return_value={'upside_pct': 0.0}),
            patch('buffet_bot.edge.fetch_insider_transactions', return_value=[]),
            patch('buffet_bot.edge.fetch_house_trades',         return_value=[]),
            patch('buffet_bot.edge.fetch_fmp_trades',           return_value=[]),
            patch('buffet_bot.edge.merge_deduplicate',          return_value=[]),
            patch('buffet_bot.edge.get_earnings_history',       return_value=[]),
        ]
        for p in patches: p.start()
        try:
            result = compute_edge_score('AAPL')
        finally:
            for p in patches: p.stop()
        # Should not raise; buffett should default to 50
        assert result['components']['buffett'] == 50.0
        assert 0.0 <= result['edge_score'] <= 100.0

    def test_weights_used_keys_match_defaults(self):
        patches = _mock_all_neutral()
        for p in patches: p.start()
        try:
            result = compute_edge_score('AAPL')
        finally:
            for p in patches: p.stop()
        assert set(result['weights_used'].keys()) == set(DEFAULT_WEIGHTS.keys())
