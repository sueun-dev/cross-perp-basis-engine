from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Dict

from models import Leg, SymbolExposure


def _leg_to_dict(leg: Leg) -> dict:
    return {
        "pacifica_side": leg.pacifica_side,
        "pacifica_amount": str(leg.pacifica_amount),
        "extended_side": leg.extended_side,
        "extended_amount": str(leg.extended_amount),
        "usd_size": str(leg.usd_size),
        "entry_ratio": str(leg.entry_ratio),
        "orphaned": leg.orphaned,
    }


def _leg_from_dict(payload: dict) -> Leg:
    return Leg(
        pacifica_side=str(payload["pacifica_side"]),
        pacifica_amount=Decimal(str(payload["pacifica_amount"])),
        extended_side=str(payload["extended_side"]),
        extended_amount=float(payload["extended_amount"]),
        usd_size=Decimal(str(payload["usd_size"])),
        entry_ratio=Decimal(str(payload["entry_ratio"])),
        orphaned=bool(payload.get("orphaned", False)),
    )


def save_exposures(
    exposures: Dict[str, SymbolExposure],
    path: str | Path,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        symbol: {
            "base_symbol": exposure.base_symbol,
            "extended_symbol": exposure.extended_symbol,
            "direction": list(exposure.direction),
            "legs": [_leg_to_dict(leg) for leg in exposure.legs],
        }
        for symbol, exposure in exposures.items()
        if exposure.legs
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(target)


def load_exposures(path: str | Path) -> Dict[str, SymbolExposure]:
    source = Path(path)
    if not source.is_file():
        return {}
    raw = json.loads(source.read_text(encoding="utf-8"))
    exposures: Dict[str, SymbolExposure] = {}
    for symbol, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        direction_raw = payload.get("direction", [])
        if not isinstance(direction_raw, list) or len(direction_raw) != 2:
            continue
        exposure = SymbolExposure(
            base_symbol=str(payload.get("base_symbol") or symbol),
            extended_symbol=str(payload["extended_symbol"]),
            direction=(str(direction_raw[0]), str(direction_raw[1])),
        )
        for leg_payload in payload.get("legs", []):
            if isinstance(leg_payload, dict):
                exposure.append_leg(_leg_from_dict(leg_payload))
        if exposure.legs:
            exposures[str(symbol)] = exposure
    return exposures
