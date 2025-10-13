# perp-dex-contango-bot

Contango-aware trading loop that watches Pacifica and Extended perpetual markets, ranks spreads, vets funding conditions, and opens delta-neutral hedges when parameters allow. This document focuses on how to run, tune, and extend the bot rather than internals.

## Quick Start
- Ensure `config.py` contains valid API credentials and risk limits for both venues.
- Activate any required environment variables (see `env_loader.py` if you keep secrets locally).
- Start the loop with:
  ```bash
  python3 main.py
  ```
- Stop with `Ctrl+C`. The loop unwinds any open legs before exiting.

## Runtime Flow
1. `main.py` orchestrates the loop: fetch market data, evaluate spreads, enforce risk controls, submit legs, then sleep for `POLL_INTERVAL`.
2. Market data collection lives in `market_data.py`; it refreshes funding caches and normalises symbol keys.
3. `opportunity_analysis.py` ranks spreads and filters them by funding (uses Pacifica vs Extended data).
4. `trade_operations.py` rounds base sizes, opens/closes legs on both venues, and tracks active exposure.
5. Shared dataclasses reside in `models.py`, while logging and funding cache helpers sit in `app_logging.py` and `funding_cache.py`.

## Tuning Behaviour
- Adjust spread thresholds, per-symbol caps, and take-profit logic in `config.py`.
- Modify rounding rules or execution sequencing inside `trade_operations.py` if either exchange changes contract requirements.
- Extend opportunity filters (e.g. volume checks) in `opportunity_analysis.py`.
- If you add new data feeds, wire them through `market_data.py` to keep the loop clean.

## Operational Notes
- The loop depends on both Pacifica and Extended SDK modules (`pacifica_pocket_bot.py`, `extended_pocket_bot.py`). Stub or replace them when back-testing.
- Logging is configured once in `app_logging.py`; adjust format/level there.
- Funding responses are cached according to `FUNDING_REFRESH_INTERVAL` to avoid excessive API calls.

## Adding New Features
1. Create a dedicated module for the feature (e.g., risk checks, persistence) to keep `main.py` minimal.
2. Expose clear helpers and import them in `main.py`.
3. Update this README whenever you add knobs users may need to adjust.

## Troubleshooting
- No trades opening: verify `MAX_TOTAL_USD`, per-symbol limits, and funding favourability; use INFO logs to see skipped reasons.
- Funding cache not refreshing: confirm `FUNDING_REFRESH_INTERVAL` and that both APIs return data; DEBUG logs offer more detail.
- Unexpected termination: run `python3 -m py_compile ...` on all modules to catch syntax issues after edits.
