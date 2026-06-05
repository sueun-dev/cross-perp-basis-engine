"""Test bootstrap.

`trade_operations` imports the two exchange clients at module load. Those clients
pull in heavy SDKs (solders, x10) and refuse to import without live credentials.
For unit-testing the pure logic we install lightweight stand-ins into
``sys.modules`` *before* anything imports them, so the trading maths can be
exercised in isolation with deterministic rounding.
"""

from __future__ import annotations

import sys
import types
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

import pytest

# Make the project root importable regardless of the working directory.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_exchange_stub(name: str, *, accepts_price: bool) -> types.ModuleType:
    module = types.ModuleType(name)
    module.LOTS: dict = {}            # symbol -> lot/step size (Decimal)
    module.DEFAULT_LOT = Decimal("0.001")
    module.CALLS: list = []           # recorded (action, symbol, side, amount)
    module.RAISE_ON: set = set()      # actions that should raise, e.g. {"open"}

    def round_base_amount(symbol, amount, price=None, rounding=ROUND_CEILING):
        lot = module.LOTS.get(symbol, module.DEFAULT_LOT)
        amt = amount if isinstance(amount, Decimal) else Decimal(str(amount))
        multiples = (amt / lot).to_integral_value(rounding=rounding)
        rounded = multiples * lot
        return rounded if rounded > 0 else lot

    # Extended's real signature has no price kwarg; keep the stubs faithful.
    if not accepts_price:
        def round_base_amount(symbol, amount, rounding=ROUND_CEILING):  # noqa: F811
            lot = module.LOTS.get(symbol, module.DEFAULT_LOT)
            amt = amount if isinstance(amount, Decimal) else Decimal(str(amount))
            multiples = (amt / lot).to_integral_value(rounding=rounding)
            rounded = multiples * lot
            return rounded if rounded > 0 else lot

    def open_position(symbol, side, amount, *args, **kwargs):
        if "open" in module.RAISE_ON:
            raise RuntimeError(f"{name} open failed (test)")
        module.CALLS.append(("open", symbol, side, amount))
        return {"ok": True}

    def close_position(symbol, side, amount, *args, **kwargs):
        if "close" in module.RAISE_ON:
            raise RuntimeError(f"{name} close failed (test)")
        module.CALLS.append(("close", symbol, side, amount))
        return {"ok": True}

    module.round_base_amount = round_base_amount
    module.open_position = open_position
    module.close_position = close_position
    return module


# Install the stubs once, before trade_operations / market_data import them.
if "pacifica_pocket_bot" not in sys.modules:
    sys.modules["pacifica_pocket_bot"] = _make_exchange_stub(
        "pacifica_pocket_bot", accepts_price=True
    )
if "extended_pocket_bot" not in sys.modules:
    sys.modules["extended_pocket_bot"] = _make_exchange_stub(
        "extended_pocket_bot", accepts_price=False
    )


@pytest.fixture
def pacifica_stub():
    mod = sys.modules["pacifica_pocket_bot"]
    mod.CALLS.clear()
    mod.RAISE_ON.clear()
    mod.LOTS.clear()
    yield mod
    mod.CALLS.clear()
    mod.RAISE_ON.clear()
    mod.LOTS.clear()


@pytest.fixture
def extended_stub():
    mod = sys.modules["extended_pocket_bot"]
    mod.CALLS.clear()
    mod.RAISE_ON.clear()
    mod.LOTS.clear()
    yield mod
    mod.CALLS.clear()
    mod.RAISE_ON.clear()
    mod.LOTS.clear()
