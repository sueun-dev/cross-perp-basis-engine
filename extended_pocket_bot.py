from __future__ import annotations

import asyncio
import atexit
import os
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from typing import Any, Awaitable, Dict, Iterable, Optional, Tuple, TYPE_CHECKING, TypeVar, cast

import requests
from requests import HTTPError

from env_loader import load_env

load_env()


@dataclass
class StarknetDomainConfig:
    name: str
    version: str
    chain_id: str
    revision: str


@dataclass
class EndpointConfigData:
    chain_rpc_url: str
    api_base_url: str
    stream_url: str
    onboarding_url: str
    signing_domain: str
    collateral_asset_contract: str
    asset_operations_contract: str
    collateral_asset_on_chain_id: str
    collateral_decimals: int
    collateral_asset_id: str
    starknet_domain: StarknetDomainConfig


if TYPE_CHECKING:
    from x10.perpetual.accounts import StarkPerpetualAccount as StarkPerpetualAccountType
    from x10.perpetual.configuration import EndpointConfig as X10EndpointConfigType, StarknetDomain as X10StarknetDomainType
    from x10.perpetual.orders import OrderSide as OrderSideType, TimeInForce as TimeInForceType
    from x10.perpetual.trading_client import PerpetualTradingClient as PerpetualTradingClientType
    from x10.utils.http import ResponseStatus as ResponseStatusType
else:
    StarkPerpetualAccountType = Any
    X10EndpointConfigType = Any
    X10StarknetDomainType = Any
    OrderSideType = Any
    TimeInForceType = Any
    PerpetualTradingClientType = Any
    ResponseStatusType = Any


_AsyncResultT = TypeVar("_AsyncResultT")


MAINNET_CONFIG_DATA = EndpointConfigData(
    chain_rpc_url="",
    api_base_url="https://api.starknet.extended.exchange/api/v1",
    stream_url="wss://api.starknet.extended.exchange/stream.extended.exchange/v1",
    onboarding_url="https://api.starknet.extended.exchange",
    signing_domain="extended.exchange",
    collateral_asset_contract="",
    asset_operations_contract="",
    collateral_asset_on_chain_id="0x1",
    collateral_decimals=6,
    collateral_asset_id="0x1",
    starknet_domain=StarknetDomainConfig(name="Perpetuals", version="v0", chain_id="SN_MAIN", revision="1"),
)


def _with_overrides(config: EndpointConfigData, *, api_base_url: str, stream_url: str) -> EndpointConfigData:
    return EndpointConfigData(
        chain_rpc_url=config.chain_rpc_url,
        api_base_url=api_base_url,
        stream_url=stream_url,
        onboarding_url=config.onboarding_url,
        signing_domain=config.signing_domain,
        collateral_asset_contract=config.collateral_asset_contract,
        asset_operations_contract=config.asset_operations_contract,
        collateral_asset_on_chain_id=config.collateral_asset_on_chain_id,
        collateral_decimals=config.collateral_decimals,
        collateral_asset_id=config.collateral_asset_id,
        starknet_domain=config.starknet_domain,
    )


try:
    from x10.perpetual.accounts import StarkPerpetualAccount
    from x10.perpetual.configuration import EndpointConfig as X10EndpointConfig, StarknetDomain as X10StarknetDomain
    from x10.perpetual.orders import OrderSide, TimeInForce
    from x10.perpetual.trading_client import PerpetualTradingClient
    from x10.utils.http import ResponseStatus
except ImportError as exc:  # pragma: no cover - allow module import without SDK
    StarkPerpetualAccount = None  # type: ignore[assignment]
    X10EndpointConfig = None  # type: ignore[assignment]
    X10StarknetDomain = None  # type: ignore[assignment]
    OrderSide = None  # type: ignore[assignment]
    TimeInForce = None  # type: ignore[assignment]
    PerpetualTradingClient = None  # type: ignore[assignment]
    ResponseStatus = None  # type: ignore[assignment]
    _X10_IMPORT_ERROR: Optional[ImportError] = exc
else:
    _X10_IMPORT_ERROR = None


