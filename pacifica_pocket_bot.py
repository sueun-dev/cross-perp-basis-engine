from __future__ import annotations

import os
import time
import uuid
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from typing import Any, Dict, Iterable, Optional, Tuple

import requests
from requests import HTTPError
from solders.keypair import Keypair

from pacifica_python_sdk.common.constants import REST_URL
from pacifica_python_sdk.common.utils import sign_message

from env_loader import load_env

load_env()

# ---- Configuration ----
ACCOUNT = os.environ.get("PACIFICA_ACCOUNT", "").strip()
AGENT_PRIVATE_KEY = os.environ.get("PACIFICA_AGENT_PRIVATE_KEY", "").strip()
API_KEY = os.environ.get("PACIFICA_API_KEY", "").strip()

if not ACCOUNT:
    raise RuntimeError("PACIFICA_ACCOUNT (퍼블릭키) 환경변수가 필요합니다.")
if not AGENT_PRIVATE_KEY:
    raise RuntimeError("PACIFICA_AGENT_PRIVATE_KEY 환경변수가 필요합니다.")
if not API_KEY:
    raise RuntimeError("PACIFICA_API_KEY 환경변수가 필요합니다.")

if AGENT_PRIVATE_KEY and API_KEY:
    AGENT_KEYPAIR = Keypair.from_base58_string(AGENT_PRIVATE_KEY)
    AGENT_PUBLIC_KEY = str(AGENT_KEYPAIR.pubkey())
else:
    AGENT_KEYPAIR = None
    AGENT_PUBLIC_KEY = None

REST = REST_URL.rstrip("/")
HEADERS = {"Content-Type": "application/json"}
if API_KEY:
    HEADERS["x-api-key"] = API_KEY


_MARKET_INFO_CACHE: Dict[str, Dict[str, Any]] = {}
_MARKET_INFO_LOADED = False
_ACCOUNT_SETTINGS_CACHE: Dict[str, Tuple[bool, int]] = {}


