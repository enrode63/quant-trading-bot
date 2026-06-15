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
MAX_SIGNALS  = 999   # 제한 없음 (ADX 높은 순 정렬만)

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

# 종목 한국명
NAMES = {
    "NVDA": "엔비디아", "MSFT": "마이크로소프트", "AAPL": "애플",
    "GOOGL": "구글", "META": "메타", "AMZN": "아마존",
    "TSLA": "테슬라", "PLTR": "팔란티어", "AMD": "AMD",
    "AVGO": "브로드컴", "QCOM": "퀄컴", "MU": "마이크론",
    "AMAT": "어플라이드머티리얼즈", "ASML": "ASML", "ARM": "ARM",
    "CRWD": "크라우드스트라이크", "NOW": "서비스나우", "DDOG": "데이터독",
    "NET": "클라우드플레어", "PANW": "팔로알토", "ORCL": "오라클",
    "INTU": "인튜이트", "JPM": "JP모건", "GS": "골드만삭스",
    "V": "비자", "MA": "마스터카드", "BLK": "블랙록", "HOOD": "로빈후드",
    "LMT": "록히드마틴", "RTX": "RTX", "GE": "GE", "CAT": "캐터필러",
    "DE": "존디어", "NOC": "노스럽그러먼", "XOM": "엑슨모빌",
    "CVX": "쉐브론", "COP": "코노코필립스", "SLB": "슐럼버거",
    "EOG": "EOG리소시스", "LLY": "일라이릴리", "UNH": "유나이티드헬스",
    "ABBV": "애브비", "TMO": "써모피셔", "MRK": "머크",
    "COST": "코스트코", "WMT": "월마트", "HD": "홈디포", "MCD": "맥도날드",
    "UBER": "우버", "COIN": "코인베이스", "IONQ": "아이온큐",
    "MSTR": "마이크로스트래티지", "TMUS": "T모바일", "SPOT": "스포티파이",
    "T": "AT&T",
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
def build_msg1(date_str, entry_signals):
    """메시지 1 — 롱 진입 (ADX 높은 순 정렬)"""
    lines = [f"📅 **{date_str}**\n", "🟢 **롱 진입**\n"]
    for e in entry_signals:
        s, sec, sig = e["symbol"], e["sector"], e["sig"]
        # 종목 한국명 매핑 (없으면 티커 그대로)
        name = NAMES.get(s, s)
        lines.append(
            f"🔹 {name} ({s} · {sec})\n"
            f"　- 진입가 : ${sig['price']}\n"
            f"　- 손절가 : ${sig['stop']}\n"
            f"　- ADX　 : {sig['adx']}\n"
            f"{'─'*24}"
        )
    return "\n".join(lines)


def build_msg2(date_str, exit_list, stop_list):
    """메시지 2 — 익절 + 손절 합산"""
    lines = [f"📅 **{date_str}**\n"]

    if exit_list:
        lines.append("💰 **익절**\n")
        for e in exit_list:
            s, sec, sig = e["symbol"], e["sector"], e["sig"]
            name = NAMES.get(s, s)
            pnl = round((sig['price'] - sig['entry_price']) / sig['entry_price'] * 100, 2)
            lines.append(
                f"✅ {name} ({s} · {sec})\n"
                f"　- 진입가 : ${sig['entry_price']}\n"
                f"　- 익절가 : ${sig['price']}\n"
                f"　- 수익률 : +{pnl}%\n"
                f"{'─'*24}"
            )

    if stop_list:
        lines.append("🛑 **손절**\n")
        for e in stop_list:
            s, sec, sig = e["symbol"], e["sector"], e["sig"]
            name = NAMES.get(s, s)
            pnl = round((sig['price'] - sig['entry_price']) / sig['entry_price'] * 100, 2)
            lines.append(
                f"❌ {name} ({s} · {sec})\n"
                f"　- 진입가 : ${sig['entry_price']}\n"
                f"　- 손절가 : ${sig['price']}\n"
                f"　- 수익률 : {pnl}%\n"
                f"{'─'*24}"
            )

    return "\n".join(lines)


def format_exit(symbol, sector, sig):
    """익절 알림 메시지 (레거시 — 미사용)"""
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

    date_str      = datetime.now().strftime('%Y-%m-%d')
    entry_signals = []   # 진입 신호
    exit_list     = []   # 익절 신호
    stop_list     = []   # 손절 신호

    for symbol, sector in SYMBOLS.items():
        try:
            df = get_data(symbol)
            if df is None:
                continue

            sig = check_signal(df)
            if sig is None:
                continue

            # 익절: 20일 최저가 역돌파
            if sig["exit"]:
                sig["entry_price"] = sig["low_20"]  # 정확한 진입가는 별도 기록 필요
                exit_list.append({"symbol": symbol, "sector": sector, "sig": sig})

            # 손절: 현재가가 ATR 손절가 아래
            if sig["price"] <= sig["stop"]:
                sig["entry_price"] = sig["stop"] + (ATR_MULT * sig["atr"])
                stop_list.append({"symbol": symbol, "sector": sector, "sig": sig})

            # 진입: 20일 최고가 돌파
            if sig["entry"]:
                entry_signals.append({
                    "symbol": symbol, "sector": sector,
                    "sig": sig, "adx": sig["adx"]
                })

            print(f"[완료] {symbol}: 진입={sig['entry']} 익절={sig['exit']} ADX={sig['adx']}")

        except Exception as e:
            print(f"[오류] {symbol}: {e}")

    # 진입 신호: ADX 높은 순 정렬 (개수 제한 없음)
    entry_signals = sorted(entry_signals, key=lambda x: x["adx"], reverse=True)

    # ── 메시지 1: 롱 진입 ──
    if entry_signals:
        send_discord(build_msg1(date_str, entry_signals))

    # ── 메시지 2: 익절 + 손절 ──
    if exit_list or stop_list:
        send_discord(build_msg2(date_str, exit_list, stop_list))

    # ── 신호 없을 때 ──
    if not entry_signals and not exit_list and not stop_list:
        send_discord(f"📅 **{date_str}**\n✅ 오늘 신호 없음")

    print("완료")


if __name__ == "__main__":
    main()
    
