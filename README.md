# Pacifica Extended Perp Basis Bot

Delta-neutral perpetual basis bot for Pacifica and Extended/X10 contango capture, funding-aware entries, and guarded hedge unwind.

## 한국어 안내

### 개요
Pacifica와 Extended/X10 영구선물 시장을 동시에 모니터링하여 컨탱고 스프레드를 계산하고, 펀딩 조건을 검증한 뒤 허용 범위 안에서 델타-중립 헤지를 자동으로 진입/청산하는 봇입니다. 현재 구현은 두 거래소 전용입니다. 더 많은 거래소를 붙이려면 `market_data.py`, `trade_operations.py`, `models.py`를 공통 exchange adapter 구조로 분리해야 합니다.

### 설치
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # 실행용 (개발/테스트는 requirements-dev.txt)
```
> Extended(X10) SDK는 PyPI에 없어 git에서 설치됩니다. 단순 import는 라이브 credential 없이도 가능하지만, 실제 API 호출과 주문 실행은 두 거래소의 API 키/계정 정보가 필요합니다.

### 빠른 시작
1. `cp .env.example .env` 후 두 거래소의 인증정보를 채웁니다(아래 변수 이름 그대로).
2. `config.py`에서 진입/청산 임계값과 리스크 한도를 조정합니다.
3. 기본값은 `DRY_RUN = True`입니다. 이 상태에서는 실제 주문을 내지 않고 진입 후보만 로그로 남깁니다.
4. 다음 명령으로 루프를 시작합니다.
   ```bash
   python3 main.py
   ```
5. 실제 주문 모드로 바꾸려면 `config.py`에서 `DRY_RUN = False`로 변경하고, 실행 환경에 `CROSS_PERP_LIVE_TRADING=I_UNDERSTAND_THIS_PLACES_REAL_ORDERS`를 설정해야 합니다.
6. 실제 거래소에 남아 있는 수동 포지션이 없는지 확인합니다. live 모드 기본값은 `REQUIRE_FLAT_START = True`라서 봇 시작 시 양쪽 포지션을 조회하고, 이미 열린 포지션이 있으면 빈 장부로 시작하지 않고 중단합니다.
7. 종료할 때는 `Ctrl+C`를 누르면 남아 있는 레그를 정리한 뒤 종료합니다. 루프 도중 한 사이클이 실패해도 봇은 죽지 않고 다음 주기에 재시도합니다(포지션 유지).

### config.py 설정 예시 (현재 기본값)
```python
# config.py
TRADE_USD = Decimal("20")               # 한 번에 체결할 USD 노출
ENTRY_THRESHOLD = Decimal("0.007")      # 0.7% 이상 컨탱고일 때 진입
EXIT_THRESHOLD = Decimal("0.001")       # 0.1% 미만으로 줄면 청산
TAKE_PROFIT_THRESHOLD = Decimal("0.01") # 진입 대비 1%p 좁혀지면 익절
MAX_USD_PER_SYMBOL = Decimal("250")
MAX_TOTAL_USD = Decimal("250")
MAX_ACTIVE_SYMBOLS = 3
DRY_RUN = True
LIVE_TRADING_CONFIRM_ENV = "CROSS_PERP_LIVE_TRADING"
LIVE_TRADING_CONFIRM_VALUE = "I_UNDERSTAND_THIS_PLACES_REAL_ORDERS"
PERSIST_STATE = True
STATE_FILE = "state/exposures.json"
SYMBOL_ALLOWLIST = ()                   # 예: ("BTC", "ETH")
REQUIRE_FLAT_START = True
POLL_INTERVAL = 10.0                     # 초
ESTIMATED_TAKER_FEE_RATE_PER_LEG = Decimal("0.0005")
ESTIMATED_SLIPPAGE_RATE_PER_LEG = Decimal("0.0005")
MIN_NET_ENTRY_EDGE = Decimal("0")
```
값은 위험 성향에 맞게 자유롭게 조정하세요.

### API 인증정보 환경 변수 예시
```bash
# Pacifica (Solana)
export PACIFICA_ACCOUNT="your-pacifica-public-key"
export PACIFICA_AGENT_PRIVATE_KEY="your-agent-wallet-base58-private-key"
export PACIFICA_API_KEY="your-pacifica-api-key"

