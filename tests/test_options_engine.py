"""Tests for buffet_bot/options_engine.py — Black-Scholes, Greeks, IV, yield, wheel.

All tests are pure-function or lightly mocked.  No real yfinance, Alpaca,
or Ollama calls are made.
"""

import math
import pytest
from unittest.mock import MagicMock, patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bs(spot, strike, dte, sigma=0.30, r=0.05, option_type="call"):
    from buffet_bot.options_engine import black_scholes_price
    return black_scholes_price(spot, strike, dte, r, sigma, option_type)


def _greeks(spot, strike, dte, sigma=0.30, r=0.05, option_type="call"):
    from buffet_bot.options_engine import compute_greeks
    return compute_greeks(spot, strike, dte, r, sigma, option_type)


# ── black_scholes_price ───────────────────────────────────────────────────────

class TestBlackScholesPrice:
    """Unit tests for black_scholes_price()."""

    # Return shape ─────────────────────────────────────────────────────────────

    def test_returns_dict_with_required_keys(self):
        result = _bs(100, 100, 30)
        assert "price" in result
        assert "d1"    in result
        assert "d2"    in result
        assert "T"     in result

    def test_price_is_non_negative(self):
        """Option prices are always >= 0."""
        assert _bs(100, 100, 30)["price"] >= 0
        assert _bs(100, 120, 30)["price"] >= 0
        assert _bs(100,  80, 30, option_type="put")["price"] >= 0

    # Call vs put relationship ─────────────────────────────────────────────────

    def test_itm_call_price_greater_than_itm_put_price(self):
        """For an ITM call (spot > strike), call price > put price.
        ITM: spot=120, strike=100, so the call is deep in-the-money.
        """
        call_price = _bs(120, 100, 30, sigma=0.30)["price"]
        put_price  = _bs(120, 100, 30, sigma=0.30, option_type="put")["price"]
        assert call_price > put_price, (
            f"Deep ITM call ({call_price:.4f}) should cost more than "
            f"corresponding put ({put_price:.4f})"
        )

    def test_deep_otm_put_price_greater_than_deep_otm_call_price(self):
        """For a deep OTM call (spot << strike), the put is worth more than
        the call: spot=80, strike=100.
        """
        call_price = _bs(80, 100, 30, sigma=0.30)["price"]
        put_price  = _bs(80, 100, 30, sigma=0.30, option_type="put")["price"]
        assert put_price > call_price, (
            f"When spot ({80}) << strike ({100}), put ({put_price:.4f}) "
            f"should be more expensive than call ({call_price:.4f})"
        )

    # Intrinsic value at near-expiry ───────────────────────────────────────────

    @pytest.mark.parametrize("spot,strike,expected_intrinsic", [
        (110, 100, 10.0),  # ITM call: 110 - 100 = 10
        (90,  100,  0.0),  # OTM call: worthless
    ])
    def test_call_price_approaches_intrinsic_at_expiry(self, spot, strike, expected_intrinsic):
        """With dte=1 (near-expiry), call price should be close to intrinsic.

        The function floors T at 1e-10 so dte=1 gives T ~ 0.00274 days.
        With very low time value, the price should be near intrinsic.
        """
        result = _bs(spot, strike, dte=1, sigma=0.30, r=0.0)
        price = result["price"]
        # Allow 10% tolerance of spot for time value at 1 DTE
        tolerance = spot * 0.10
        assert abs(price - expected_intrinsic) < tolerance, (
            f"At 1 DTE, call({spot}, {strike}) price={price:.4f} "
            f"expected ≈ {expected_intrinsic:.4f} (intrinsic), "
            f"tolerance={tolerance:.4f}"
        )

    # Put intrinsic value at near-expiry ─────────────────────────────────────

    def test_put_price_approaches_intrinsic_at_expiry(self):
        """ITM put: strike 110, spot 100 → intrinsic = 10."""
        result = _bs(100, 110, dte=1, sigma=0.30, r=0.0, option_type="put")
        price = result["price"]
        intrinsic = 10.0
        tolerance = 100 * 0.10   # 10% of spot
        assert abs(price - intrinsic) < tolerance, (
            f"At 1 DTE, put(100, 110) price={price:.4f} expected ≈ {intrinsic:.4f}"
        )

    # Monotone in sigma ────────────────────────────────────────────────────────

    def test_higher_sigma_yields_higher_price(self):
        """Higher volatility → higher option price (all else equal)."""
        low_vol  = _bs(100, 100, 30, sigma=0.10)["price"]
        high_vol = _bs(100, 100, 30, sigma=0.50)["price"]
        assert high_vol > low_vol, (
            f"sigma=0.50 price ({high_vol:.4f}) should exceed "
            f"sigma=0.10 price ({low_vol:.4f})"
        )

    # Parity check ─────────────────────────────────────────────────────────────

    def test_put_call_parity_approximately_holds(self):
        """C - P ≈ S * e^{-q*T} - K * e^{-r*T}  (simplified, q=0 here).

        Using r=0.05 and verifying the deviation is small.
        """
        S, K, dte, sigma, r = 100, 100, 60, 0.30, 0.05
        T = dte / 365.0
        call = _bs(S, K, dte, sigma=sigma, r=r)["price"]
        put  = _bs(S, K, dte, sigma=sigma, r=r, option_type="put")["price"]
        parity_rhs = S - K * math.exp(-r * T)
        assert abs((call - put) - parity_rhs) < 0.02, (
            f"Put-call parity violated: C-P={call - put:.4f}, "
            f"S-Ke^(-rT)={parity_rhs:.4f}"
        )