# ---- Configuration ----
API_KEY = os.environ.get("EXTENDED_API_KEY", "").strip()
STARK_PRIVATE_KEY = os.environ.get("EXTENDED_PRIVATE_KEY", "").strip()
STARK_PUBLIC_KEY = os.environ.get("EXTENDED_PUBLIC_KEY", "").strip()
STARK_VAULT_ID = os.environ.get("EXTENDED_VAULT_ID", "").strip()
BASE_URL_OVERRIDE = os.environ.get("EXTENDED_BASE_URL", "").strip()
STREAM_URL_OVERRIDE = os.environ.get("EXTENDED_STREAM_URL", "").strip()
USER_AGENT = os.environ.get("EXTENDED_USER_AGENT", "extended-pocket-bot/1.0").strip()

if not API_KEY:
    raise RuntimeError("EXTENDED_API_KEY 환경변수가 필요합니다.")
if not STARK_PRIVATE_KEY or not STARK_PUBLIC_KEY or not STARK_VAULT_ID:
    raise RuntimeError(
        "EXTENDED_PRIVATE_KEY, EXTENDED_PUBLIC_KEY, EXTENDED_VAULT_ID 환경변수가 필요합니다."
    )

API_BASE_URL = BASE_URL_OVERRIDE or MAINNET_CONFIG_DATA.api_base_url
STREAM_URL = STREAM_URL_OVERRIDE or MAINNET_CONFIG_DATA.stream_url
REST = API_BASE_URL.rstrip("/")

HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": USER_AGENT,
    "X-Api-Key": API_KEY,
}

SESSION = requests.Session()

_TRADING_CLIENT: Optional[PerpetualTradingClientType] = None
_STARK_ACCOUNT: Optional[StarkPerpetualAccountType] = None
_ENDPOINT_CONFIG: Optional[X10EndpointConfigType] = None


def _require_time_in_force_enum() -> TimeInForceType:
    if TimeInForce is None:
        raise RuntimeError("TimeInForce enum unavailable. Ensure x10-python-trading-starknet is installed.")
    return cast(TimeInForceType, TimeInForce)


def _require_order_side_enum() -> OrderSideType:
    if OrderSide is None:
        raise RuntimeError("OrderSide enum unavailable. Ensure x10-python-trading-starknet is installed.")
    return cast(OrderSideType, OrderSide)


def _api_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{REST}{path}"
    response = SESSION.get(url, params=params, headers=HEADERS, timeout=10)
    try:
        response.raise_for_status()
    except HTTPError as exc:
        raise
    payload = response.json()
    status = payload.get("status")
    if isinstance(status, str) and status.upper() == "ERROR":
        error_info = payload.get("error") or {}
        code = error_info.get("code", "UNKNOWN")
        message = error_info.get("message", "Extended API error")
        raise RuntimeError(f"Extended API error {code}: {message}")
    return payload


_MARKET_CACHE: Dict[str, Dict[str, Any]] = {}
_ORDERBOOK_CACHE: Dict[str, Tuple[float, float]] = {}
_ORDERBOOK_CACHE_TIMESTAMP: Dict[str, float] = {}
_FEE_CACHE: Dict[str, Decimal] = {}
_LEVERAGE_CACHE: Dict[str, Decimal] = {}


def _get_market_info(symbol: str) -> Dict[str, Any]:
    symbol = symbol.upper()
    cached = _MARKET_CACHE.get(symbol)
    if cached:
        return cached
    data = _api_get("/info/markets", params={"market": symbol})
    markets = data.get("data") or []
    if not markets:
        raise RuntimeError(f"Market config for {symbol} not found")
    market_info = markets[0]
    _MARKET_CACHE[symbol] = market_info
    return market_info


def get_all_market_info(use_cache: bool = True) -> Dict[str, Dict[str, Any]]:
    if use_cache and _MARKET_CACHE:
        return dict(_MARKET_CACHE)
    payload = _api_get("/info/markets")
    markets = payload.get("data") or []
    for entry in markets:
        symbol = (entry.get("market") or entry.get("symbol") or entry.get("name") or "").upper()
        if not symbol:
            continue
        _MARKET_CACHE[symbol] = entry
    return dict(_MARKET_CACHE)


def list_symbols() -> Iterable[str]:
    return sorted(get_all_market_info().keys())


