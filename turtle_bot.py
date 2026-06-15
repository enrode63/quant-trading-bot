"""
터틀 전략 디스코드 알림 봇
===========================
- 매일 아침 9시 (한국시간) 자동 실행
- 50종목 스캔 → 롱 진입/손절/익절 신호 감지
- 하루 신호 3개 이상이면 ADX 높은 순 3개만 선택
- 디스코드 웹훅으로 알림 전송
- GitHub Actions로 실행 (컴퓨터 꺼도 됨)
"""

import os
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime

# ─────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────

# 디스코드 웹훅 URL (GitHub Secrets에서 자동으로 읽어옴)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# 터틀 파라미터
ENTRY_PERIOD = 20    # 진입: 20일 최고가 돌파
EXIT_PERIOD  = 20    # 익절: 20일 최저가 역돌파
ATR_PERIOD   = 14    # ATR 기간
ATR_MULT     = 2.0   # 손절: 진입가 - 2×ATR
MAX_SIGNALS  = 3     # 하루 최대 진입 신호 수 (초과 시 ADX 상위 3개만)

# 50종목 + 섹터 정보
SYMBOLS = {
    # 테크/AI (8)
    "NVDA":  "AI반도체",
    "MSFT":  "테크/AI",
    "AAPL":  "테크/AI",
    "GOOGL": "테크/AI",
    "META":  "테크/AI",
    "AMZN":  "테크/AI",
    "TSLA":  "전기차/AI",
    "PLTR":  "AI소프트웨어",
    # 반도체 (7)
    "AMD":   "반도체",
    "AVGO":  "반도체",
    "QCOM":  "반도체",
    "MU":    "반도체",
    "AMAT":  "반도체장비",
    "ASML":  "반도체장비",
    "ARM":   "반도체설계",
    # 소프트웨어/클라우드 (7)
    "CRWD":  "사이버보안",
    "NOW":   "클라우드",
    "DDOG":  "클라우드",
    "NET":   "클라우드",
    "PANW":  "사이버보안",
    "ORCL":  "클라우드",
    "INTU":  "소프트웨어",
    # 금융 (6)
    "JPM":   "금융",
    "GS":    "금융",
    "V":     "핀테크",
    "MA":    "핀테크",
    "BLK":   "자산운용",
    "HOOD":  "핀테크",
    # 방산/산업재 (6)
    "LMT":   "방산",
    "RTX":   "방산",
    "GE":    "산업재",
    "CAT":   "산업재",
    "DE":    "산업재",
    "NOC":   "방산",
    # 에너지 (5)
    "XOM":   "에너지",
    "CVX":   "에너지",
    "COP":   "에너지",
    "SLB":   "에너지서비스",
    "EOG":   "에너지",
    # 헬스케어 (5)
    "LLY":   "헬스케어",
    "UNH":   "헬스케어",
    "ABBV":  "헬스케어",
    "TMO":   "헬스케어",
    "MRK":   "헬스케어",
    # 소비재 (4)
    "COST":  "소비재",
    "WMT":   "소비재",
    "HD":    "소비재",
    "MCD":   "소비재",
    # 성장/혁신 (4)
    "UBER":  "모빌리티",
    "COIN":  "크립토",
    "IONQ":  "양자컴퓨팅",
    "MSTR":  "크립토",
    # 통신 (3)
    "TMUS":  "통신",
    "SPOT":  "미디어",
    "T":     "통신",
}


# ─────────────────────────────────────────────────────────────
# 1. 지표 계산
# ─────────────────────────────────────────────────────────────
def calc_atr(df, period):
    """ATR (평균 진폭) 계산"""
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift()).abs()
    tr3 = (df["low"] - df["close"].shift()).abs()
    tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calc_adx(df, period):
    """ADX (추세 강도) 계산 — 높을수록 추세 강함"""
    up   = df["high"].diff()
    down = -df["low"].diff()
    plus_dm  = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift()).abs()
    tr3 = (df["low"]  - df["close"].shift()).abs()
    tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr      = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di  = 100 * pd.Series(plus_dm,  index=df.index).ewm(alpha=1/period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1/period, adjust=False).mean()


# ─────────────────────────────────────────────────────────────
# 2. 데이터 로드
# ─────────────────────────────────────────────────────────────
def get_data(symbol):
    """야후파이낸스에서 60일 일봉 로드"""
    df = yf.download(symbol, period="60d", interval="1d",
                     progress=False, auto_adjust=True)
    if df.empty:
        return None
    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                  for c in df.columns]
    return df[["open", "high", "low", "close", "volume"]]