# ── compute_greeks ────────────────────────────────────────────────────────────

class TestComputeGreeks:
    """Unit tests for compute_greeks()."""

    def test_returns_dict_with_all_greek_keys(self):
        result = _greeks(100, 100, 30)
        for key in ("delta", "gamma", "theta", "vega", "rho"):
            assert key in result, f"Missing Greek: {key}"

    def test_all_greeks_are_floats(self):
        result = _greeks(100, 100, 30)
        for key, value in result.items():
            assert isinstance(value, float), f"{key} is not a float: {type(value)}"

    # Delta: ATM call ──────────────────────────────────────────────────────────

    def test_atm_call_delta_approximately_half(self):
        """ATM call delta (S = K = 100) should be close to 0.5.

        The exact value depends on r and T; for r=0 it equals exactly 0.5.
        With r=0.05 and 30 DTE it should be slightly above 0.5.
        """
        result = _greeks(100, 100, 30, sigma=0.30)
        delta = result["delta"]
        assert 0.45 <= delta <= 0.60, (
            f"ATM call delta should be ≈ 0.5; got {delta:.4f}"
        )

    # Delta: deep ITM call ─────────────────────────────────────────────────────

    def test_deep_itm_call_delta_near_one(self):
        """Deep ITM call (spot=150, strike=100) → delta close to 1.0."""
        result = _greeks(150, 100, 30, sigma=0.30)
        delta = result["delta"]
        assert delta >= 0.85, (
            f"Deep ITM call delta should be ≥ 0.85; got {delta:.4f}"
        )

    # Delta: put ───────────────────────────────────────────────────────────────

    def test_atm_put_delta_is_negative_approximately_minus_half(self):
        """ATM put delta should be approximately -0.5."""
        result = _greeks(100, 100, 30, sigma=0.30, option_type="put")
        delta = result["delta"]
        assert -0.60 <= delta <= -0.40, (
            f"ATM put delta should be ≈ -0.5; got {delta:.4f}"
        )

    def test_deep_itm_put_delta_near_minus_one(self):
        """Deep ITM put (spot=50, strike=100) → delta close to -1.0."""
        result = _greeks(50, 100, 30, sigma=0.30, option_type="put")
        delta = result["delta"]
        assert delta <= -0.85, (
            f"Deep ITM put delta should be ≤ -0.85; got {delta:.4f}"
        )

    # Gamma ────────────────────────────────────────────────────────────────────

    def test_gamma_is_positive(self):
        """Gamma is always positive for both calls and puts."""
        assert _greeks(100, 100, 30)["gamma"] > 0
        assert _greeks(100, 100, 30, option_type="put")["gamma"] > 0

    def test_gamma_call_equals_put_gamma(self):
        """Gamma is the same for calls and puts (same underlying, same strike)."""
        call_gamma = _greeks(100, 100, 30)["gamma"]
        put_gamma  = _greeks(100, 100, 30, option_type="put")["gamma"]
        assert abs(call_gamma - put_gamma) < 1e-6, (
            f"Call gamma ({call_gamma}) != put gamma ({put_gamma})"
        )

    # Theta ────────────────────────────────────────────────────────────────────

    def test_theta_is_negative(self):
        """Theta is negative — long options lose value as time passes."""
        assert _greeks(100, 100, 30)["theta"] < 0
        assert _greeks(100, 100, 30, option_type="put")["theta"] < 0

    # Vega ─────────────────────────────────────────────────────────────────────

    def test_vega_is_positive(self):
        """Vega is positive — long options gain value with rising IV."""
        assert _greeks(100, 100, 30)["vega"] > 0
        assert _greeks(100, 100, 30, option_type="put")["vega"] > 0

    def test_vega_call_equals_put_vega(self):
        """Vega is the same for calls and puts (same underlying, strike, dte)."""
        call_vega = _greeks(100, 100, 30)["vega"]
        put_vega  = _greeks(100, 100, 30, option_type="put")["vega"]
        assert abs(call_vega - put_vega) < 1e-6


