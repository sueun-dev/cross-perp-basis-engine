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

# 루프 주기 및 로그 설정
POLL_INTERVAL = 10.0  # 초
LOG_LEVEL = "INFO"
TOP_OPP_LOG_COUNT = 5

# 펀딩 데이터 새로고침 (초)
FUNDING_REFRESH_INTERVAL = 3600.0
