from __future__ import annotations

import logging
from decimal import Decimal
from typing import Dict, Iterable, Optional

import extended_pocket_bot as extended
import pacifica_pocket_bot as pacifica

from models import Leg, Opportunity, SymbolExposure


LOGGER = logging.getLogger("main")


def compute_trade_leg(opportunity: Opportunity, usd_notional: Decimal) -> Optional[Leg]:
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
