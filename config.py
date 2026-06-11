from __future__ import annotations


from decimal import Decimal

# ---- Trading configuration ---------------------------------------------------
# 이 파일에서 숫자들을 직접 수정하면 됩니다.
# 단위:
#   - 퍼센트 값들은 소수 (예: 0.01 = 1%)
#   - USD 관련 값들은 Decimal

# 한 번에 체결할 USD 기준 노출
TRADE_USD = Decimal("20")

# 진입/청산 임계값 (스프레드 비율)
ENTRY_THRESHOLD = Decimal("0.007")       # 0.7% 이상 컨탱고일 때 진입
EXIT_THRESHOLD = Decimal("0.001")        # 스프레드가 0.1% 미만으로 줄면 손절
TAKE_PROFIT_THRESHOLD = Decimal("0.01")  # 진입 대비 1% 이상 좁혀질 때 take-profit

# 노출 및 포지션 제약
MAX_USD_PER_SYMBOL = Decimal("250")
MAX_TOTAL_USD = Decimal("250")
MAX_ACTIVE_SYMBOLS = 3

# 비워두면 두 거래소에 공통으로 존재하는 모든 심볼을 스캔합니다.
# 예: ("BTC", "ETH") 로 제한하면 Pacifica orderbook 호출 수를 줄일 수 있습니다.
SYMBOL_ALLOWLIST: tuple[str, ...] = ()

# 시작 시 실제 거래소에 이미 열린 포지션이 있으면 빈 장부로 실행하지 않습니다.
REQUIRE_FLAT_START = True

# 루프 주기 및 로그 설정
POLL_INTERVAL = 10.0  # 초
LOG_LEVEL = "INFO"
TOP_OPP_LOG_COUNT = 5

# 펀딩 데이터 새로고침 (초)
FUNDING_REFRESH_INTERVAL = 3600.0
