from __future__ import annotations

import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

import extended_pocket_bot as extended
import pacifica_pocket_bot as pacifica


# ---- Configuration ----
TRADE_USD = Decimal(os.environ.get("ARBITRAGE_TRADE_USD", "50"))
ENTRY_THRESHOLD = Decimal(
    os.environ.get("ARBITRAGE_MIN_CONTANGO", os.environ.get("ARBITRAGE_ENTRY_THRESHOLD", "0"))
)
EXIT_THRESHOLD = Decimal(os.environ.get("ARBITRAGE_EXIT_THRESHOLD", "0.01"))
MAX_USD_PER_SYMBOL = Decimal(os.environ.get("ARBITRAGE_MAX_USD_PER_SYMBOL", "300"))
MAX_ACTIVE_SYMBOLS = int(os.environ.get("ARBITRAGE_MAX_SYMBOLS", "3"))
POLL_INTERVAL = float(os.environ.get("ARBITRAGE_POLL_INTERVAL", "10"))
LOG_LEVEL = os.environ.get("ARBITRAGE_LOG_LEVEL", "INFO").upper()
TOP_OPP_LOG_COUNT = int(os.environ.get("ARBITRAGE_TOP_OPP_LOG_COUNT", "5"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOGGER = logging.getLogger("main")


@dataclass
class Leg:
    pacifica_side: str
    pacifica_amount: Decimal
    extended_side: str
    extended_amount: float
    usd_size: Decimal


@dataclass
class SymbolExposure:
    base_symbol: str
    extended_symbol: str
    direction: Tuple[str, str]  # (high_exchange, low_exchange)
    legs: List[Leg] = field(default_factory=list)

    @property
    def total_usd(self) -> Decimal:
        total = Decimal("0")
        for leg in self.legs:
            total += leg.usd_size
        return total

    def append_leg(self, leg: Leg) -> None:
        self.legs.append(leg)

    def pop_leg(self) -> Optional[Leg]:
        if not self.legs:
            return None
        return self.legs.pop()

    def clear(self) -> List[Leg]:
        legs = list(self.legs)
        self.legs.clear()
        return legs


@dataclass
class Opportunity:
    base_symbol: str
    extended_symbol: str
    high_exchange: str
    low_exchange: str
    sell_price: float  # price obtainable when selling on the high exchange (best bid)
    buy_price: float  # price payable when buying on the low exchange (best ask)
    ratio: Decimal


def _normalize_extended_symbol(symbol: str) -> str:
    return symbol.split("-")[0].upper()


def _extract_pacifica_price(entry: Optional[Dict[str, float]]) -> Optional[float]:
    if not entry:
        return None
    return entry.get("price")


def fetch_market_data() -> Tuple[
    Dict[str, Tuple[str, Dict[str, Optional[float]]]],
    Dict[str, Dict[str, Optional[float]]],
    Dict[str, Optional[Decimal]],
    Dict[str, Optional[Decimal]],
]:
    extended_quotes_raw = extended.list_market_quotes(use_cache=False)
    extended_funding_raw = extended.get_funding_rates(use_cache=True)
    pacifica_quotes_raw = pacifica.list_market_quotes()
    pacifica_funding_raw = pacifica.get_funding_rates()

    extended_quotes: Dict[str, Tuple[str, Dict[str, Optional[float]]]] = {}
    for ext_symbol, payload in extended_quotes_raw.items():
        base = _normalize_extended_symbol(ext_symbol)
        extended_quotes[base] = (ext_symbol, payload)

    pacifica_quotes: Dict[str, Dict[str, Optional[float]]] = {}
    for symbol, payload in pacifica_quotes_raw.items():
        bid_price = _extract_pacifica_price(payload.get("best_bid"))
        ask_price = _extract_pacifica_price(payload.get("best_ask"))
        pacifica_quotes[symbol.upper()] = {
            "best_bid": bid_price,
            "best_ask": ask_price,
            "mid_price": payload.get("mid_price"),
        }

    extended_funding: Dict[str, Optional[Decimal]] = {}
    for ext_symbol, data in extended_funding_raw.items():
        base = _normalize_extended_symbol(ext_symbol)
        extended_funding[base] = data.get("current")

    pacifica_funding: Dict[str, Optional[Decimal]] = {}
    for symbol, data in pacifica_funding_raw.items():
        pacifica_funding[symbol.upper()] = data.get("current")

    return extended_quotes, pacifica_quotes, extended_funding, pacifica_funding


def evaluate_opportunities(
    extended_quotes: Dict[str, Tuple[str, Dict[str, Optional[float]]]],
    pacifica_quotes: Dict[str, Dict[str, Optional[float]]],
) -> Dict[str, Opportunity]:
    opportunities: Dict[str, Opportunity] = {}
    for base_symbol, (extended_symbol, ext_payload) in extended_quotes.items():
        pac_payload = pacifica_quotes.get(base_symbol)
        if not pac_payload:
            continue
        ext_bid = ext_payload.get("best_bid")
        ext_ask = ext_payload.get("best_ask")
        pac_bid = pac_payload.get("best_bid")
        pac_ask = pac_payload.get("best_ask")

        candidates: List[Opportunity] = []
        if ext_bid and pac_ask and pac_ask > 0:
            ratio = Decimal(ext_bid / pac_ask - 1)
            candidates.append(
                Opportunity(
                    base_symbol=base_symbol,
                    extended_symbol=extended_symbol,
                    high_exchange="extended",
                    low_exchange="pacifica",
                    sell_price=ext_bid,
                    buy_price=pac_ask,
                    ratio=ratio,
                )
            )
        if pac_bid and ext_ask and ext_ask > 0:
            ratio = Decimal(pac_bid / ext_ask - 1)
            candidates.append(
                Opportunity(
                    base_symbol=base_symbol,
                    extended_symbol=extended_symbol,
                    high_exchange="pacifica",
                    low_exchange="extended",
                    sell_price=pac_bid,
                    buy_price=ext_ask,
                    ratio=ratio,
                )
            )
        if not candidates:
            continue
        best = max(candidates, key=lambda opp: opp.ratio)
        opportunities[base_symbol] = best
    return opportunities


def compute_net_funding(
    base_symbol: str,
    high_exchange: str,
    low_exchange: str,
    pacifica_funding: Dict[str, Optional[Decimal]],
    extended_funding: Dict[str, Optional[Decimal]],
) -> Optional[Decimal]:
    pac_rate = pacifica_funding.get(base_symbol)
    ext_rate = extended_funding.get(base_symbol)
    if pac_rate is None or ext_rate is None:
        return None
    if high_exchange == "extended":
        short_rate = ext_rate
        long_rate = pac_rate
    else:
        short_rate = pac_rate
        long_rate = ext_rate
    return short_rate - long_rate


def funding_is_favorable(
    opportunity: Opportunity,
    pacifica_funding: Dict[str, Optional[Decimal]],
    extended_funding: Dict[str, Optional[Decimal]],
) -> bool:
    net = compute_net_funding(
        opportunity.base_symbol,
        opportunity.high_exchange,
        opportunity.low_exchange,
        pacifica_funding,
        extended_funding,
    )
    if net is None:
        LOGGER.debug("Skipping %s: missing funding data", opportunity.base_symbol)
        return False
    if net < Decimal("0"):
        LOGGER.debug(
            "Skipping %s: unfavorable funding (net %.8f)",
            opportunity.base_symbol,
            float(net),
        )
        return False
    return True


def _compute_trade_leg(opportunity: Opportunity, usd_notional: Decimal) -> Optional[Leg]:
    usd = float(usd_notional)
    if opportunity.high_exchange == "extended":
        extended_side = "short"
        extended_price = opportunity.sell_price
        pacifica_side = "long"
        pacifica_price = opportunity.buy_price
    else:
        extended_side = "long"
        extended_price = opportunity.buy_price
        pacifica_side = "short"
        pacifica_price = opportunity.sell_price

    pac_amount = pacifica.usd_to_base(opportunity.base_symbol, usd, price=pacifica_price)
    ext_amount = extended.usd_to_base(opportunity.extended_symbol, usd, price=extended_price)

    if pac_amount <= 0 or ext_amount <= 0:
        return None

    return Leg(
        pacifica_side=pacifica_side,
        pacifica_amount=pac_amount,
        extended_side=extended_side,
        extended_amount=ext_amount,
        usd_size=usd_notional,
    )


def execute_open_leg(symbol: str, extended_symbol: str, leg: Leg) -> None:
    LOGGER.info(
        "Opening hedge %s: Pacifica %s %.8f, Extended %s %.8f",
        symbol,
        leg.pacifica_side,
        float(leg.pacifica_amount),
        leg.extended_side,
        leg.extended_amount,
    )
    pacifica.open_position(symbol, leg.pacifica_side, leg.pacifica_amount)
    extended.open_position(extended_symbol, leg.extended_side, leg.extended_amount)


def execute_close_leg(symbol: str, extended_symbol: str, leg: Leg) -> None:
    LOGGER.info(
        "Closing hedge %s: Pacifica %s %.8f, Extended %s %.8f",
        symbol,
        leg.pacifica_side,
        float(leg.pacifica_amount),
        leg.extended_side,
        leg.extended_amount,
    )
    pacifica.close_position(symbol, leg.pacifica_side, leg.pacifica_amount)
    extended.close_position(extended_symbol, leg.extended_side, leg.extended_amount)


def close_all_legs(exposure: SymbolExposure) -> None:
    LOGGER.info("Unwinding %s exposure (%d legs)", exposure.base_symbol, len(exposure.legs))
    while exposure.legs:
        leg = exposure.pop_leg()
        if leg is None:
            break
        try:
            execute_close_leg(exposure.base_symbol, exposure.extended_symbol, leg)
        except Exception as exc:
            LOGGER.exception("Failed to close hedge leg for %s: %s", exposure.base_symbol, exc)
            exposure.legs.append(leg)
            break


def active_symbols(exposures: Dict[str, SymbolExposure]) -> Iterable[str]:
    for symbol, exposure in exposures.items():
        if exposure.legs:
            yield symbol


def run() -> None:
    exposures: Dict[str, SymbolExposure] = {}
    stop_requested = False

    def _handle_stop(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        LOGGER.info("Received signal %s, preparing to exit...", signum)
        stop_requested = True

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    while not stop_requested:
        try:
            (
                extended_quotes,
                pacifica_quotes,
                extended_funding,
                pacifica_funding,
            ) = fetch_market_data()
            opportunities = evaluate_opportunities(extended_quotes, pacifica_quotes)
            sorted_opportunities = sorted(
                opportunities.values(), key=lambda opp: opp.ratio, reverse=True
            )

            if sorted_opportunities and LOGGER.isEnabledFor(logging.INFO):
                positive_slice = [opp for opp in sorted_opportunities if opp.ratio > 0]
                log_slice = positive_slice[:TOP_OPP_LOG_COUNT]
                LOGGER.info(
                    "Top spreads: %s",
                    ", ".join(
                        f"{opp.base_symbol}:{(opp.ratio * 100):.2f}%" for opp in log_slice
                    ),
                )

            # First unwind exposures that lost their edge or flipped direction.
            for symbol in list(exposures.keys()):
                exposure = exposures[symbol]
                opp = opportunities.get(symbol)
                if not exposure.legs:
                    exposures.pop(symbol, None)
                    continue
                if opp is None or opp.high_exchange != exposure.direction[0] or opp.low_exchange != exposure.direction[1]:
                    close_all_legs(exposure)
                    if not exposure.legs:
                        exposures.pop(symbol, None)
                    continue
                net_funding = compute_net_funding(
                    symbol,
                    exposure.direction[0],
                    exposure.direction[1],
                    pacifica_funding,
                    extended_funding,
                )
                if net_funding is not None and net_funding < Decimal("0"):
                    LOGGER.info(
                        "Funding turned unfavorable for %s (net %.8f); closing positions",
                        symbol,
                        float(net_funding),
                    )
                    close_all_legs(exposure)
                    if not exposure.legs:
                        exposures.pop(symbol, None)
                    continue
                if opp.ratio < EXIT_THRESHOLD:
                    LOGGER.info(
                        "Contango for %s dropped to %.4f (< exit %.4f); closing positions",
                        symbol,
                        float(opp.ratio),
                        float(EXIT_THRESHOLD),
                    )
                    close_all_legs(exposure)
                    if not exposure.legs:
                        exposures.pop(symbol, None)

            # Evaluate potential new entries.
            for opportunity in sorted_opportunities:
                symbol = opportunity.base_symbol
                if opportunity.ratio <= ENTRY_THRESHOLD:
                    continue
                if not funding_is_favorable(opportunity, pacifica_funding, extended_funding):
                    continue
                exposure = exposures.get(symbol)
                if exposure is None:
                    if len(list(active_symbols(exposures))) >= MAX_ACTIVE_SYMBOLS:
                        continue
                    exposure = SymbolExposure(
                        base_symbol=symbol,
                        extended_symbol=opportunity.extended_symbol,
                        direction=(opportunity.high_exchange, opportunity.low_exchange),
                    )
                    exposures[symbol] = exposure
                if exposure.direction != (opportunity.high_exchange, opportunity.low_exchange):
                    continue
                remaining = MAX_USD_PER_SYMBOL - exposure.total_usd
                if remaining < TRADE_USD:
                    continue
                leg = _compute_trade_leg(opportunity, TRADE_USD)
                if leg is None:
                    continue
                try:
                    execute_open_leg(symbol, exposure.extended_symbol, leg)
                    exposure.append_leg(leg)
                except Exception as exc:
                    LOGGER.exception("Failed to open hedge for %s: %s", symbol, exc)
                    continue

            time.sleep(POLL_INTERVAL)
        except Exception as exc:
            LOGGER.exception("Arbitrage loop error: %s", exc)
            time.sleep(POLL_INTERVAL)

    LOGGER.info("Stopping arbitrage loop, closing all open positions...")
    for exposure in list(exposures.values()):
        close_all_legs(exposure)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user, exiting.")
        sys.exit(0)