# Extended / X10 (Starknet)
export EXTENDED_API_KEY="your-extended-api-key"
export EXTENDED_PRIVATE_KEY="0x...stark-private-key"
export EXTENDED_PUBLIC_KEY="0x...stark-public-key"
export EXTENDED_VAULT_ID="your-extended-vault-id"

python3 main.py
```
변수 이름은 `pacifica_pocket_bot.py` / `extended_pocket_bot.py`가 요구하는 값과 정확히 일치해야 합니다. 로컬에서는 `cp .env.example .env` 후 값을 채우면 `env_loader.py`가 자동으로 읽어옵니다. CI/CD나 서버에서는 위와 같이 `export`를 사용하세요.

### 모듈 구성
1. `main.py` – dry-run/live guard, state load/save, 시작 전 flat-start 검증 후 시장 데이터 요청 → 기회 평가 → 리스크 검사 → 주문 실행 → 슬립 순으로 수행합니다.
2. `market_data.py` – Extended 심볼 집합을 기준으로 Pacifica orderbook 호출 범위를 줄이고, 시세/펀딩 캐시(`FundingCache`)를 관리합니다.
3. `opportunity_analysis.py` – 스프레드 계산, 펀딩 검증, 후보 순위를 담당합니다.
4. `trade_operations.py` – 주문 수량 반올림, 포지션 오픈/클로즈, orphaned leg 복구, 시작 시 기존 포지션 검증을 맡습니다.
5. `models.py` – `Leg`, `SymbolExposure`, `Opportunity` 등의 데이터 클래스를 정의합니다.
6. `app_logging.py`, `funding_cache.py` – 로깅 초기화와 펀딩 캐시 구조체를 제공합니다.
7. `state_store.py` – live 모드 exposure book을 JSON으로 저장/복원합니다.

### 실행 중 변경 포인트
- 스프레드 임계값이나 최대 익스포저 한도는 `config.py`에서 조정합니다.
- 실제 주문을 켜려면 `DRY_RUN = False`와 confirmation 환경 변수가 둘 다 필요합니다.
- live 모드에서는 `PERSIST_STATE = True`로 exposure book을 `STATE_FILE`에 저장합니다. 이 파일은 `.gitignore` 처리되어 GitHub에 올라가지 않습니다.
- 거래소별 수량 반올림 규칙이나 롤백 로직을 바꾸려면 `trade_operations.py`를 수정합니다.
- 펀딩 검증에 추가 조건을 붙이고 싶다면 `opportunity_analysis.py`를 확장하세요.
- 심볼을 제한해 API 호출 수를 줄이고 싶다면 `config.py`의 `SYMBOL_ALLOWLIST`를 설정합니다.
- 기존 포지션이 있어도 강제로 시작해야 하는 특수 상황에서는 `REQUIRE_FLAT_START = False`로 바꿀 수 있지만, 빈 메모리 장부와 실제 포지션이 어긋날 수 있으므로 권장하지 않습니다.
- 로그 포맷이나 레벨은 `app_logging.py`에서 손쉽게 변경할 수 있습니다.
- 기본값 기준으로 컨탱고가 약 1%p 좁혀지면(현재 `TAKE_PROFIT_THRESHOLD = Decimal("0.01")`) 자동으로 포지션을 청산합니다. 실제 수익은 양쪽 taker fee, 슬리피지, 체결 수량, 펀딩 타이밍에 따라 달라집니다.

### 검증 상태
현재 저장소에서 검증된 범위:

- 단위 테스트: `pytest -q`
- 구문 검사: `python3 -m py_compile *.py`
- credential 없이 `import main` 가능
- 시작 시 기존 Pacifica/Extended 포지션이 감지되면 중단하는 테스트
- 기본 dry-run에서 주문 호출/장부 기록을 하지 않는 테스트
- live confirmation 환경 변수가 없으면 실주문 모드를 막는 테스트
- exposure state 저장/복원 테스트
- fee/slippage 추정치를 차감한 net edge 계산 테스트
- orphaned leg 추적/강제 reconcile 로직의 스텁 기반 테스트

아직 검증되지 않은 범위:

- 실제 Pacifica/Extended 계정으로 양쪽 포지션을 열고 닫는 라이브 주문 테스트
- 부분체결, API 지연, 주문 취소, 실계정 fee/slippage를 포함한 end-to-end 수익성 검증
- 프로세스 재시작 후 기존 포지션을 자동으로 가져와 장부를 복원하는 persistence/import flow

### 테스트
순수 로직(스프레드 계산, 펀딩 부호, 수량 반올림/수렴, 노출 집계)은 거래소 SDK 없이 단위 테스트로 검증합니다.
```bash
pip install -r requirements-dev.txt
pytest -q
```

### 문제 해결 가이드
- **포지션이 열리지 않을 때**: `MAX_TOTAL_USD`, `MAX_USD_PER_SYMBOL`, 펀딩 조건이 허용되는지 확인하고 INFO 로그의 건너뛴 사유를 참고하세요.
- **펀딩 캐시가 갱신되지 않을 때**: `FUNDING_REFRESH_INTERVAL` 값을 확인하고 두 API에서 데이터가 제대로 오는지 점검하세요.
- **예상치 못한 종료 발생 시**: 수정 후 `python3 -m py_compile *.py`로 구문 검사를 수행하고 `pytest -q`로 회귀를 확인하세요.

---

## English Guide

### Overview
This bot monitors Pacifica and Extended/X10 perpetual markets, measures contango spreads, checks funding, and opens delta-neutral hedges when your risk parameters allow. The current implementation is specific to those two venues; adding more venues requires extracting common exchange adapters from `market_data.py`, `trade_operations.py`, and `models.py`.

### Install
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # runtime (use requirements-dev.txt for tests)
```
> The Extended (X10) SDK has no PyPI release and is installed from git. Plain imports no longer require live credentials, but real API calls and live order placement require both venues' configured credentials.

