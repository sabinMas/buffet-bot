"""Phase 1: Pure-logic tests — no network, no mocking required."""
import pytest
import pandas as pd
import numpy as np

from buffet_bot.main import (
    _calculate_future_value,
    _calculate_sharpe,
    _calculate_max_drawdown,
    _consensus_text,
    _years_to_reach,
    _score_color,
)


# ── _calculate_future_value ───────────────────────────────────────────────────
# Signature: (principal, monthly_contribution, annual_return, years)

class TestCalculateFutureValue:
    @pytest.mark.parametrize("principal,monthly,rate,years,expected,tol", [
        # Standard monthly-compounding growth, no contributions
        (10_000, 0, 0.08, 10, 10_000 * (1 + 0.08/12)**120, 1.0),
        # Zero rate → no compounding, only principal + flat contributions
        (10_000, 0, 0.00, 10, 10_000.0, 0.01),
        # Zero principal, zero contribution, positive rate → 0
        (0,      0, 0.08, 10, 0.0,      0.01),
        # Zero years → return principal unchanged regardless of rate
        (10_000, 0, 0.08,  0, 10_000.0, 0.01),
        # Zero principal, zero rate, with monthly contribution
        (0, 100, 0.00, 10, 100 * 10 * 12, 0.01),
    ])
    def test_future_value(self, principal, monthly, rate, years, expected, tol):
        result = _calculate_future_value(principal, monthly, rate, years)
        assert abs(result - expected) < tol, (
            f"FV({principal}, {monthly}, {rate}, {years}) = {result}, expected ≈ {expected}"
        )

    def test_future_value_with_monthly_contribution(self):
        """Monthly contributions meaningfully increase FV vs no contributions."""
        fv_no_contrib  = _calculate_future_value(10_000,   0, 0.08, 10)
        fv_with_contrib = _calculate_future_value(10_000, 200, 0.08, 10)
        assert fv_with_contrib > fv_no_contrib

    def test_future_value_negative_years_returns_principal(self):
        """years <= 0 must always return the principal (no crash)."""
        result = _calculate_future_value(5_000, 100, 0.08, -5)
        assert result == 5_000.0


# ── _calculate_sharpe ─────────────────────────────────────────────────────────
# Signature: (daily_returns: pd.Series, risk_free_annual=0.05)

class TestCalculateSharpe:
    def test_sharpe_positive_returns_is_positive(self):
        """Consistent daily gains above risk-free → positive Sharpe."""
        returns = pd.Series([0.01, 0.012, 0.008, 0.015, 0.009] * 50)
        result = _calculate_sharpe(returns)
        assert result > 0

    def test_sharpe_negative_returns_is_negative(self):
        """Consistent daily losses → negative Sharpe."""
        returns = pd.Series([-0.005, -0.008, -0.003, -0.010] * 50)
        result = _calculate_sharpe(returns)
        assert result < 0

    def test_sharpe_zero_std_returns_zero(self):
        """Uniform returns → std=0 → must return 0.0 (no ZeroDivisionError)."""
        returns = pd.Series([0.001] * 100)
        result = _calculate_sharpe(returns)
        assert result == 0.0

    def test_sharpe_all_zeros_does_not_raise(self):
        """All-zero returns must not raise, must return a float."""
        returns = pd.Series([0.0] * 50)
        result = _calculate_sharpe(returns)
        # Due to floating-point precision, std() of all-zero excess may be non-zero;
        # the contract is: no exception and the result is a Python float.
        assert isinstance(result, float)

    def test_sharpe_returns_float(self):
        """Return type must always be a Python float."""
        returns = pd.Series([0.005, -0.002, 0.008, 0.001])
        result = _calculate_sharpe(returns)
        assert isinstance(result, float)


# ── _calculate_max_drawdown ───────────────────────────────────────────────────
# Signature: (equity_curve: list/array) → float in [0.0, 1.0]

class TestCalculateMaxDrawdown:
    def test_standard_drawdown(self):
        """Peak=120, trough=80 → drawdown=(120-80)/120 ≈ 0.333."""
        curve = [100, 120, 80, 90]
        result = _calculate_max_drawdown(curve)
        assert abs(result - (40 / 120)) < 1e-6

    def test_flat_curve_is_zero(self):
        """No peak-to-trough movement → drawdown = 0."""
        result = _calculate_max_drawdown([100, 100, 100, 100])
        assert result == 0.0

    def test_monotone_rise_is_zero(self):
        """Steadily rising equity → never draws down → 0."""
        result = _calculate_max_drawdown([100, 110, 120, 130])
        assert result == 0.0

    def test_drawdown_bounded_between_zero_and_one(self):
        """Max drawdown must always be in [0, 1]."""
        curve = [1000, 500, 200, 800, 300]
        result = _calculate_max_drawdown(curve)
        assert 0.0 <= result <= 1.0

    def test_drawdown_returns_float(self):
        """Must return a Python float."""
        result = _calculate_max_drawdown([100, 90, 110, 80])
        assert isinstance(result, float)

    @pytest.mark.parametrize("curve,expected_dd", [
        ([100, 50], 0.50),                   # 50% drop
        ([100, 100, 50, 100], 0.50),          # drop mid-sequence
        ([200, 100, 50], 0.75),               # cascading drops: (200-50)/200
    ])
    def test_drawdown_parametrized(self, curve, expected_dd):
        result = _calculate_max_drawdown(curve)
        assert abs(result - expected_dd) < 1e-6


# ── _consensus_text ───────────────────────────────────────────────────────────
# Signature: (consensus: str) → Rich markup string

