from __future__ import annotations

import time
from decimal import Decimal
from typing import Dict, Optional, Tuple

from config import FUNDING_REFRESH_INTERVAL
from funding_cache import FundingCache
import extended_pocket_bot as extended
import pacifica_pocket_bot as pacifica


def _normalize_extended_symbol(symbol: str) -> str:
    return symbol.split("-")[0].upper()


def fetch_market_data(
    cache: FundingCache,
) -> Tuple[
    Dict[str, Tuple[str, Dict[str, Optional[float]]]],
    Dict[str, Dict[str, Optional[float]]],
    Dict[str, Optional[Decimal]],
    Dict[str, Optional[Decimal]],
]:
    extended_quotes_raw = extended.list_market_quotes(use_cache=False)
    now = time.time()
    refresh_needed = (
        cache.extended is None
        or cache.pacifica is None
        or cache.last_refresh is None
        or (now - cache.last_refresh) >= FUNDING_REFRESH_INTERVAL
    )

    if refresh_needed:
        extended_funding_raw = extended.get_funding_rates(use_cache=False)
        pacifica_funding_raw = pacifica.get_funding_rates(use_cache=False)
        cache.extended = extended_funding_raw
        cache.pacifica = pacifica_funding_raw
        cache.last_refresh = now
    else:
        extended_funding_raw = cache.extended
        pacifica_funding_raw = cache.pacifica

    assert extended_funding_raw is not None
    assert pacifica_funding_raw is not None
    pacifica_quotes_raw = pacifica.list_market_quotes()

    extended_quotes: Dict[str, Tuple[str, Dict[str, Optional[float]]]] = {}
    for ext_symbol, payload in extended_quotes_raw.items():
        base = _normalize_extended_symbol(ext_symbol)
        extended_quotes[base] = (ext_symbol, payload)

    pacifica_quotes: Dict[str, Dict[str, Optional[float]]] = {}
    for symbol, payload in pacifica_quotes_raw.items():
        bid_entry = payload.get("best_bid") or {}
        ask_entry = payload.get("best_ask") or {}
        bid_price = bid_entry.get("price")
        ask_price = ask_entry.get("price")
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
