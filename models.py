from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional, Tuple


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