# ── estimate_iv ───────────────────────────────────────────────────────────────

class TestEstimateIV:
    """Unit tests for estimate_iv() — Newton-Raphson IV solver."""

    def _estimate(self, option_price, spot, strike, dte, r=0.05, option_type="call"):
        from buffet_bot.options_engine import estimate_iv
        return estimate_iv(option_price, spot, strike, dte, r, option_type)

    # Round-trip tests ─────────────────────────────────────────────────────────

    @pytest.mark.parametrize("known_iv,spot,strike,dte", [
        (0.20, 100, 100, 30),   # low IV, ATM, 30 DTE
        (0.40, 100, 100, 60),   # medium IV, ATM, 60 DTE
        (0.25, 150, 140, 45),   # slightly ITM call, 45 DTE
        (0.35, 100, 110, 30),   # OTM call, 30 DTE
    ])
    def test_round_trip_call_within_tolerance(self, known_iv, spot, strike, dte):
        """Compute BS price at known_iv, then estimate IV from that price.
        The recovered IV must be within 0.01 of the input.
        """
        bs_result = _bs(spot, strike, dte, sigma=known_iv, r=0.05)
        price = bs_result["price"]

        if price <= 0:
            pytest.skip(f"BS price is non-positive ({price}) at these inputs")

        recovered_iv = self._estimate(price, spot, strike, dte)
        assert recovered_iv is not None, (
            f"estimate_iv returned None for price={price:.4f}, "
            f"spot={spot}, strike={strike}, dte={dte}, known_iv={known_iv}"
        )
        assert abs(recovered_iv - known_iv) < 0.01, (
            f"Round-trip IV mismatch: known={known_iv}, recovered={recovered_iv:.6f}, "
            f"diff={abs(recovered_iv - known_iv):.6f}"
        )

    @pytest.mark.parametrize("known_iv,spot,strike,dte", [
        (0.20, 100, 100, 30),   # ATM put
        (0.30, 100, 110, 45),   # ITM put
    ])
    def test_round_trip_put_within_tolerance(self, known_iv, spot, strike, dte):
        """Same round-trip test for put options."""
        bs_result = _bs(spot, strike, dte, sigma=known_iv, r=0.05, option_type="put")
        price = bs_result["price"]

        if price <= 0:
            pytest.skip(f"BS put price is non-positive ({price})")

        recovered_iv = self._estimate(price, spot, strike, dte, option_type="put")
        assert recovered_iv is not None
        assert abs(recovered_iv - known_iv) < 0.01, (
            f"Put round-trip IV: known={known_iv}, recovered={recovered_iv:.6f}"
        )

    # Degenerate inputs ────────────────────────────────────────────────────────

    @pytest.mark.parametrize("option_price,spot,strike,dte", [
        (0.0,  100, 100, 30),   # zero premium → None
        (-1.0, 100, 100, 30),   # negative premium → None
        (5.0,  0,   100, 30),   # zero spot → None
        (5.0,  100, 0,   30),   # zero strike → None
        (5.0,  100, 100,  0),   # zero DTE → None
    ])
    def test_degenerate_inputs_return_none(self, option_price, spot, strike, dte):
        """Degenerate inputs (zero or negative) must return None, not raise."""
        result = self._estimate(option_price, spot, strike, dte)
        assert result is None, (
            f"estimate_iv({option_price}, {spot}, {strike}, {dte}) "
            f"should return None, got {result}"
        )

    def test_returned_iv_is_positive_when_not_none(self):
        """A valid IV solution must be a positive float."""
        iv = self._estimate(5.0, 100, 100, 30)
        if iv is not None:
            assert iv > 0


# ── annualized_yield ──────────────────────────────────────────────────────────

