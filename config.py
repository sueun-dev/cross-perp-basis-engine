from __future__ import annotations

import os
from decimal import Decimal

from env_loader import load_env

load_env()


TRADE_USD = Decimal(os.environ.get("ARBITRAGE_TRADE_USD", "20"))
ENTRY_THRESHOLD = Decimal(
    os.environ.get("ARBITRAGE_MIN_CONTANGO", os.environ.get("ARBITRAGE_ENTRY_THRESHOLD", "0"))
)
EXIT_THRESHOLD = Decimal(os.environ.get("ARBITRAGE_EXIT_THRESHOLD", "0.01"))
MAX_USD_PER_SYMBOL = Decimal(os.environ.get("ARBITRAGE_MAX_USD_PER_SYMBOL", "250"))
MAX_ACTIVE_SYMBOLS = int(os.environ.get("ARBITRAGE_MAX_SYMBOLS", "3"))
POLL_INTERVAL = float(os.environ.get("ARBITRAGE_POLL_INTERVAL", "10"))
LOG_LEVEL = os.environ.get("ARBITRAGE_LOG_LEVEL", "INFO").upper()
TOP_OPP_LOG_COUNT = int(os.environ.get("ARBITRAGE_TOP_OPP_LOG_COUNT", "5"))
FUNDING_REFRESH_INTERVAL = float(os.environ.get("ARBITRAGE_FUNDING_REFRESH_SECONDS", "3600"))
MAX_TOTAL_USD = Decimal(os.environ.get("ARBITRAGE_MAX_TOTAL_USD", "250"))
TAKE_PROFIT_THRESHOLD = Decimal(
    os.environ.get("ARBITRAGE_TAKE_PROFIT", os.environ.get("ARBITRAGE_ENTRY_THRESHOLD", "0.01"))
)
