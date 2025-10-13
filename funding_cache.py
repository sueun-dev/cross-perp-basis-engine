from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional


@dataclass
class FundingCache:
    """Mutable cache for funding-rate lookups shared across loop iterations."""

    last_refresh: Optional[float] = None
    extended: Optional[Dict[str, Dict[str, Optional[Decimal]]]] = None
    pacifica: Optional[Dict[str, Dict[str, Optional[Decimal]]]] = None