# ─────────────────────────────────────────────────────────────
# 3. 신호 판정
# ─────────────────────────────────────────────────────────────
def check_signal(df):
    """
    오늘 봉 기준 신호 판정
    반환: dict (entry/exit/stop 신호 + 가격 정보)
    """
    if len(df) < ENTRY_PERIOD + ATR_PERIOD + 2:
        return None

    # 어제까지로 기준선 계산 (미래참조 방지)
    prev  = df.iloc[:-1]
    today = df.iloc[-1]

    high_20  = prev["high"].rolling(ENTRY_PERIOD).max().iloc[-1]   # 20일 최고가
    low_20   = prev["low"].rolling(EXIT_PERIOD).min().iloc[-1]     # 20일 최저가
    atr      = calc_atr(prev, ATR_PERIOD).iloc[-1]                 # ATR
    adx_val  = calc_adx(prev, ATR_PERIOD).iloc[-1]                 # ADX

    price = float(today["close"])

    return {
        "price":    round(price, 2),
        "high_20":  round(float(high_20), 2),
        "low_20":   round(float(low_20), 2),
        "atr":      round(float(atr), 2),
        "adx":      round(float(adx_val), 2),
        "stop":     round(price - ATR_MULT * float(atr), 2),   # 손절가
        # 신호 판정
        "entry":    today["high"] > high_20,    # 20일 최고가 돌파 → 롱 진입
        "exit":     today["low"]  < low_20,     # 20일 최저가 역돌파 → 익절
    }


# ─────────────────────────────────────────────────────────────
# 4. 디스코드 메시지 포맷
# ─────────────────────────────────────────────────────────────
def format_entry(symbol, sector, sig):
    """롱 진입 알림 메시지"""
    return (
        f"🟢 **롱 진입 | {symbol} ({sector})**\n"
        f"현재가: `${sig['price']}`\n"
        f"20일 고점 돌파: `${sig['high_20']}`\n"
        f"손절가: `${sig['stop']}` (진입가 - 2×ATR)\n"
        f"추세강도 ADX: `{sig['adx']}`"
    )


def format_exit(symbol, sector, sig):
    """익절 알림 메시지"""
    return (
        f"⬜ **익절 | {symbol} ({sector})**\n"
        f"현재가: `${sig['price']}`\n"
        f"20일 저점 역돌파: `${sig['low_20']}` → 추세 꺾임"
    )


def format_stop(symbol, sector, sig):
    """손절 알림 메시지"""
    return (
        f"🔴 **손절 | {symbol} ({sector})**\n"
        f"현재가: `${sig['price']}`\n"
        f"손절가 `${sig['stop']}` 이탈"
    )


# ─────────────────────────────────────────────────────────────
# 5. 디스코드 전송
# ─────────────────────────────────────────────────────────────
def send_discord(message):
    """디스코드 웹훅으로 전송"""
    if not DISCORD_WEBHOOK_URL:
        print("[미리보기]\n" + message)
        return

    # 디스코드 메시지 2000자 제한 처리
    chunks = [message[i:i+1900] for i in range(0, len(message), 1900)]
    for chunk in chunks:
        res = requests.post(DISCORD_WEBHOOK_URL, json={"content": chunk})
        if res.status_code != 204:
            print(f"[전송 실패] {res.status_code}")


# ─────────────────────────────────────────────────────────────
# 6. 메인
# ─────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 스캔 시작 — {len(SYMBOLS)}종목")

    entry_signals = []   # 진입 신호 목록 (ADX 필터용)
    exit_msgs     = []   # 익절 메시지
    stop_msgs     = []   # 손절 메시지
    errors        = []   # 오류 종목

    for symbol, sector in SYMBOLS.items():
        try:
            df = get_data(symbol)
            if df is None:
                errors.append(symbol)
                continue

            sig = check_signal(df)
            if sig is None:
                continue

            # 익절 신호
            if sig["exit"]:
                exit_msgs.append(format_exit(symbol, sector, sig))

            # 손절 신호 (현재가가 손절가 아래)
            if sig["price"] <= sig["stop"]:
                stop_msgs.append(format_stop(symbol, sector, sig))

            # 진입 신호 (ADX 필터를 위해 모아둠)
            if sig["entry"]:
                entry_signals.append({
                    "symbol": symbol, "sector": sector,
                    "sig": sig, "adx": sig["adx"]
                })

            print(f"[완료] {symbol}: 진입={sig['entry']} 익절={sig['exit']} ADX={sig['adx']}")

        except Exception as e:
            errors.append(f"{symbol}({e})")

    # 진입 신호 ADX 필터 — 3개 초과 시 ADX 높은 순으로 자름
    if len(entry_signals) > MAX_SIGNALS:
        entry_signals = sorted(entry_signals, key=lambda x: x["adx"], reverse=True)
        entry_signals = entry_signals[:MAX_SIGNALS]
        print(f"[필터] 진입 신호 {len(entry_signals)}개 초과 → ADX 상위 {MAX_SIGNALS}개 선택")

    entry_msgs = [format_entry(e["symbol"], e["sector"], e["sig"])
                  for e in entry_signals]

    # ── 최종 메시지 조립 ──
    header = (
        f"**📊 터틀 신호 | {datetime.now().strftime('%Y-%m-%d')} 아침 9시**\n"
        f"스캔: {len(SYMBOLS)}종목 | "
        f"진입 {len(entry_msgs)}건 | 익절 {len(exit_msgs)}건 | 손절 {len(stop_msgs)}건\n"
        f"{'─'*40}"
    )

    sections = [header]

    if entry_msgs:
        sections.append("\n".join(entry_msgs))
    if exit_msgs:
        sections.append("\n".join(exit_msgs))
    if stop_msgs:
        sections.append("\n".join(stop_msgs))
    if not entry_msgs and not exit_msgs and not stop_msgs:
        sections.append("✅ 오늘 신호 없음")

    send_discord("\n\n".join(sections))
    print("완료")


if __name__ == "__main__":
    main()
