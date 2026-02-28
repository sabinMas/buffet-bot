"""
buffet_bot/ibkr.py
Interactive Brokers integration via ibapi.
Wraps the event-driven IBKR API in a synchronous threading model.

Requirements:
  - TWS or IB Gateway running locally (default port 7497 for paper / 7496 for live)
  - IBKR_ACCOUNT_ID set in .env
  - ibapi installed: pip install ibapi
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

IBKR_ACCOUNT_ID = os.getenv("IBKR_ACCOUNT_ID", "")
IBKR_HOST       = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT       = int(os.getenv("IBKR_PORT", "7497"))


# ── IBKR client wrapper ───────────────────────────────────────────────────────

def _import_ibapi():
    """Import ibapi, returning (EWrapper, EClient) or (None, None) if not installed."""
    try:
        from ibapi.wrapper import EWrapper
        from ibapi.client import EClient
        return EWrapper, EClient
    except ImportError:
        return None, None


class IBKRClient:
    """
    Synchronous wrapper around IBKR's async EWrapper/EClient.
    Each method blocks until the response arrives (up to *timeout* seconds).
    """

    def __init__(
        self,
        host: str = IBKR_HOST,
        port: int = IBKR_PORT,
        client_id: int = 1,
        timeout: float = 10.0,
    ):
        EWrapper, EClient = _import_ibapi()
        if EWrapper is None:
            raise ImportError(
                "ibapi not installed. Run: pip install ibapi"
            )

        # Build a concrete class combining EWrapper + EClient
        class _App(EWrapper, EClient):  # type: ignore[misc]
            def __init__(inner):
                EClient.__init__(inner, inner)
                inner._account_data: dict[str, Any] = {}
                inner._account_ready = threading.Event()
                inner._next_order_id: int | None = None
                inner._order_ready   = threading.Event()
                inner._errors: list[str] = []

            def error(inner, req_id, error_code, error_string, *args):
                # Codes < 2000 are just warnings/info
                if error_code >= 2000:
                    inner._errors.append(f"[{error_code}] {error_string}")

            def nextValidId(inner, order_id: int):
                inner._next_order_id = order_id
                inner._order_ready.set()

            def updateAccountValue(inner, key, val, currency, account_name):
                inner._account_data[key] = val

            def accountDownloadEnd(inner, account_name):
                inner._account_ready.set()

            def updatePortfolio(inner, *args):
                pass

            def updateAccountTime(inner, time_stamp):
                pass

        self._app     = _App()
        self._host    = host
        self._port    = port
        self._cid     = client_id
        self._timeout = timeout
        self._thread: threading.Thread | None = None

    def connect_and_run(self) -> bool:
        """Connect to TWS/IB Gateway. Returns False if unreachable."""
        try:
            self._app.connect(self._host, self._port, self._cid)
            self._thread = threading.Thread(target=self._app.run, daemon=True)
            self._thread.start()
            # Wait for nextValidId — proof that the connection succeeded
            if not self._app._order_ready.wait(timeout=self._timeout):
                self.disconnect()
                return False
            return True
        except Exception:
            return False

    def get_account_summary(self) -> dict:
        """
        Request account values from IBKR.
        Returns {NetLiquidation, TotalCashValue, BuyingPower} as floats.
        """
        if not self._app.isConnected():
            return {}
        self._app._account_ready.clear()
        self._app._account_data.clear()
        account = IBKR_ACCOUNT_ID or ""
        self._app.reqAccountUpdates(True, account)
        self._app._account_ready.wait(timeout=self._timeout)
        self._app.reqAccountUpdates(False, account)

        raw = self._app._account_data
        def _float(key: str) -> float:
            try:
                return float(raw.get(key, 0))
            except (TypeError, ValueError):
                return 0.0

        return {
            "NetLiquidation": _float("NetLiquidation"),
            "TotalCashValue": _float("TotalCashValue"),
            "BuyingPower":    _float("BuyingPower"),
            "account":        account,
        }

    def place_market_order(self, symbol: str, qty: int, action: str) -> int | None:
        """
        Place a market order. *action* is "BUY" or "SELL".
        Returns the order_id on success or None on failure.
        """
        try:
            from ibapi.contract import Contract
            from ibapi.order import Order

            contract         = Contract()
            contract.symbol  = symbol.upper()
            contract.secType = "STK"
            contract.exchange = "SMART"
            contract.currency = "USD"

            order            = Order()
            order.action     = action.upper()
            order.totalQuantity = qty
            order.orderType  = "MKT"

            oid = self._app._next_order_id
            if oid is None:
                return None
            self._app._next_order_id += 1
            self._app.placeOrder(oid, contract, order)
            return oid
        except Exception:
            return None

    def disconnect(self) -> None:
        try:
            self._app.disconnect()
        except Exception:
            pass


# ── Public helpers ────────────────────────────────────────────────────────────


def get_ibkr_status() -> dict | None:
    """
    Return IBKR account summary dict or None if:
    - IBKR_ACCOUNT_ID not set, or
    - ibapi not installed, or
    - TWS/IB Gateway not reachable.
    """
    if not IBKR_ACCOUNT_ID:
        return None
    try:
        client = IBKRClient()
        if not client.connect_and_run():
            return None
        summary = client.get_account_summary()
        client.disconnect()
        return summary if summary else None
    except ImportError:
        return None
    except Exception:
        return None


def ibkr_market_order(symbol: str, qty: int, action: str) -> bool:
    """
    Connect, place a market order, disconnect.
    Returns True on success.
    """
    if not IBKR_ACCOUNT_ID:
        return False
    try:
        client = IBKRClient()
        if not client.connect_and_run():
            return False
        oid = client.place_market_order(symbol, qty, action)
        time.sleep(1)   # give TWS a moment to accept
        client.disconnect()
        return oid is not None
    except Exception:
        return False
