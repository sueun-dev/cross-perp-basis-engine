from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Dict, List, Optional

from app_logging import configure as configure_logging
from config import (
    ENTRY_THRESHOLD,
    EXIT_THRESHOLD,
    MAX_ACTIVE_SYMBOLS,
    MAX_TOTAL_USD,
    MAX_USD_PER_SYMBOL,
    POLL_INTERVAL,
    TOP_OPP_LOG_COUNT,
    TRADE_USD,
    TAKE_PROFIT_THRESHOLD,
)
from funding_cache import FundingCache
from market_data import fetch_market_data
from models import Opportunity, SymbolExposure
from opportunity_analysis import (
    compute_net_funding,
    evaluate_opportunities,
    funding_is_favorable,
)
from trade_operations import (
    active_symbols,
    close_all_legs,
    compute_trade_leg,
    execute_open_leg,
    total_exposure_usd,
)


LOGGER = configure_logging("main")


def _log_top_spreads(sorted_opportunities: List[Opportunity]) -> None:
    if not sorted_opportunities or not LOGGER.isEnabledFor(logging.INFO):
        return
    positive_slice = [opp for opp in sorted_opportunities if opp.ratio > 0]
    log_slice = positive_slice[:TOP_OPP_LOG_COUNT]
    if not log_slice:
        return
    LOGGER.info(
        "Top spreads: %s",
        ", ".join(f"{opp.base_symbol}:{(opp.ratio * 100):.2f}%" for opp in log_slice),
    )


def _unwind_stale_exposures(
    exposures: Dict[str, SymbolExposure],
    opportunities: Dict[str, Opportunity],
    pacifica_funding: Dict[str, Optional[Decimal]],
    extended_funding: Dict[str, Optional[Decimal]],
) -> None:
    """Close any exposure that lost its edge, flipped direction, or hit a target."""
    for symbol in list(exposures.keys()):
        exposure = exposures[symbol]
        opp = opportunities.get(symbol)
        if not exposure.legs:
            exposures.pop(symbol, None)
            continue
        # Direction flipped or the symbol disappeared from the opportunity set.
        if (
            opp is None
            or opp.high_exchange != exposure.direction[0]
            or opp.low_exchange != exposure.direction[1]
        ):
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


def _evaluate_entries(
    exposures: Dict[str, SymbolExposure],
    sorted_opportunities: List[Opportunity],
    pacifica_funding: Dict[str, Optional[Decimal]],
    extended_funding: Dict[str, Optional[Decimal]],
) -> None:
    """Open new hedge legs for opportunities that clear the entry filters."""
    for opportunity in sorted_opportunities:
        symbol = opportunity.base_symbol
        if opportunity.ratio <= ENTRY_THRESHOLD:
            continue
        if not funding_is_favorable(opportunity, pacifica_funding, extended_funding):
            continue

        direction = (opportunity.high_exchange, opportunity.low_exchange)
        exposure = exposures.get(symbol)
        is_new = exposure is None
        if is_new:
            if len(list(active_symbols(exposures))) >= MAX_ACTIVE_SYMBOLS:
                continue
            exposure = SymbolExposure(
                base_symbol=symbol,
                extended_symbol=opportunity.extended_symbol,
                direction=direction,
            )
        elif exposure.direction != direction:
            continue

        remaining = MAX_USD_PER_SYMBOL - exposure.total_usd
        if remaining <= Decimal("0"):
            continue
        leg = compute_trade_leg(opportunity, TRADE_USD)
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
        except Exception as exc:  # noqa: BLE001 - keep the loop alive on a single bad order
            LOGGER.exception("Failed to open hedge leg for %s: %s", symbol, exc)
            continue

        exposure.append_leg(leg)
        # Only register a brand-new exposure once it actually holds a leg, so a
        # symbol that never fills does not linger as an empty entry.
        if is_new:
            exposures[symbol] = exposure


def process_iteration(
    exposures: Dict[str, SymbolExposure], funding_cache: FundingCache
) -> None:
    """Run one full poll cycle: fetch data, unwind stale legs, evaluate entries."""
    (
        extended_quotes,
        pacifica_quotes,
        extended_funding,
        pacifica_funding,
    ) = fetch_market_data(funding_cache)
    opportunities = evaluate_opportunities(extended_quotes, pacifica_quotes)
    sorted_opportunities = sorted(
        opportunities.values(), key=lambda opp: opp.ratio, reverse=True
    )

    _log_top_spreads(sorted_opportunities)
    _unwind_stale_exposures(exposures, opportunities, pacifica_funding, extended_funding)
    _evaluate_entries(exposures, sorted_opportunities, pacifica_funding, extended_funding)


def run() -> None:
    exposures: Dict[str, SymbolExposure] = {}
    funding_cache = FundingCache()
    try:
        while True:
            try:
                process_iteration(exposures, funding_cache)
            except KeyboardInterrupt:
                raise
            except Exception:  # noqa: BLE001 - a single bad cycle must not kill the loop
                LOGGER.exception(
                    "Unexpected error in trading loop; retrying after %.1fs", POLL_INTERVAL
                )
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        LOGGER.info("Keyboard interrupt received, stopping trading loop...")
    finally:
        LOGGER.info("Stopping arbitrage loop, closing all open positions...")
        for exposure in list(exposures.values()):
            try:
                close_all_legs(exposure)
            except Exception as exc:  # noqa: BLE001 - close the rest even if one fails
                LOGGER.exception(
                    "Failed to close %s during shutdown: %s", exposure.base_symbol, exc
                )


if __name__ == "__main__":
    run()