def list_market_quotes(use_cache: bool = True) -> Dict[str, Dict[str, Optional[float]]]:
    markets = get_all_market_info(use_cache=use_cache)
    quotes: Dict[str, Dict[str, Optional[float]]] = {}
    for symbol, info in markets.items():
        stats = info.get("marketStats") or {}
        bid_raw = stats.get("bidPrice")
        ask_raw = stats.get("askPrice")
        mark_raw = stats.get("markPrice") or stats.get("lastPrice")
        def _to_float(value: Any) -> Optional[float]:
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        quotes[symbol] = {
            "best_bid": _to_float(bid_raw),
            "best_ask": _to_float(ask_raw),
            "mid_price": _to_float(mark_raw),
        }
    return quotes


def get_funding_rates(symbol: Optional[str] = None, *, use_cache: bool = True) -> Dict[str, Dict[str, Optional[Decimal]]]:
    markets = get_all_market_info(use_cache=use_cache)
    symbols: Iterable[str]
    if symbol:
        symbol_key = symbol.upper()
        if symbol_key not in markets:
            raise RuntimeError(f"Market config for {symbol} not found")
        symbols = [symbol_key]
    else:
        symbols = markets.keys()

    funding: Dict[str, Dict[str, Optional[Decimal]]] = {}

    def _to_decimal(value: Any) -> Optional[Decimal]:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    for sym in symbols:
        stats = markets[sym].get("marketStats") or {}
        current_rate = _to_decimal(stats.get("fundingRate"))
        next_raw = stats.get("nextFundingRate")
        next_rate: Optional[Decimal] = None
        next_timestamp: Optional[Decimal] = None
        if isinstance(next_raw, (int, float)):
            if next_raw > 1:
                next_timestamp = Decimal(str(next_raw))
            else:
                next_rate = _to_decimal(next_raw)
        else:
            next_rate = _to_decimal(next_raw)
        funding[sym] = {
            "current": current_rate,
            "next": next_rate,
            "next_event_timestamp": next_timestamp,
        }
    return funding


def _round_quantity(symbol: str, amount: Decimal, rounding=ROUND_CEILING) -> Decimal:
    market = _get_market_info(symbol)
    trading_config = market.get("tradingConfig") or {}
    step_str = trading_config.get("minOrderSizeChange") or trading_config.get("minOrderSize")
    if not step_str:
        return amount
    step = Decimal(str(step_str))
    if step == 0:
        return amount
    normalized = (amount / step).to_integral_value(rounding=rounding) * step
    min_size_str = trading_config.get("minOrderSize") or step_str
    min_size = Decimal(str(min_size_str))
    if normalized < min_size and amount > 0:
        normalized = min_size
    return normalized


def _round_price(symbol: str, price: Decimal, rounding=ROUND_HALF_UP) -> Decimal:
    market = _get_market_info(symbol)
    trading_config = market.get("tradingConfig") or {}
    tick_str = trading_config.get("minPriceChange")
    if not tick_str:
        return price
    tick = Decimal(str(tick_str))
    if tick == 0:
        return price
    ticks = (price / tick).to_integral_value(rounding=rounding)
    rounded = ticks * tick
    if rounded <= 0:
        rounded = tick
    return rounded


def _get_orderbook(symbol: str, use_cache: bool = True) -> Tuple[float, float]:
    now = time.time()
    if use_cache and symbol in _ORDERBOOK_CACHE and now - _ORDERBOOK_CACHE_TIMESTAMP.get(symbol, 0) < 1.0:
        return _ORDERBOOK_CACHE[symbol]

    payload = _api_get(f"/info/markets/{symbol}/orderbook")
    data = payload.get("data") or {}
    bids = data.get("bid") or []
    asks = data.get("ask") or []
    if not bids or not asks:
        raise RuntimeError(f"Orderbook data missing for {symbol}")

    def _extract(level: Any) -> float:
        if isinstance(level, dict):
            price = level.get("price") or level.get("p")
        elif isinstance(level, (list, tuple)) and level:
            price = level[0]
        else:
            price = None
        if price is None:
            raise RuntimeError(f"Invalid orderbook level for {symbol}: {level}")
        return float(price)

    best_bid = _extract(bids[0])
    best_ask = _extract(asks[0])
    if best_bid <= 0 or best_ask <= 0:
        raise RuntimeError(f"Invalid prices in orderbook for {symbol}")

    _ORDERBOOK_CACHE[symbol] = (best_bid, best_ask)
    _ORDERBOOK_CACHE_TIMESTAMP[symbol] = now
    return best_bid, best_ask


