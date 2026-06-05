# Cross Perp Basis Engine

Delta-neutral perpetual basis engine for cross-venue contango capture and funding-aware hedge management.

## 한국어 안내

### 개요
Pacifica와 Extended 영구선물 시장을 동시에 모니터링하여 컨탱고 스프레드를 계산하고, 펀딩 조건을 검증한 뒤 허용 범위 안에서 델타-중립 헤지를 자동으로 진입/청산하는 봇입니다. 아래 내용은 실행 방법과 설정 위치, 확장 포인트에 집중합니다.

### 설치
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # 실행용 (개발/테스트는 requirements-dev.txt)
```
> Extended(X10) SDK는 PyPI에 없어 git에서 설치됩니다. 주문 기능 없이 시세/분석만 쓸 때는 SDK가 없어도 import 시점에 친절한 오류로 처리됩니다.

### 빠른 시작
1. `cp .env.example .env` 후 두 거래소의 인증정보를 채웁니다(아래 변수 이름 그대로).
2. `config.py`에서 진입/청산 임계값과 리스크 한도를 조정합니다.
3. 다음 명령으로 루프를 시작합니다.
   ```bash
   python3 main.py
   ```
4. 종료할 때는 `Ctrl+C`를 누르면 남아 있는 레그를 정리한 뒤 종료합니다. 루프 도중 한 사이클이 실패해도 봇은 죽지 않고 다음 주기에 재시도합니다(포지션 유지).

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
POLL_INTERVAL = 10.0                     # 초
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
1. `main.py` – 이벤트 루프를 orchestration, 시장 데이터 요청 → 기회 평가 → 리스크 검사 → 주문 실행 → 슬립 순으로 수행합니다.
2. `market_data.py` – 시세 및 펀딩 정보를 가져오고 캐시(`FundingCache`)를 관리합니다.
3. `opportunity_analysis.py` – 스프레드 계산, 펀딩 검증, 후보 순위를 담당합니다.
4. `trade_operations.py` – 주문 수량 반올림, 포지션 오픈/클로즈, 보유 익스포저 계산을 맡습니다.
5. `models.py` – `Leg`, `SymbolExposure`, `Opportunity` 등의 데이터 클래스를 정의합니다.
6. `app_logging.py`, `funding_cache.py` – 로깅 초기화와 펀딩 캐시 구조체를 제공합니다.

### 실행 중 변경 포인트
- 스프레드 임계값이나 최대 익스포저 한도는 `config.py`에서 조정합니다.
- 거래소별 수량 반올림 규칙이나 롤백 로직을 바꾸려면 `trade_operations.py`를 수정합니다.
- 펀딩 검증에 추가 조건을 붙이고 싶다면 `opportunity_analysis.py`를 확장하세요.
- 로그 포맷이나 레벨은 `app_logging.py`에서 손쉽게 변경할 수 있습니다.
- 기본값 기준으로 컨탱고가 약 1%p 좁혀지면(현재 `TAKE_PROFIT_THRESHOLD = Decimal("0.01")`) 자동으로 포지션을 청산해 대략 1% 수익률을 실현합니다.

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
This bot monitors Pacifica and Extended perpetual markets, measures contango spreads, checks funding, and opens delta-neutral hedges when your risk parameters allow. The guide below focuses on running, configuring, and extending the system.

### Install
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # runtime (use requirements-dev.txt for tests)
```
> The Extended (X10) SDK has no PyPI release and is installed from git. Market-data and analysis still work without it; only live order placement requires it, and the import fails with a clear message if it is missing.

### Quick Start
1. `cp .env.example .env` and fill in both exchanges’ credentials (exact variable names below).
2. Tune entry/exit thresholds and risk limits in `config.py`.
3. Launch the loop:
   ```bash
   python3 main.py
   ```
4. Stop with `Ctrl+C`; the loop closes any remaining legs before exiting. A single failed cycle is logged and retried on the next poll — it no longer tears the bot down or force-closes open hedges.

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
POLL_INTERVAL = 10.0                     # seconds
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
1. `main.py` – orchestrates the loop: fetch data → evaluate spreads → enforce risk → submit orders → sleep.
2. `market_data.py` – pulls quotes/funding and manages the `FundingCache`.
3. `opportunity_analysis.py` – ranks spreads and validates funding differentials.
4. `trade_operations.py` – rounds trade sizes, opens/closes hedges, and tracks exposure.
5. `models.py` – defines `Leg`, `SymbolExposure`, and `Opportunity` dataclasses.
6. `app_logging.py`, `funding_cache.py` – provide logging configuration and the funding cache struct.

### Where to Tweak Behaviour
- Edit spread thresholds and exposure caps inside `config.py`.
- Update rounding rules or rollback handling in `trade_operations.py` if exchange specs change.
- Extend funding or spread filters in `opportunity_analysis.py`.
- Change logging format/level directly in `app_logging.py`.
- With the defaults, once contango compresses by roughly 1 percentage point (`TAKE_PROFIT_THRESHOLD = Decimal("0.01")`), the bot auto-closes the hedge to lock in about a 1% gain.

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