class TestConsensusText:
    @pytest.mark.parametrize("action,expected_color", [
        ("BUY",     "green"),
        ("SELL",    "red"),
        ("HOLD",    "yellow"),
        ("UNKNOWN", "white"),   # fallback for any unrecognized value
        ("",        "white"),   # empty string → fallback
    ])
    def test_consensus_text_color(self, action, expected_color):
        result = _consensus_text(action)
        assert expected_color in result.lower()

    def test_consensus_text_contains_action(self):
        """The returned string must include the original action word."""
        for action in ("BUY", "SELL", "HOLD"):
            assert action in _consensus_text(action)

    def test_consensus_text_returns_string(self):
        assert isinstance(_consensus_text("BUY"), str)


# ── _score_color ──────────────────────────────────────────────────────────────
# Depends on _CONFIG defaults: green ≥ 70, yellow ≥ 40, red < 40

class TestScoreColor:
    @pytest.mark.parametrize("score,expected_color", [
        (100, "green"),   # max score
        (70,  "green"),   # exact green boundary
        (69,  "yellow"),  # one below green boundary
        (40,  "yellow"),  # exact yellow boundary
        (39,  "red"),     # one below yellow boundary
        (0,   "red"),     # minimum score
    ])
    def test_score_color_thresholds(self, score, expected_color):
        result = _score_color(score)
        assert result == expected_color

    def test_score_color_returns_string(self):
        assert isinstance(_score_color(50), str)


# ── _years_to_reach ───────────────────────────────────────────────────────────
# Signature: (target, balance, monthly, annual_return, max_years=100)

class TestYearsToReach:
    def test_already_at_target_returns_zero(self):
        """balance >= target → 0.0 immediately."""
        result = _years_to_reach(target=25_000, balance=25_000, monthly=0, annual_return=0.08)
        assert result == 0.0

    def test_above_target_returns_zero(self):
        """balance > target → 0.0."""
        result = _years_to_reach(target=10_000, balance=50_000, monthly=0, annual_return=0.08)
        assert result == 0.0

    def test_zero_rate_and_no_contributions_returns_none(self):
        """Zero rate + no contributions → can never grow → None."""
        result = _years_to_reach(target=50_000, balance=10_000, monthly=0, annual_return=0.0)
        assert result is None

    def test_reasonable_case_returns_positive_years(self):
        """$10k growing at 8% should reach $20k in roughly 8-10 years."""
        result = _years_to_reach(target=20_000, balance=10_000, monthly=0, annual_return=0.08)
        assert result is not None
        assert 8.0 < result < 10.0

    def test_with_monthly_contribution_reaches_faster(self):
        """Adding monthly contributions should reduce years to target."""
        no_contrib  = _years_to_reach(target=50_000, balance=10_000, monthly=0,   annual_return=0.08)
        with_contrib = _years_to_reach(target=50_000, balance=10_000, monthly=500, annual_return=0.08)
        assert with_contrib is not None
        assert no_contrib is not None
        assert with_contrib < no_contrib

    def test_unreachable_returns_none(self):
        """Target so large it cannot be reached in max_years → None."""
        result = _years_to_reach(
            target=1_000_000_000,
            balance=1,
            monthly=0,
            annual_return=0.001,
            max_years=10,
        )
        assert result is None


# ── Consensus voting logic ────────────────────────────────────────────────────
# Replicates lines 676-677 of main.py — logic is pure Python, no imports needed.

class TestConsensusVoting:
    """Test the consensus voting algorithm extracted from _run_analysis."""

    def _vote(self, model_responses):
        """Replicate the consensus calculation from main.py lines 676-677."""
        actions = [
            r.get('action', 'HOLD')
            for r in model_responses.values()
            if isinstance(r, dict) and 'action' in r
        ]
        return max(set(actions), key=actions.count) if actions else 'HOLD'

    @pytest.mark.parametrize("responses,expected", [
        # Both agree BUY
        ({'m1': {'action': 'BUY'}, 'm2': {'action': 'BUY'}}, 'BUY'),
        # Both agree SELL
        ({'m1': {'action': 'SELL'}, 'm2': {'action': 'SELL'}}, 'SELL'),
        # Both agree HOLD
        ({'m1': {'action': 'HOLD'}, 'm2': {'action': 'HOLD'}}, 'HOLD'),
        # One model errored — valid response wins
        ({'m1': {'action': 'HOLD'}, 'm2': {'error': 'timeout'}}, 'HOLD'),
        # Both errored — must default to HOLD, no crash
        ({'m1': {'error': 'fail'}, 'm2': {'error': 'fail'}}, 'HOLD'),
        # One response missing 'action' key — only the one with 'action' counts
        ({'m1': {'confidence': 0.8}, 'm2': {'action': 'BUY'}}, 'BUY'),
        # Both missing 'action' key — falls through to empty actions list → HOLD
        ({'m1': {'confidence': 0.9}, 'm2': {'confidence': 0.7}}, 'HOLD'),
    ])
    def test_consensus_voting(self, responses, expected):
        assert self._vote(responses) == expected

    def test_tie_between_two_actions(self):
        """Tie: BUY vs SELL — max() picks deterministically (BUY comes first in set)."""
        responses = {'m1': {'action': 'BUY'}, 'm2': {'action': 'SELL'}}
        result = self._vote(responses)
        # On a tie, max() with key=count picks one of the tied values — just verify no crash
        assert result in ('BUY', 'SELL')

    def test_majority_wins_with_three_models(self):
        """Three-model scenario: majority action wins."""
        responses = {
            'm1': {'action': 'BUY'},
            'm2': {'action': 'BUY'},
            'm3': {'action': 'SELL'},
        }
        assert self._vote(responses) == 'BUY'