def get_mid_price(symbol: str) -> float:
    best_bid, best_ask = _get_orderbook(symbol, use_cache=False)
    return (best_bid + best_ask) / 2.0


def usd_to_base(symbol: str, usd_amount: float, price: Optional[float] = None) -> float:
    if usd_amount <= 0:
        return 0.0
    price = price or get_mid_price(symbol)
    if price <= 0:
        raise ValueError("Invalid price for conversion")
    price_dec = Decimal(str(price))
    usd_dec = Decimal(str(usd_amount))
    raw_amount = usd_dec / price_dec
    rounded = _round_quantity(symbol, raw_amount, rounding=ROUND_CEILING)
    return float(rounded)


def get_positions(market: Optional[str] = None, side: Optional[str] = None) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if market:
        params["market"] = market
    if side:
        params["side"] = side.upper()
    payload = _api_get("/user/positions", params=params or None)
    return payload


def get_balances() -> Dict[str, Any]:
    try:
        payload = _api_get("/user/balance")
    except HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise RuntimeError("Balance fetch returned 404 (likely zero balance).") from exc
        raise
    return payload


def _ensure_trading_client() -> PerpetualTradingClientType:
    if (
        _X10_IMPORT_ERROR is not None
        or PerpetualTradingClient is None
        or StarkPerpetualAccount is None
        or X10EndpointConfig is None
        or X10StarknetDomain is None
        or OrderSide is None
        or TimeInForce is None
    ):
        raise RuntimeError(
            "x10-python-trading-starknet 패키지가 설치되어야 주문 기능을 사용할 수 있습니다. "
            "pip install git+https://github.com/x10xchange/python_sdk.git#egg=x10-python-trading-starknet"
        ) from _X10_IMPORT_ERROR

    global _TRADING_CLIENT, _STARK_ACCOUNT, _ENDPOINT_CONFIG

    if _TRADING_CLIENT is not None:
        return _TRADING_CLIENT

    if not STARK_PRIVATE_KEY or not STARK_PUBLIC_KEY or not STARK_VAULT_ID:
        raise RuntimeError("EXTENDED_PRIVATE_KEY, EXTENDED_PUBLIC_KEY, EXTENDED_VAULT_ID 환경변수가 필요합니다.")
    if not STARK_PRIVATE_KEY.startswith("0x") or not STARK_PUBLIC_KEY.startswith("0x"):
        raise ValueError("Stark 키는 0x로 시작하는 16진수 문자열이어야 합니다.")

    _STARK_ACCOUNT = cast(
        StarkPerpetualAccountType,
        StarkPerpetualAccount(
            vault=STARK_VAULT_ID,
            private_key=STARK_PRIVATE_KEY,
            public_key=STARK_PUBLIC_KEY,
            api_key=API_KEY,
        ),
    )

    config_for_client = MAINNET_CONFIG_DATA
    if BASE_URL_OVERRIDE or STREAM_URL_OVERRIDE:
        config_for_client = _with_overrides(MAINNET_CONFIG_DATA, api_base_url=API_BASE_URL, stream_url=STREAM_URL)

    domain = config_for_client.starknet_domain
    _ENDPOINT_CONFIG = cast(
        X10EndpointConfigType,
        X10EndpointConfig(
            chain_rpc_url=config_for_client.chain_rpc_url,
            api_base_url=config_for_client.api_base_url,
            stream_url=config_for_client.stream_url,
            onboarding_url=config_for_client.onboarding_url,
            signing_domain=config_for_client.signing_domain,
            collateral_asset_contract=config_for_client.collateral_asset_contract,
            asset_operations_contract=config_for_client.asset_operations_contract,
            collateral_asset_on_chain_id=config_for_client.collateral_asset_on_chain_id,
            collateral_decimals=config_for_client.collateral_decimals,
            collateral_asset_id=config_for_client.collateral_asset_id,
            starknet_domain=X10StarknetDomain(
                name=domain.name,
                version=domain.version,
                chain_id=domain.chain_id,
                revision=domain.revision,
            ),
        ),
    )

    _TRADING_CLIENT = cast(PerpetualTradingClientType, PerpetualTradingClient(_ENDPOINT_CONFIG, _STARK_ACCOUNT))
    return _TRADING_CLIENT