class TestAnnualizedYield:
    """Unit tests for annualized_yield(premium, strike, dte)."""

    def _yield(self, premium, strike, dte):
        from buffet_bot.options_engine import annualized_yield
        return annualized_yield(premium, strike, dte)

    def test_known_value_0_50_on_100_strike_30_dte(self):
        """(0.5 / 100) * (365 / 30) ≈ 0.0608 within 1e-4."""
        result = self._yield(0.5, 100, 30)
        expected = (0.5 / 100.0) * (365.0 / 30.0)
        assert abs(result - expected) < 1e-4, (
            f"annualized_yield(0.5, 100, 30) = {result:.6f}; "
            f"expected {expected:.6f}"
        )

    def test_zero_strike_returns_zero(self):
        """Dividing by strike=0 must return 0.0, not raise ZeroDivisionError."""
        result = self._yield(1.0, 0, 30)
        assert result == 0.0

    def test_zero_dte_returns_zero(self):
        """Dividing by dte=0 must return 0.0, not raise ZeroDivisionError."""
        result = self._yield(1.0, 100, 0)
        assert result == 0.0

    def test_result_is_float(self):
        """Result must always be a float."""
        assert isinstance(self._yield(2.0, 100, 45), float)

    @pytest.mark.parametrize("premium,strike,dte", [
        (1.0,  100, 30),
        (2.0,  150, 45),
        (0.5,  50,  14),
    ])
    def test_result_is_positive_for_positive_inputs(self, premium, strike, dte):
        """Positive inputs must produce a positive yield."""
        assert self._yield(premium, strike, dte) > 0

    def test_higher_premium_gives_higher_yield(self):
        """Doubling the premium doubles the annualised yield."""
        y1 = self._yield(1.0, 100, 30)
        y2 = self._yield(2.0, 100, 30)
        assert abs(y2 - 2 * y1) < 1e-9

    def test_longer_dte_gives_lower_yield(self):
        """Longer time to expiry dilutes the annualised yield."""
        short = self._yield(1.0, 100, 30)
        long_ = self._yield(1.0, 100, 60)
        assert short > long_


# ── score_wheel_strategy ──────────────────────────────────────────────────────

