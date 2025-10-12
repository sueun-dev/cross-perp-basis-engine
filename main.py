from __future__ import annotations

import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from config import (
    ENTRY_THRESHOLD,
    EXIT_THRESHOLD,
    LOG_LEVEL,
    FUNDING_REFRESH_INTERVAL,
    MAX_ACTIVE_SYMBOLS,
    MAX_TOTAL_USD,
    MAX_USD_PER_SYMBOL,
    POLL_INTERVAL,
    TOP_OPP_LOG_COUNT,
    TRADE_USD,
    TAKE_PROFIT_THRESHOLD,
)
import extended_pocket_bot as extended
import pacifica_pocket_bot as pacifica


logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOGGER = logging.getLogger("main")

_LAST_FUNDING_REFRESH: Optional[float] = None
_EXTENDED_FUNDING_CACHE: Optional[Dict[str, Dict[str, Optional[Decimal]]]] = None
_PACIFICA_FUNDING_CACHE: Optional[Dict[str, Dict[str, Optional[Decimal]]]] = None


@dataclass
class Leg:
    pacifica_side: str
    pacifica_amount: Decimal
    extended_side: str
    extended_amount: float
    usd_size: Decimal
    entry_ratio: Decimal


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
    global _LAST_FUNDING_REFRESH, _EXTENDED_FUNDING_CACHE, _PACIFICA_FUNDING_CACHE

    now = time.time()
    refresh_needed = (
        _EXTENDED_FUNDING_CACHE is None
        or _PACIFICA_FUNDING_CACHE is None
        or _LAST_FUNDING_REFRESH is None
        or (now - _LAST_FUNDING_REFRESH) >= FUNDING_REFRESH_INTERVAL
    )

    if refresh_needed:
        extended_funding_raw = extended.get_funding_rates(use_cache=False)
        pacifica_funding_raw = pacifica.get_funding_rates(use_cache=False)
        _EXTENDED_FUNDING_CACHE = extended_funding_raw
        _PACIFICA_FUNDING_CACHE = pacifica_funding_raw
        _LAST_FUNDING_REFRESH = now
    else:
        extended_funding_raw = _EXTENDED_FUNDING_CACHE
        pacifica_funding_raw = _PACIFICA_FUNDING_CACHE

    assert extended_funding_raw is not None
    assert pacifica_funding_raw is not None
    pacifica_quotes_raw = pacifica.list_market_quotes()

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
    pac_price_dec = Decimal(str(pacifica_price))
    ext_price_dec = Decimal(str(extended_price))

    if pac_price_dec <= 0 or ext_price_dec <= 0:
        return None

    target_usd = Decimal(usd_notional)
    if target_usd <= 0:
        return None

    if opportunity.high_exchange == "extended":
        # Long Pacifica (buy), short Extended (sell)
        buy_price_dec = pac_price_dec
        sell_price_dec = ext_price_dec

        def _round_buy(amount: Decimal) -> Decimal:
            return pacifica.round_base_amount(opportunity.base_symbol, amount, price=buy_price_dec)

        def _round_sell(amount: Decimal) -> Decimal:
            return extended.round_base_amount(opportunity.extended_symbol, amount)
    else:
        # Long Extended (buy), short Pacifica (sell)
        buy_price_dec = ext_price_dec
        sell_price_dec = pac_price_dec

        def _round_buy(amount: Decimal) -> Decimal:
            return extended.round_base_amount(opportunity.extended_symbol, amount)

        def _round_sell(amount: Decimal) -> Decimal:
            return pacifica.round_base_amount(opportunity.base_symbol, amount, price=sell_price_dec)

    # Initial guesses based on desired USD notional
    buy_base_estimate = Decimal(str(usd_notional)) / buy_price_dec
    sell_base_estimate = Decimal(str(usd_notional)) / sell_price_dec
    base_target = max(buy_base_estimate, sell_base_estimate)

    for _ in range(8):
        if base_target <= 0:
            return None
        buy_base = _round_buy(base_target)
        sell_base = _round_sell(base_target)
        new_target = max(buy_base, sell_base)
        if new_target == base_target:
            break
        base_target = new_target
    else:
        buy_base = _round_buy(base_target)
        sell_base = _round_sell(base_target)

    if buy_base <= 0 or sell_base <= 0:
        return None

    pac_amount_dec: Decimal
    ext_amount_dec: Decimal
    if opportunity.high_exchange == "extended":
        pac_amount_dec = buy_base
        ext_amount_dec = sell_base
    else:
        pac_amount_dec = sell_base
        ext_amount_dec = buy_base

    pac_usd = pac_amount_dec * pac_price_dec
    ext_usd = ext_amount_dec * ext_price_dec
    actual_usd = max(pac_usd, ext_usd)

    return Leg(
        pacifica_side=pacifica_side,
        pacifica_amount=pac_amount_dec,
        extended_side=extended_side,
        extended_amount=float(ext_amount_dec),
        usd_size=actual_usd,
        entry_ratio=opportunity.ratio,
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
    pacifica_opened = False
    try:
        pacifica.open_position(symbol, leg.pacifica_side, leg.pacifica_amount)
        pacifica_opened = True
        extended.open_position(extended_symbol, leg.extended_side, leg.extended_amount)
    except Exception as exc:
        if pacifica_opened:
            LOGGER.warning(
                "Extended leg failed for %s; attempting to roll back Pacifica position (%s)",
                symbol,
                exc,
            )
            try:
                pacifica.close_position(symbol, leg.pacifica_side, leg.pacifica_amount)
            except Exception as rollback_exc:
                LOGGER.error(
                    "Rollback of Pacifica position for %s failed: %s", symbol, rollback_exc
                )
        raise


def execute_close_leg(symbol: str, extended_symbol: str, leg: Leg) -> None:
    LOGGER.info(
        "Closing hedge %s: Pacifica %s %.8f, Extended %s %.8f",
        symbol,
        leg.pacifica_side,
        float(leg.pacifica_amount),
        leg.extended_side,
        leg.extended_amount,
    )
    pacifica_closed = False
    try:
        pacifica.close_position(symbol, leg.pacifica_side, leg.pacifica_amount)
        pacifica_closed = True
        extended.close_position(extended_symbol, leg.extended_side, leg.extended_amount)
    except Exception as exc:
        if pacifica_closed:
            LOGGER.warning(
                "Extended close failed for %s; attempting to restore Pacifica position (%s)",
                symbol,
                exc,
            )
            try:
                pacifica.open_position(symbol, leg.pacifica_side, leg.pacifica_amount)
            except Exception as reopen_exc:
                LOGGER.error(
                    "Failed to restore Pacifica position for %s after close error: %s",
                    symbol,
                    reopen_exc,
                )
        raise


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


def total_exposure_usd(exposures: Dict[str, SymbolExposure]) -> Decimal:
    total = Decimal("0")
    for exposure in exposures.values():
        total += exposure.total_usd
    return total


def run() -> None:
    exposures: Dict[str, SymbolExposure] = {}
    stop_requested = False

    def _handle_stop(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        if not stop_requested:
            LOGGER.info("Received signal %s, preparing to exit...", signum)
        stop_requested = True

    signal.signal(signal.SIGTERM, _handle_stop)

    try:
        while not stop_requested:
            try:
                (
                    extended_quotes,
                    pacifica_quotes,
                    extended_funding,
                    pacifica_funding,
                ) = fetch_market_data() # Done
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
                    if opp is not None:
                        profit_reached = any(
                            (leg.entry_ratio - opp.ratio) >= TAKE_PROFIT_THRESHOLD for leg in exposure.legs
                        )
                        if profit_reached:
                            first_leg = exposure.legs[0]
                            LOGGER.info(
                                "Take-profit reached for %s: entry %.4f -> current %.4f (>= %.4f)",
                                symbol,
                                float(first_leg.entry_ratio),
                                float(opp.ratio),
                                float(TAKE_PROFIT_THRESHOLD),
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
                        continue

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
                    if remaining <= Decimal("0"):
                        continue
                    leg = _compute_trade_leg(opportunity, TRADE_USD)
                    if leg is None:
                        continue
                    if leg.usd_size > remaining:
                        LOGGER.debug(
                            "Skipping %s: rounded notional %.2f exceeds remaining per-symbol cap %.2f",
                            symbol,
                            float(leg.usd_size),
                            float(remaining),
                        )
                        continue
                    projected_total = total_exposure_usd(exposures) + leg.usd_size
                    if projected_total > MAX_TOTAL_USD:
                        LOGGER.debug(
                            "Skipping %s: rounded notional pushes total exposure to %.2f (> %.2f)",
                            symbol,
                            float(projected_total),
                            float(MAX_TOTAL_USD),
                        )
                        continue
                    try:
                        execute_open_leg(symbol, exposure.extended_symbol, leg)
                        exposure.append_leg(leg)
                    except Exception as exc:
                        LOGGER.exception("Failed to open hedge for %s: %s", symbol, exc)
                        continue

                if not stop_requested:
                    time.sleep(POLL_INTERVAL)
            except Exception as exc:
                LOGGER.exception("Arbitrage loop error: %s", exc)
                if not stop_requested:
                    time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        LOGGER.info("Keyboard interrupt received, stopping trading loop...")
        stop_requested = True
    finally:
        LOGGER.info("Stopping arbitrage loop, closing all open positions...")
        for exposure in list(exposures.values()):
            close_all_legs(exposure)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user, exiting.")
        sys.exit(0)