_EVENT_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _get_event_loop() -> asyncio.AbstractEventLoop:
    global _EVENT_LOOP
    if _EVENT_LOOP is None or _EVENT_LOOP.is_closed():
        _EVENT_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_EVENT_LOOP)
    return _EVENT_LOOP


def _run_async(coro: Awaitable[_AsyncResultT]) -> _AsyncResultT:
    loop = _get_event_loop()
    return loop.run_until_complete(coro)


def _get_fee(symbol: str) -> Decimal:
    symbol = symbol.upper()
    cached = _FEE_CACHE.get(symbol)
    if cached is not None:
        return cached
    try:
        payload = _api_get("/user/fees", params={"market": symbol})
        fee_entries = payload.get("data") or []
        if fee_entries:
            entry = fee_entries[0]
            taker_fee_rate = Decimal(str(entry.get("takerFeeRate", "0.00025")))
        else:
            taker_fee_rate = Decimal("0.00025")
    except Exception:
        taker_fee_rate = Decimal("0.00025")
    _FEE_CACHE[symbol] = taker_fee_rate
    return taker_fee_rate


def _ensure_leverage(client: PerpetualTradingClientType, symbol: str, leverage: Decimal) -> None:
    symbol = symbol.upper()
    cached = _LEVERAGE_CACHE.get(symbol)
    if cached == leverage:
        return
    response = _run_async(client.account.update_leverage(market_name=symbol, leverage=leverage))
    response_status = cast(Optional[ResponseStatusType], ResponseStatus)
    if response_status is not None:
        status = getattr(response, "status", None)
        if status != response_status.OK:
            raise RuntimeError(f"Failed to set leverage for {symbol}: {response}")
    _LEVERAGE_CACHE[symbol] = leverage


def _place_order(
    symbol: str,
    side: str,
    amount: float,
    price: Decimal,
    *,
    reduce_only: bool,
    time_in_force: Optional[TimeInForceType] = None,
) -> Dict[str, Any]:
    amount_dec = Decimal(str(amount))
    amount_dec = _round_quantity(symbol, amount_dec, rounding=ROUND_HALF_UP)
    if amount_dec <= 0:
        raise ValueError("Order size must be greater than zero")

    rounded_price = _round_price(symbol, price, rounding=ROUND_HALF_UP)
    if rounded_price <= 0:
        raise ValueError("Order price must be greater than zero")

    client = _ensure_trading_client()
    if not reduce_only:
        _ensure_leverage(client, symbol, Decimal("1"))
    time_in_force_enum = _require_time_in_force_enum()
    tif = time_in_force or time_in_force_enum.IOC
    order_side_enum = _require_order_side_enum()
    order_side = order_side_enum.BUY if side.lower() == "buy" else order_side_enum.SELL

    response = _run_async(
        client.place_order(
            market_name=symbol.upper(),
            amount_of_synthetic=amount_dec,
            price=rounded_price,
            side=order_side,
            post_only=False,
            time_in_force=tif,
            reduce_only=reduce_only,
        )
    )

    data = response.model_dump()
    data["feeRateUsed"] = str(_get_fee(symbol))
    data["roundedPrice"] = str(rounded_price)
    data["roundedAmount"] = str(amount_dec)
    return data


def open_position(symbol: str, side: str, amount: float, slippage_percent: float = 0.75) -> Dict[str, Any]:
    side = side.lower()
    if side not in ("long", "short", "buy", "sell"):
        raise ValueError("side must be 'long' or 'short'")

    best_bid, best_ask = _get_orderbook(symbol)
    slippage = Decimal(str(slippage_percent)) / Decimal("100")
    if side in ("long", "buy"):
        reference = Decimal(str(best_ask))
        price = reference * (Decimal("1") + slippage)
        order_side = "buy"
    else:
        reference = Decimal(str(best_bid))
        price = reference * (Decimal("1") - slippage)
        order_side = "sell"

    return _place_order(symbol, order_side, amount, price, reduce_only=False)