def _get_orderbook(symbol: str) -> Dict[str, Any]:
    response = requests.get(
        f"{REST}/book",
        params={"symbol": symbol},
        headers=HEADERS,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_mid_price(symbol: str) -> float:
    book = _get_orderbook(symbol)
    data = book.get("data", {})
    levels = data.get("l") or []
    if len(levels) < 2 or not levels[0] or not levels[1]:
        raise RuntimeError(f"Orderbook data missing for {symbol}")
    bids, asks = levels[0], levels[1]
    best_bid = float(bids[0]["p"])
    best_ask = float(asks[0]["p"])
    return (best_bid + best_ask) / 2.0


def _ensure_market_info_loaded(force_refresh: bool = False) -> None:
    global _MARKET_INFO_LOADED
    if force_refresh:
        _MARKET_INFO_CACHE.clear()
        _MARKET_INFO_LOADED = False
    if _MARKET_INFO_LOADED:
        return
    response = requests.get(f"{REST}/info", headers=HEADERS, timeout=10)
    response.raise_for_status()
    payload = response.json()
    markets = payload.get("data")
    if not isinstance(markets, list):
        raise RuntimeError("Invalid response from /info endpoint")
    for market in markets:
        market_symbol = market.get("symbol")
        if market_symbol:
            _MARKET_INFO_CACHE[market_symbol.upper()] = market
    _MARKET_INFO_LOADED = True


def _get_market_specs(symbol: str) -> Dict[str, Any]:
    symbol_key = symbol.upper()
    _ensure_market_info_loaded()
    if symbol_key not in _MARKET_INFO_CACHE:
        raise RuntimeError(f"Symbol {symbol} not found in market info")
    return _MARKET_INFO_CACHE[symbol_key]


def _get_lot_size(symbol: str) -> Decimal:
    market = _get_market_specs(symbol)
    lot_str = market.get("lot_size")
    if lot_str is None:
        raise RuntimeError(f"Lot size missing for {symbol}")
    lot_size = Decimal(str(lot_str))
    if lot_size <= 0:
        raise RuntimeError(f"Invalid lot size {lot_size} for {symbol}")
    return lot_size


def _get_min_notional(symbol: str) -> Decimal:
    market = _get_market_specs(symbol)
    min_order = market.get("min_order_size")
    if min_order is None:
        return Decimal("0")
    min_notional = Decimal(str(min_order))
    if min_notional < 0:
        raise RuntimeError(f"Invalid min_order_size {min_notional} for {symbol}")
    return min_notional


def round_base_amount(
    symbol: str,
    amount: Decimal | float | str,
    *,
    price: Decimal | float | None = None,
    rounding=ROUND_CEILING,
) -> Decimal:
    if isinstance(amount, Decimal):
        amount_dec = amount
    elif isinstance(amount, (int, float)):
        amount_dec = Decimal(str(amount))
    else:
        amount_dec = Decimal(amount)
    lot_size = _get_lot_size(symbol)
    multiples = (amount_dec / lot_size).to_integral_value(rounding=rounding)
    rounded = multiples * lot_size
    if rounded <= 0:
        rounded = lot_size

    min_notional = _get_min_notional(symbol)
    if min_notional > 0:
        price_dec = price if isinstance(price, Decimal) else (Decimal(str(price)) if price is not None else Decimal(str(get_mid_price(symbol))))
        if rounded * price_dec < min_notional:
            multiples = (min_notional / price_dec / lot_size).to_integral_value(rounding=ROUND_CEILING)
            rounded = multiples * lot_size

    return rounded


def _lot_decimals(symbol: str) -> int:
    lot_size = _get_lot_size(symbol)
    return abs(lot_size.as_tuple().exponent)


def list_symbols() -> Iterable[str]:
    _ensure_market_info_loaded()
    return sorted(_MARKET_INFO_CACHE.keys())


def _get_top_of_book(symbol: str) -> Dict[str, Any]:
    book = _get_orderbook(symbol)
    data = book.get("data", {})
    levels = data.get("l") or []
    bids = levels[0] if len(levels) > 0 else []
    asks = levels[1] if len(levels) > 1 else []

    top_bid = bids[0] if bids else None
    top_ask = asks[0] if asks else None

    result: Dict[str, Any] = {
        "best_bid": None,
        "best_ask": None,
        "mid_price": None,
    }

    if top_bid:
        result["best_bid"] = {
            "price": float(top_bid["p"]),
            "amount": float(top_bid["a"]),
        }
    if top_ask:
        result["best_ask"] = {
            "price": float(top_ask["p"]),
            "amount": float(top_ask["a"]),
        }

    if result["best_bid"] and result["best_ask"]:
        bid_price = Decimal(str(result["best_bid"]["price"]))
        ask_price = Decimal(str(result["best_ask"]["price"]))
        result["mid_price"] = float((bid_price + ask_price) / 2)
    elif result["best_bid"]:
        result["mid_price"] = result["best_bid"]["price"]
    elif result["best_ask"]:
        result["mid_price"] = result["best_ask"]["price"]

    return result


def list_market_quotes(use_cache: bool = True) -> Dict[str, Dict[str, Any]]:
    _ensure_market_info_loaded(force_refresh=not use_cache)
    quotes: Dict[str, Dict[str, Any]] = {}
    for symbol in list_symbols():
        try:
            quotes[symbol] = _get_top_of_book(symbol)
        except (HTTPError, requests.RequestException) as exc:
            quotes[symbol] = {"error": str(exc)}
    return quotes


def get_funding_rates(
    symbol: str | None = None,
    use_cache: bool = True,
) -> Dict[str, Dict[str, Optional[Decimal]]]:
    _ensure_market_info_loaded(force_refresh=not use_cache)
    symbols: Iterable[str]
    if symbol:
        symbol_upper = symbol.upper()
        if symbol_upper not in _MARKET_INFO_CACHE:
            raise RuntimeError(f"Symbol {symbol} not found in market info")
        symbols = [symbol_upper]
    else:
        symbols = list_symbols()

    funding: Dict[str, Dict[str, Optional[Decimal]]] = {}

    def _to_decimal(value: Any) -> Optional[Decimal]:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    for sym in symbols:
        market = _MARKET_INFO_CACHE[sym]
        funding[sym] = {
            "current": _to_decimal(market.get("funding_rate")),
            "next": _to_decimal(market.get("next_funding_rate")),
        }
    return funding


def usd_to_base(symbol: str, usd_amount: float, price: float | None = None) -> Decimal:
    price = price or get_mid_price(symbol)
    if price <= 0:
        raise ValueError("Invalid price for conversion")
    lot_size = _get_lot_size(symbol)
    price_dec = Decimal(str(price))
    raw_amount = Decimal(str(usd_amount)) / price_dec
    raw_lots = raw_amount / lot_size
    lots = raw_lots.to_integral_value(rounding=ROUND_CEILING)
    if lots == 0 and raw_amount > 0:
        lots = Decimal(1)
    normalized = lots * lot_size
    min_notional = _get_min_notional(symbol) or Decimal("10")
    if normalized * price_dec < min_notional:
        lots = (
            min_notional / price_dec / lot_size
        ).to_integral_value(rounding=ROUND_CEILING)
        if lots == 0:
            lots = Decimal(1)
        normalized = lots * lot_size
    return normalized


def format_base_amount(symbol: str, amount: Decimal | float | str) -> str:
    if isinstance(amount, Decimal):
        amount_dec = amount
    elif isinstance(amount, str):
        amount_dec = Decimal(amount)
    else:
        amount_dec = Decimal(str(amount))
    lot_size = _get_lot_size(symbol)
    quantized = amount_dec.quantize(lot_size, rounding=ROUND_HALF_UP)
    return f"{quantized:.{_lot_decimals(symbol)}f}"


def _sign_and_post(path: str, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not AGENT_KEYPAIR or not AGENT_PUBLIC_KEY:
        raise RuntimeError("에이전트 키가 없으면 이 작업을 실행할 수 없습니다.")

    timestamp = int(time.time() * 1_000)
    header = {"timestamp": timestamp, "expiry_window": 5_000, "type": action}
    _, signature = sign_message(header, payload, AGENT_KEYPAIR)
    body = {
        "account": ACCOUNT,
        "agent_wallet": AGENT_PUBLIC_KEY,
        "signature": signature,
        "timestamp": timestamp,
        "expiry_window": header["expiry_window"],
        **payload,
    }
    response = requests.post(f"{REST}{path}", json=body, headers=HEADERS, timeout=10)
    if not response.ok:
        print(f"[ERROR] {path} {response.status_code}: {response.text}")
        response.raise_for_status()
    return response.json()


def _ensure_account_action(
    path: str,
    action: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    response = _sign_and_post(path, action, payload)
    if not response.get("success", True):
        raise RuntimeError(f"{action} failed: {response}")
    return response


def _update_margin_mode(symbol: str, is_isolated: bool) -> None:
    payload = {
        "symbol": symbol.upper(),
        "is_isolated": bool(is_isolated),
    }
    _ensure_account_action("/account/margin", "update_margin_mode", payload)


def _update_leverage(symbol: str, leverage: int) -> None:
    payload = {
        "symbol": symbol.upper(),
        "leverage": int(leverage),
    }
    _ensure_account_action("/account/leverage", "update_leverage", payload)


def _ensure_margin_settings(symbol: str, *, leverage: int = 1, is_isolated: bool = True) -> None:
    symbol_upper = symbol.upper()
    target = (bool(is_isolated), int(leverage))
    cached = _ACCOUNT_SETTINGS_CACHE.get(symbol_upper)
    if cached == target:
        return
    _update_margin_mode(symbol_upper, target[0])
    _update_leverage(symbol_upper, target[1])
    _ACCOUNT_SETTINGS_CACHE[symbol_upper] = target


def get_positions() -> Dict[str, Any]:
    response = requests.get(
        f"{REST}/positions",
        params={"account": ACCOUNT},
        headers=HEADERS,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_balances() -> Dict[str, Any]:
    response = requests.get(
        f"{REST}/account",
        params={"account": ACCOUNT},
        headers=HEADERS,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def open_position(
    symbol: str,
    side: str,
    amount: Decimal | float | str,
    slippage_percent: float = 0.5,
) -> Dict[str, Any]:
    _ensure_margin_settings(symbol, leverage=1, is_isolated=True)
    order_side = "bid" if side.lower() == "long" else "ask"
    amount_str = format_base_amount(symbol, amount)
    payload = {
        "symbol": symbol,
        "reduce_only": False,
        "amount": amount_str,
        "side": order_side,
        "slippage_percent": str(slippage_percent),
        "client_order_id": str(uuid.uuid4()),
    }
    return _sign_and_post("/orders/create_market", "create_market_order", payload)


def close_position(
    symbol: str,
    side: str,
    amount: Decimal | float | str,
    slippage_percent: float = 0.5,
) -> Dict[str, Any]:
    _ensure_margin_settings(symbol, leverage=1, is_isolated=True)
    exit_side = "ask" if side.lower() == "long" else "bid"
    amount_str = format_base_amount(symbol, amount)
    payload = {
        "symbol": symbol,
        "reduce_only": True,
        "amount": amount_str,
        "side": exit_side,
        "slippage_percent": str(slippage_percent),
        "client_order_id": str(uuid.uuid4()),
    }
    return _sign_and_post("/orders/create_market", "create_market_order", payload)


if __name__ == "__main__":
    if os.environ.get("PACIFICA_LIST_PRICES") == "1":
        print("Fetching market quotes for all markets...")
        quotes = list_market_quotes()
        for symbol in quotes:
            entry = quotes[symbol]
            if "error" in entry:
                print(f"{symbol}: unavailable ({entry['error']})")
                continue
            best_bid = entry.get("best_bid") or {}
            best_ask = entry.get("best_ask") or {}
            mid = entry.get("mid_price")
            bid_price = best_bid.get("price")
            ask_price = best_ask.get("price")
            current_price = f"{mid:.6f}" if mid is not None else "n/a"
            buy_now = f"{ask_price:.6f}" if ask_price is not None else "n/a"
            sell_now = f"{bid_price:.6f}" if bid_price is not None else "n/a"
            print(
                f"{symbol}: 현재 {current_price} | 바로살수있는 {buy_now} | 바로팔수있는 {sell_now}"
            )
        raise SystemExit(0)

    print("Account:", ACCOUNT)
    print("Positions:", get_positions())
    try:
        print("Balances:", get_balances())
    except RuntimeError as exc:
        print(f"Balance fetch failed: {exc}")

    if not AGENT_KEYPAIR:
        print("Agent keys not configured. Skipping trade test.")
        raise SystemExit(0)

    symbol = "BNB"
    usd_lot = 10.0  # Pacifica minimum market order notional is 10 USD

    mid_price = get_mid_price(symbol)
    base_amount1 = usd_to_base(symbol, usd_lot, mid_price)
    base_amount1_str = format_base_amount(symbol, base_amount1)
    print(f"Opening {usd_lot}$ short on {symbol} ({base_amount1_str} units) at {mid_price:.2f}")
    resp1 = open_position(symbol, "short", base_amount1)
    print("Open short #1:", resp1)

    time.sleep(5)

    mid_price2 = get_mid_price(symbol)
    base_amount2 = usd_to_base(symbol, usd_lot, mid_price2)
    base_amount2_str = format_base_amount(symbol, base_amount2)
    print(
        f"Opening another {usd_lot}$ short on {symbol} ({base_amount2_str} units) at {mid_price2:.2f}"
    )
    resp2 = open_position(symbol, "short", base_amount2)
    print("Open short #2:", resp2)

    time.sleep(5)

    mid_price3 = get_mid_price(symbol)
    base_amount_close = usd_to_base(symbol, usd_lot, mid_price3)
    base_amount_close_str = format_base_amount(symbol, base_amount_close)
    print(
        f"Closing one {usd_lot}$ short on {symbol} ({base_amount_close_str} units) at {mid_price3:.2f}"
    )
    resp3 = close_position(symbol, "short", base_amount_close)
    print("Close partial:", resp3)

    time.sleep(5)

    positions_after = get_positions()
    remaining_amount = Decimal("0")
    for pos in positions_after.get("data", []):
        if pos.get("symbol") == symbol and pos.get("side") == "ask":
            remaining_amount += Decimal(pos.get("amount", "0"))

    if remaining_amount > 0:
        lot_size = _get_lot_size(symbol)
        remaining_decimal = remaining_amount.quantize(lot_size, rounding=ROUND_HALF_UP)
        remaining_str = format_base_amount(symbol, remaining_decimal)
        mid_price4 = get_mid_price(symbol)
        print(f"Closing remaining short ({remaining_str} units) at {mid_price4:.2f}")
        resp4 = close_position(symbol, "short", remaining_decimal)
        print("Close remaining:", resp4)
    else:
        print("No remaining short position detected.")