class TestScoreWheelStrategy:
    """Unit tests for score_wheel_strategy() — verifies composite score is
    in [0, 100] and the returned dict has the expected keys.

    All external calls (get_realtime_data, compute_edge_score, find_optimal_csp,
    find_optimal_covered_call, _get_atr) are mocked so no network I/O occurs.
    """

    def _run(
        self,
        price=150.0,
        edge_score=70.0,
        put_yield=0.12,
        call_yield=0.10,
        atr=2.0,
        csp_oi=500,
    ):
        """Call score_wheel_strategy with fully mocked dependencies."""
        from buffet_bot.options_engine import score_wheel_strategy

        mock_csp = {"annual_yield": put_yield,  "oi": csp_oi}
        mock_cc  = {"annual_yield": call_yield}

        with patch("buffet_bot.options_engine.get_realtime_data",
                   return_value={"price": price}):
            with patch("buffet_bot.options_engine.compute_edge_score",
                       return_value={"edge_score": edge_score}):
                with patch("buffet_bot.options_engine.find_optimal_csp",
                           return_value=mock_csp):
                    with patch("buffet_bot.options_engine.find_optimal_covered_call",
                               return_value=mock_cc):
                        with patch("buffet_bot.options_engine._get_atr",
                                   return_value=atr):
                            return score_wheel_strategy("AAPL", min_edge_score=65.0)

    # Happy path ───────────────────────────────────────────────────────────────

    def test_returns_dict_with_required_keys(self):
        result = self._run()
        assert result is not None
        for key in ("ticker", "wheel_score", "edge_score",
                    "put_yield", "call_yield", "combined_yield",
                    "atr_pct", "atr_score", "liquidity_score",
                    "price", "csp", "cc", "strategy"):
            assert key in result, f"Missing key: {key!r}"

    def test_wheel_score_within_zero_to_100(self):
        """Composite wheel score must be in [0, 100]."""
        result = self._run()
        assert result is not None
        score = result["wheel_score"]
        assert 0 <= score <= 100, f"wheel_score={score} is outside [0, 100]"

    def test_strategy_field_is_wheel(self):
        result = self._run()
        assert result is not None
        assert result["strategy"] == "WHEEL"

    def test_ticker_is_uppercased(self):
        """score_wheel_strategy should uppercase the ticker in the return dict."""
        result = self._run()
        assert result is not None
        assert result["ticker"] == "AAPL"

    # Edge-score gate ──────────────────────────────────────────────────────────

    def test_returns_none_when_edge_score_below_minimum(self):
        """If edge_score < min_edge_score, return None (wheel not viable)."""
        from buffet_bot.options_engine import score_wheel_strategy

        with patch("buffet_bot.options_engine.get_realtime_data",
                   return_value={"price": 100.0}):
            with patch("buffet_bot.options_engine.compute_edge_score",
                       return_value={"edge_score": 40.0}):
                result = score_wheel_strategy("TSLA", min_edge_score=65.0)

        assert result is None, (
            "score_wheel_strategy should return None when edge_score < min_edge_score"
        )

    def test_returns_none_when_price_is_zero(self):
        """If get_realtime_data returns price=0, return None immediately."""
        from buffet_bot.options_engine import score_wheel_strategy

        with patch("buffet_bot.options_engine.get_realtime_data",
                   return_value={"price": 0}):
            result = score_wheel_strategy("AAPL")

        assert result is None

    # Score component sanity checks ───────────────────────────────────────────

    def test_high_edge_score_produces_nonzero_wheel_score(self):
        """Edge score above minimum with good yields must produce score > 0."""
        result = self._run(edge_score=80.0, put_yield=0.15, call_yield=0.12)
        assert result is not None
        assert result["wheel_score"] > 0

    def test_low_atr_produces_higher_atr_score(self):
        """Lower ATR (more stable stock) → higher atr_score component."""
        low_atr_result  = self._run(atr=0.5)   # very stable
        high_atr_result = self._run(atr=8.0)   # volatile

        assert low_atr_result  is not None
        assert high_atr_result is not None

        assert low_atr_result["atr_score"] > high_atr_result["atr_score"], (
            f"Low ATR atr_score ({low_atr_result['atr_score']}) should be "
            f"greater than high ATR atr_score ({high_atr_result['atr_score']})"
        )

    def test_combined_yield_is_sum_of_put_and_call_yield(self):
        """combined_yield = put_yield + call_yield (rounded to 4dp)."""
        result = self._run(put_yield=0.12, call_yield=0.10)
        assert result is not None
        expected = round(0.12 + 0.10, 4)
        assert abs(result["combined_yield"] - expected) < 1e-6, (
            f"combined_yield={result['combined_yield']} expected {expected}"
        )

    def test_csp_none_does_not_crash(self):
        """If find_optimal_csp returns None (no liquid contracts), must not crash."""
        from buffet_bot.options_engine import score_wheel_strategy

        mock_cc = {"annual_yield": 0.08}

        with patch("buffet_bot.options_engine.get_realtime_data",
                   return_value={"price": 100.0}):
            with patch("buffet_bot.options_engine.compute_edge_score",
                       return_value={"edge_score": 70.0}):
                with patch("buffet_bot.options_engine.find_optimal_csp",
                           return_value=None):    # no CSP found
                    with patch("buffet_bot.options_engine.find_optimal_covered_call",
                               return_value=mock_cc):
                        with patch("buffet_bot.options_engine._get_atr",
                                   return_value=2.0):
                            result = score_wheel_strategy("AAPL", min_edge_score=65.0)

        # Must not raise; put_yield defaults to 0.0 when csp is None
        assert result is not None
        assert result["put_yield"] == 0.0

    def test_cc_none_does_not_crash(self):
        """If find_optimal_covered_call returns None, must not crash."""
        from buffet_bot.options_engine import score_wheel_strategy

        mock_csp = {"annual_yield": 0.10, "oi": 300}

        with patch("buffet_bot.options_engine.get_realtime_data",
                   return_value={"price": 100.0}):
            with patch("buffet_bot.options_engine.compute_edge_score",
                       return_value={"edge_score": 70.0}):
                with patch("buffet_bot.options_engine.find_optimal_csp",
                           return_value=mock_csp):
                    with patch("buffet_bot.options_engine.find_optimal_covered_call",
                               return_value=None):    # no CC found
                        with patch("buffet_bot.options_engine._get_atr",
                                   return_value=2.0):
                            result = score_wheel_strategy("AAPL", min_edge_score=65.0)

        assert result is not None
        assert result["call_yield"] == 0.0

    def test_edge_exception_treated_as_50_score(self):
        """If compute_edge_score raises, edge_val defaults to 50.0.
        This means a min_edge_score=65 should then return None.
        """
        from buffet_bot.options_engine import score_wheel_strategy

        with patch("buffet_bot.options_engine.get_realtime_data",
                   return_value={"price": 100.0}):
            with patch("buffet_bot.options_engine.compute_edge_score",
                       side_effect=Exception("edge error")):
                result = score_wheel_strategy("AAPL", min_edge_score=65.0)

        # edge_val=50 < min_edge_score=65 → should return None
        assert result is None, (
            "When compute_edge_score raises and edge defaults to 50.0, "
            "score_wheel_strategy should return None for min_edge_score=65."
        )