def close_position(symbol: str, side: str, amount: float, slippage_percent: float = 0.75) -> Dict[str, Any]:
    side = side.lower()
    if side not in ("long", "short", "buy", "sell"):
        raise ValueError("side must be 'long' or 'short'")

    best_bid, best_ask = _get_orderbook(symbol)
    slippage = Decimal(str(slippage_percent)) / Decimal("100")
    if side in ("long", "buy"):
        reference = Decimal(str(best_bid))
        price = reference * (Decimal("1") - slippage)
        order_side = "sell"
    else:
        reference = Decimal(str(best_ask))
        price = reference * (Decimal("1") + slippage)
        order_side = "buy"

    return _place_order(symbol, order_side, amount, price, reduce_only=True)


@atexit.register
def _cleanup():
    try:
        if _TRADING_CLIENT is not None:
            _run_async(_TRADING_CLIENT.close())
    except Exception:
        pass
    finally:
        if _EVENT_LOOP is not None and not _EVENT_LOOP.is_closed():
            _EVENT_LOOP.close()
        SESSION.close()


if __name__ == "__main__":
    SYMBOL = os.environ.get("EXTENDED_SYMBOL", "BTC-USD").strip() or "BTC-USD"
    USD_SIZE = float(os.environ.get("EXTENDED_TEST_USD", "10"))
    WAIT_SECONDS = float(os.environ.get("EXTENDED_TEST_SLEEP", "5"))

    print("REST Endpoint:", REST)
    print("Stream Endpoint:", STREAM_URL)
    print(f"Test symbol: {SYMBOL}, notional: ${USD_SIZE}, wait {WAIT_SECONDS}s")

    try:
        balances = get_balances()
        print("Balance:", balances)
    except Exception as exc:
        print(f"[WARN] Balance fetch failed: {exc}")

    try:
        positions_before = get_positions(market=SYMBOL)
        print("Initial Positions:", positions_before)
    except Exception as exc:
        print(f"[WARN] Position fetch failed: {exc}")
        positions_before = {"data": []}

    try:
        first_amount = usd_to_base(SYMBOL, USD_SIZE)
        print(f"[STEP 1] USD {USD_SIZE} -> {first_amount} {SYMBOL}")

        print("① Opening first long position")
        first_order = open_position(SYMBOL, "long", first_amount)
        print("First order response:", first_order)

        time.sleep(WAIT_SECONDS)

        second_amount = usd_to_base(SYMBOL, USD_SIZE)
        print(f"[STEP 2] USD {USD_SIZE} -> {second_amount} {SYMBOL}")

        print("② Opening second long position")
        second_order = open_position(SYMBOL, "long", second_amount)
        print("Second order response:", second_order)

        time.sleep(WAIT_SECONDS)

        mid_positions = get_positions(market=SYMBOL)
        print("Positions after two longs:", mid_positions)
        total_long = sum(
            float(pos.get("size", "0"))
            for pos in mid_positions.get("data", [])
            if pos.get("market") == SYMBOL and pos.get("side") == "LONG"
        )
        print(f"Accumulated long size: {total_long}")

        close_amount = usd_to_base(SYMBOL, USD_SIZE)
        print(f"[STEP 3] Closing amount derived from USD {USD_SIZE}: {close_amount}")
        print("③ Closing one chunk")
        close_order = close_position(SYMBOL, "long", close_amount)
        print("Close order response:", close_order)

        print("④ Checking remaining positions")
        remaining_positions = get_positions(market=SYMBOL)
        print("Remaining positions:", remaining_positions)

        for pos in remaining_positions.get("data", []):
            if pos.get("market") != SYMBOL:
                continue
            remaining_size = float(pos.get("size", "0"))
            if remaining_size == 0:
                continue
            pos_side = "long" if pos.get("side") == "LONG" else "short"
            print(f"⑤ Closing residual {pos_side} size {remaining_size}")
            residual_resp = close_position(SYMBOL, pos_side, abs(remaining_size))
            print("Residual close response:", residual_resp)
    except Exception as exc:
        print(f"[ERROR] Test flow failed: {exc}")