### Quick Start
1. `cp .env.example .env` and fill in both exchanges’ credentials (exact variable names below).
2. Tune entry/exit thresholds and risk limits in `config.py`.
3. The default is `DRY_RUN = True`. In this mode the bot logs qualifying entries but does not submit live orders.
4. Launch the loop:
   ```bash
   python3 main.py
   ```
5. To enable live orders, set `DRY_RUN = False` in `config.py` and export `CROSS_PERP_LIVE_TRADING=I_UNDERSTAND_THIS_PLACES_REAL_ORDERS`.
6. Confirm there are no manual/live positions left on either venue. In live mode the default `REQUIRE_FLAT_START = True` fetches both venues' positions and aborts rather than starting with an empty local book while live exposure already exists.
7. Stop with `Ctrl+C`; the loop closes any remaining legs before exiting. A single failed cycle is logged and retried on the next poll — it no longer tears the bot down or force-closes open hedges.

### config.py Settings (current defaults)
```python
# config.py
TRADE_USD = Decimal("20")               # USD notional per fill
ENTRY_THRESHOLD = Decimal("0.007")      # Enter when contango >= 0.7%
EXIT_THRESHOLD = Decimal("0.001")       # Unwind when spread drops below 0.1%
TAKE_PROFIT_THRESHOLD = Decimal("0.01") # Take profit after 1pp compression
MAX_USD_PER_SYMBOL = Decimal("250")
MAX_TOTAL_USD = Decimal("250")
MAX_ACTIVE_SYMBOLS = 3
DRY_RUN = True
LIVE_TRADING_CONFIRM_ENV = "CROSS_PERP_LIVE_TRADING"
LIVE_TRADING_CONFIRM_VALUE = "I_UNDERSTAND_THIS_PLACES_REAL_ORDERS"
PERSIST_STATE = True
STATE_FILE = "state/exposures.json"
SYMBOL_ALLOWLIST = ()                   # e.g. ("BTC", "ETH")
REQUIRE_FLAT_START = True
POLL_INTERVAL = 10.0                     # seconds
ESTIMATED_TAKER_FEE_RATE_PER_LEG = Decimal("0.0005")
ESTIMATED_SLIPPAGE_RATE_PER_LEG = Decimal("0.0005")
MIN_NET_ENTRY_EDGE = Decimal("0")
```
Adjust these knobs to match your risk tolerance.

