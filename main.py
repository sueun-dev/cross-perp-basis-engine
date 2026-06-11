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
    REQUIRE_FLAT_START,
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
    OrphanedLegError,
    assert_startup_flat,
    active_symbols,
    close_all_legs,
    compute_trade_leg,
    execute_open_leg,
    reconcile_orphan,
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


def _close_and_drop_if_empty(
    exposures: Dict[str, SymbolExposure], symbol: str, exposure: SymbolExposure
) -> None:
    """Unwind every leg of an exposure and forget the symbol once it is flat."""
    close_all_legs(exposure)
    if not exposure.legs:
        exposures.pop(symbol, None)


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
            _close_and_drop_if_empty(exposures, symbol, exposure)
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
            _close_and_drop_if_empty(exposures, symbol, exposure)
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
            _close_and_drop_if_empty(exposures, symbol, exposure)
            continue
        if opp.ratio < EXIT_THRESHOLD:
            LOGGER.info(
                "Contango for %s dropped to %.4f (< exit %.4f); closing positions",
                symbol,
                float(opp.ratio),
                float(EXIT_THRESHOLD),
            )
            _close_and_drop_if_empty(exposures, symbol, exposure)


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
        existing = exposures.get(symbol)
        is_new = existing is None
        if existing is None:
            if len(list(active_symbols(exposures))) >= MAX_ACTIVE_SYMBOLS:
                continue
            exposure = SymbolExposure(
                base_symbol=symbol,
                extended_symbol=opportunity.extended_symbol,
                direction=direction,
            )
        else:
            if existing.direction != direction:
                continue
            exposure = existing
        # Past this point `exposure` is always present: it was either fetched
        # from the map or freshly constructed above (the only other branch
        # continues), so the bookkeeping below operates on a real exposure.
        assert exposure is not None

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
        except OrphanedLegError as exc:
            # A leg filled on Pacifica but the hedge failed AND the rollback
            # failed: there is now a live, UN-hedged position. Never drop it.
            # Record the orphaned leg so it is tracked and counted against risk
            # caps, register the exposure, and let the next cycle force-unwind
            # it via reconciliation against actual venue positions.
            LOGGER.error(
                "Naked Pacifica position left open for %s after failed hedge+rollback; "
                "recording orphaned leg for forced unwind: %s",
                symbol,
                exc,
            )
            exposure.append_leg(exc.leg)
            exposures[symbol] = exposure
            continue
        except Exception as exc:  # noqa: BLE001 - keep the loop alive on a single bad order
            LOGGER.exception("Failed to open hedge leg for %s: %s", symbol, exc)
            continue

        exposure.append_leg(leg)
        # Only register a brand-new exposure once it actually holds a leg, so a
        # symbol that never fills does not linger as an empty entry.
        if is_new:
            exposures[symbol] = exposure


def _reconcile_orphans(exposures: Dict[str, SymbolExposure]) -> None:
    """Force-flatten any orphaned legs against live venue positions.

    An orphaned leg means the two venues are known to be out of sync (a hedge
    whose rollback failed, or an unwind whose re-open failed). Until it is
    reconciled it is NOT a clean delta-neutral hedge, so resolve it before doing
    anything else. A leg confirmed flat on both venues is dropped; one that is
    still live (or unverifiable) is kept for the next cycle to retry.
    """
    for symbol in list(exposures.keys()):
        exposure = exposures[symbol]
        if not any(leg.orphaned for leg in exposure.legs):
            continue
        remaining: List = []
        for leg in exposure.legs:
            if not leg.orphaned:
                remaining.append(leg)
                continue
            if not reconcile_orphan(exposure, leg):
                remaining.append(leg)  # still live/unverifiable; keep retrying
        exposure.legs = remaining
        if not exposure.legs:
            exposures.pop(symbol, None)


def process_iteration(
    exposures: Dict[str, SymbolExposure], funding_cache: FundingCache
) -> None:
    """Run one full poll cycle: fetch data, unwind stale legs, evaluate entries."""
    # Resolve any known venue desync first so caps and unwind logic operate on
    # an accurate book.
    _reconcile_orphans(exposures)
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
    if REQUIRE_FLAT_START:
        LOGGER.info("Checking both venues are flat before starting with an empty book...")
        assert_startup_flat()
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
