# Cross Perp Basis Engine

Delta-neutral perpetual basis engine for cross-venue contango capture and funding-aware hedge management.

## 한국어 안내

### 개요
Pacifica와 Extended 영구선물 시장을 동시에 모니터링하여 컨탱고 스프레드를 계산하고, 펀딩 조건을 검증한 뒤 허용 범위 안에서 델타-중립 헤지를 자동으로 진입/청산하는 봇입니다. 아래 내용은 실행 방법과 설정 위치, 확장 포인트에 집중합니다.

### 빠른 시작
1. `config.py`에 두 거래소의 API 키와 원하는 리스크 한도를 입력합니다.
2. 필요하다면 환경 변수로 민감한 값을 로드하세요(예: `env_loader.py` 사용).
3. 다음 명령으로 루프를 시작합니다.
   ```bash
   python3 main.py
   ```
4. 종료할 때는 `Ctrl+C`를 누르면 남아 있는 레그를 정리한 뒤 종료합니다.

### config.py 설정 예시
```python
# config.py
ENTRY_THRESHOLD = Decimal("0.015")   # 신규 진입 최소 스프레드
EXIT_THRESHOLD = Decimal("0.005")    # 청산 임계값
MAX_ACTIVE_SYMBOLS = 4
MAX_TOTAL_USD = Decimal("25000")
MAX_USD_PER_SYMBOL = Decimal("6000")
POLL_INTERVAL = 3
```
필요에 따라 `TAKE_PROFIT_THRESHOLD`, `TRADE_USD` 등 다른 값도 같이 조정하세요.

### API 인증정보 환경 변수 예시
```bash
export PACIFICA_API_KEY="your-pacifica-key"
export PACIFICA_API_SECRET="your-pacifica-secret"
export EXTENDED_API_KEY="your-extended-key"
export EXTENDED_API_SECRET="your-extended-secret"
python3 main.py
```
CI/CD나 서버에서 실행할 때는 위와 같이 `export`를 사용하거나 `.env` 파일을 `env_loader.py`로 불러올 수 있습니다.

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

### 문제 해결 가이드
- **포지션이 열리지 않을 때**: `MAX_TOTAL_USD`, `MAX_USD_PER_SYMBOL`, 펀딩 조건이 허용되는지 확인하고 INFO 로그의 건너뛴 사유를 참고하세요.
- **펀딩 캐시가 갱신되지 않을 때**: `FUNDING_REFRESH_INTERVAL` 값을 확인하고 두 API에서 데이터가 제대로 오는지 점검하세요.
- **예상치 못한 종료 발생 시**: 수정 후 `python3 -m py_compile *.py`로 구문 검사를 수행하면 빠르게 원인을 찾을 수 있습니다.

---

## English Guide

### Overview
This bot monitors Pacifica and Extended perpetual markets, measures contango spreads, checks funding, and opens delta-neutral hedges when your risk parameters allow. The guide below focuses on running, configuring, and extending the system.

### Quick Start
1. Populate `config.py` with both exchanges’ API credentials and your preferred risk limits.
2. Load sensitive values via environment variables if needed (see `env_loader.py` for local secrets).
3. Launch the loop:
   ```bash
   python3 main.py
   ```
4. Stop with `Ctrl+C`; the loop closes any remaining legs before exiting.

### config.py Sample Settings
```python
# config.py
ENTRY_THRESHOLD = Decimal("0.015")   # Minimum spread to enter new legs
EXIT_THRESHOLD = Decimal("0.005")    # Profit-take / unwind trigger
MAX_ACTIVE_SYMBOLS = 4
MAX_TOTAL_USD = Decimal("25000")
MAX_USD_PER_SYMBOL = Decimal("6000")
POLL_INTERVAL = 3
```
Adjust `TAKE_PROFIT_THRESHOLD`, `TRADE_USD`, and other knobs alongside these values as needed.

### API Credential Export Example
```bash
export PACIFICA_API_KEY="your-pacifica-key"
export PACIFICA_API_SECRET="your-pacifica-secret"
export EXTENDED_API_KEY="your-extended-key"
export EXTENDED_API_SECRET="your-extended-secret"
python3 main.py
```
For CI/CD or servers, export environment variables as shown or load a `.env` file through `env_loader.py`.

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

### Troubleshooting
- **No trades opening:** Confirm `MAX_TOTAL_USD`, per-symbol limits, and funding favourability; INFO logs explain skip reasons.
- **Funding cache stale:** Check `FUNDING_REFRESH_INTERVAL` and ensure both APIs return valid data.
- **Unexpected exit:** After edits, run `python3 -m py_compile *.py` to catch syntax issues quickly.