### API Credential Export Example
```bash
# Pacifica (Solana)
export PACIFICA_ACCOUNT="your-pacifica-public-key"
export PACIFICA_AGENT_PRIVATE_KEY="your-agent-wallet-base58-private-key"
export PACIFICA_API_KEY="your-pacifica-api-key"

# Extended / X10 (Starknet)
export EXTENDED_API_KEY="your-extended-api-key"
export EXTENDED_PRIVATE_KEY="0x...stark-private-key"
export EXTENDED_PUBLIC_KEY="0x...stark-public-key"
export EXTENDED_VAULT_ID="your-extended-vault-id"

python3 main.py
```
The variable names must match exactly what `pacifica_pocket_bot.py` / `extended_pocket_bot.py` expect. Locally, `cp .env.example .env` and fill in the values — `env_loader.py` loads them automatically. For CI/CD or servers, export the variables as shown.

### Module Layout
1. `main.py` – enforces dry-run/live guards, loads/saves state, checks for a flat startup book, then fetches data → evaluates spreads → enforces risk → submits orders → sleeps.
2. `market_data.py` – uses Extended symbols to limit Pacifica orderbook fanout and manages the `FundingCache`.
3. `opportunity_analysis.py` – ranks spreads and validates funding differentials.
4. `trade_operations.py` – rounds trade sizes, opens/closes hedges, tracks exposure, reconciles orphaned legs, and checks startup positions.
5. `models.py` – defines `Leg`, `SymbolExposure`, and `Opportunity` dataclasses.
6. `app_logging.py`, `funding_cache.py` – provide logging configuration and the funding cache struct.
7. `state_store.py` – persists and reloads the live exposure book as JSON.

### Where to Tweak Behaviour
- Edit spread thresholds and exposure caps inside `config.py`.
- Live order placement requires both `DRY_RUN = False` and the exact confirmation environment variable.
- In live mode, `PERSIST_STATE = True` writes the exposure book to `STATE_FILE`; the runtime state directory is git-ignored.
- Update rounding rules or rollback handling in `trade_operations.py` if exchange specs change.
- Extend funding or spread filters in `opportunity_analysis.py`.
- Set `SYMBOL_ALLOWLIST` in `config.py` to reduce quote fanout when you only want selected markets.
- Keep `REQUIRE_FLAT_START = True` unless you have an explicit state-import or manual reconciliation flow.
- Change logging format/level directly in `app_logging.py`.
- With the defaults, once contango compresses by roughly 1 percentage point (`TAKE_PROFIT_THRESHOLD = Decimal("0.01")`), the bot auto-closes the hedge. Actual PnL still depends on taker fees, slippage, fill size, and funding timing.

### Verification Status
Currently verified:

- Unit tests: `pytest -q`
- Syntax compilation: `python3 -m py_compile *.py`
- `import main` works without live credentials
- Startup aborts when existing Pacifica/Extended positions are reported
- Dry-run mode does not place orders or record live exposure
- Live mode requires the explicit confirmation environment variable
- Exposure state persistence round-trips Decimal fields
- Estimated fee/slippage buffers are subtracted from gross entry edge
- Orphaned-leg tracking and forced reconciliation are covered with stubs

Not yet verified:

- Live open/close tests against real Pacifica and Extended accounts
- Partial fills, API latency, cancellations, account-specific fees/slippage, and end-to-end profitability
- Persistence or automatic book import after process restart

### Testing
The pure logic (spread math, funding sign, size rounding/convergence, exposure accounting) is covered by unit tests that need no exchange SDK or credentials:
```bash
pip install -r requirements-dev.txt
pytest -q
```

### Troubleshooting
- **No trades opening:** Confirm `MAX_TOTAL_USD`, per-symbol limits, and funding favourability; INFO logs explain skip reasons.
- **Funding cache stale:** Check `FUNDING_REFRESH_INTERVAL` and ensure both APIs return valid data.
- **Unexpected exit:** After edits, run `python3 -m py_compile *.py` for syntax and `pytest -q` for regressions.
