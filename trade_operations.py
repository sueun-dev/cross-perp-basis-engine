from __future__ import annotations

import logging
from dataclasses import replace
from decimal import Decimal
from typing import Dict, Iterable, Optional

import extended_pocket_bot as extended
import pacifica_pocket_bot as pacifica

from models import Leg, Opportunity, SymbolExposure


LOGGER = logging.getLogger("main")


class OrphanedLegError(Exception):
    """Raised when an open/close leaves the two venues out of sync.

    Carries the residual :class:`Leg` (with ``orphaned=True``) describing the
    position that is actually live on a single venue, so the caller can keep
    tracking it (for risk caps) and force a reconciliation/unwind instead of
    silently dropping a filled leg or restoring one that was never re-opened.
    """

    def __init__(self, leg: Leg, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.leg = leg
        self.cause = cause


def compute_trade_leg(opportunity: Opportunity, usd_notional: Decimal) -> Optional[Leg]:
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

    if usd_notional <= 0:
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
    buy_base_estimate = usd_notional / buy_price_dec
    sell_base_estimate = usd_notional / sell_price_dec
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
        if not pacifica_opened:
            # Nothing filled on either venue; clean failure, nothing to track.
            raise
        LOGGER.warning(
            "Extended leg failed for %s; attempting to roll back Pacifica position (%s)",
            symbol,
            exc,
        )
        try:
            pacifica.close_position(symbol, leg.pacifica_side, leg.pacifica_amount)
        except Exception as rollback_exc:
            # The Pacifica leg is FILLED and now UN-hedged, and we could not
            # flatten it. Never drop it: hand the orphaned leg back so the
            # caller keeps it tracked (risk caps) and forces an unwind that
            # reconciles against actual venue positions.
            LOGGER.error(
                "Rollback of Pacifica position for %s failed: %s. "
                "Pacifica leg is live and UN-hedged; flagging for forced unwind.",
                symbol,
                rollback_exc,
            )
            orphan = replace(leg, extended_amount=0.0, orphaned=True)
            raise OrphanedLegError(orphan, rollback_exc) from rollback_exc
        # Rollback succeeded: both venues flat. Clean failure, nothing to track.
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
        if not pacifica_closed:
            # Pacifica close failed first: both legs are still open as recorded.
            # Re-raise so the caller keeps the (unchanged) leg.
            raise
        LOGGER.warning(
            "Extended close failed for %s; attempting to restore Pacifica position (%s)",
            symbol,
            exc,
        )
        try:
            pacifica.open_position(symbol, leg.pacifica_side, leg.pacifica_amount)
        except Exception as reopen_exc:
            # Pacifica is now FLAT but Extended is still OPEN. The original leg
            # (both sides open) no longer matches reality. Surface an orphaned
            # leg with only the Extended side so the caller does not later issue
            # a double-close against an already-flat Pacifica.
            LOGGER.error(
                "Failed to restore Pacifica position for %s after close error: %s. "
                "Pacifica is flat but Extended remains open; flagging residual for reconciliation.",
                symbol,
                reopen_exc,
            )
            residual = replace(
                leg, pacifica_amount=Decimal("0"), usd_size=Decimal("0"), orphaned=True
            )
            raise OrphanedLegError(residual, reopen_exc) from reopen_exc
        # Re-open succeeded: both legs open again, matching the recorded leg.
        raise


def close_all_legs(exposure: SymbolExposure) -> None:
    LOGGER.info("Unwinding %s exposure (%d legs)", exposure.base_symbol, len(exposure.legs))
    while exposure.legs:
        leg = exposure.pop_leg()
        if leg is None:
            break
        try:
            execute_close_leg(exposure.base_symbol, exposure.extended_symbol, leg)
        except OrphanedLegError as exc:
            # Venues diverged mid-close (Pacifica flat, Extended still open).
            # Track the reconciled residual that is *actually* live rather than
            # the original both-sides leg, so the next pass does not double-close
            # an already-flat Pacifica. Stop here for manual/forced follow-up.
            LOGGER.error(
                "Hedge close for %s left an orphaned leg: %s", exposure.base_symbol, exc
            )
            exposure.legs.append(exc.leg)
            break
        except Exception as exc:
            LOGGER.exception("Failed to close hedge leg for %s: %s", exposure.base_symbol, exc)
            exposure.legs.append(leg)
            break


def _pacifica_live_amount(symbol: str, side: str) -> Optional[Decimal]:
    """Net live Pacifica base amount for ``symbol`` on ``side`` ("long"/"short").

    Returns ``None`` when the position could not be fetched (so the caller does
    NOT assume the venue is flat and keeps the orphan for retry). Pacifica
    reports order side as "bid"/"ask".
    """
    want = "bid" if side.lower() == "long" else "ask"
    try:
        payload = pacifica.get_positions()
    except Exception as exc:  # noqa: BLE001 - reconciliation must not raise
        LOGGER.error("Could not fetch Pacifica positions for %s: %s", symbol, exc)
        return None
    total = Decimal("0")
    for pos in (payload or {}).get("data", []) or []:
        if str(pos.get("symbol", "")).upper() != symbol.upper():
            continue
        if str(pos.get("side", "")).lower() != want:
            continue
        try:
            total += Decimal(str(pos.get("amount", "0")))
        except Exception:  # noqa: BLE001 - skip malformed rows
            continue
    return total


def _extended_live_amount(extended_symbol: str, side: str) -> Optional[Decimal]:
    """Net live Extended base size for ``extended_symbol`` on ``side``.

    Returns ``None`` when the position could not be fetched. Extended reports
    side as "LONG"/"SHORT" and size under "size".
    """
    want = "long" if side.lower() == "long" else "short"
    try:
        payload = extended.get_positions(market=extended_symbol)
    except Exception as exc:  # noqa: BLE001 - reconciliation must not raise
        LOGGER.error("Could not fetch Extended positions for %s: %s", extended_symbol, exc)
        return None
    total = Decimal("0")
    for pos in (payload or {}).get("data", []) or []:
        market = pos.get("market") or pos.get("symbol")
        if str(market or "").upper() != extended_symbol.upper():
            continue
        if str(pos.get("side", "")).lower() != want:
            continue
        try:
            total += abs(Decimal(str(pos.get("size", "0"))))
        except Exception:  # noqa: BLE001 - skip malformed rows
            continue
    return total


def reconcile_orphan(exposure: SymbolExposure, leg: Leg) -> bool:
    """Force-flatten whatever is *actually* live for an orphaned ``leg``.

    Queries each venue's real positions and issues a reduce-only close for any
    residual that still exists, so the engine's books are realigned with venue
    truth. Returns ``True`` only when BOTH venues are *confirmed* flat for this
    leg (caller may drop it); ``False`` if anything is still live OR could not be
    verified (caller keeps the orphan for a later retry). A fetch failure is
    treated as "not confirmed flat" so a real position is never dropped on the
    strength of a failed query. Never raises.
    """
    symbol = exposure.base_symbol
    extended_symbol = exposure.extended_symbol
    LOGGER.warning("Reconciling orphaned %s leg against live venue positions", symbol)

    pac_confirmed_flat = False
    pac_live = _pacifica_live_amount(symbol, leg.pacifica_side)
    if pac_live is None:
        LOGGER.error("Pacifica position for %s unverifiable; not assuming flat", symbol)
    elif pac_live > 0:
        LOGGER.warning("Pacifica still holds %s %s on %s; closing", pac_live, leg.pacifica_side, symbol)
        try:
            pacifica.close_position(symbol, leg.pacifica_side, pac_live)
            # Re-query so "flat" reflects venue truth, not an assumed success.
            after = _pacifica_live_amount(symbol, leg.pacifica_side)
            pac_confirmed_flat = after is not None and after <= 0
        except Exception as exc:  # noqa: BLE001 - keep going, report not-flat
            LOGGER.error("Reconcile close of Pacifica %s failed: %s", symbol, exc)
    else:
        pac_confirmed_flat = True

    ext_confirmed_flat = False
    ext_live = _extended_live_amount(extended_symbol, leg.extended_side)
    if ext_live is None:
        LOGGER.error("Extended position for %s unverifiable; not assuming flat", extended_symbol)
    elif ext_live > 0:
        LOGGER.warning(
            "Extended still holds %s %s on %s; closing", ext_live, leg.extended_side, extended_symbol
        )
        try:
            extended.close_position(extended_symbol, leg.extended_side, float(ext_live))
            after_ext = _extended_live_amount(extended_symbol, leg.extended_side)
            ext_confirmed_flat = after_ext is not None and after_ext <= 0
        except Exception as exc:  # noqa: BLE001 - keep going, report not-flat
            LOGGER.error("Reconcile close of Extended %s failed: %s", extended_symbol, exc)
    else:
        ext_confirmed_flat = True

    flat = pac_confirmed_flat and ext_confirmed_flat
    if flat:
        LOGGER.info("Orphaned %s leg reconciled flat on both venues", symbol)
    else:
        LOGGER.error(
            "Orphaned %s leg NOT confirmed flat after reconcile "
            "(pacifica_flat=%s, extended_flat=%s); will retry",
            symbol,
            pac_confirmed_flat,
            ext_confirmed_flat,
        )
    return flat


def active_symbols(exposures: Dict[str, SymbolExposure]) -> Iterable[str]:
    for symbol, exposure in exposures.items():
        if exposure.legs:
            yield symbol


def total_exposure_usd(exposures: Dict[str, SymbolExposure]) -> Decimal:
    total = Decimal("0")
    for exposure in exposures.values():
        total += exposure.total_usd
    return total
