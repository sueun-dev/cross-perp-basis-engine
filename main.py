from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Dict

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
from models import SymbolExposure
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


def run() -> None:
    exposures: Dict[str, SymbolExposure] = {}
    funding_cache = FundingCache()
    try:
        while True:
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
                execute_open_leg(symbol, exposure.extended_symbol, leg)
                exposure.append_leg(leg)

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        LOGGER.info("Keyboard interrupt received, stopping trading loop...")
    finally:
        LOGGER.info("Stopping arbitrage loop, closing all open positions...")
        for exposure in list(exposures.values()):
            close_all_legs(exposure)


if __name__ == "__main__":
    run()
