from __future__ import annotations

import logging
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from models import Opportunity


LOGGER = logging.getLogger("main")


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
