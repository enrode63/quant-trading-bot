"""
터틀 전략 디스코드 알림 봇
===========================
- 매일 아침 9시 (한국시간) 자동 실행
- 50종목 스캔 → 롱 진입/손절/익절 신호 감지
- ADX 높은 순 정렬 (개수 제한 없음)
- 이미 진입한 종목은 익절/손절 전까지 재알림 안 함 (positions.json으로 상태 기억)
- 디스코드 웹훅으로 알림 전송
- GitHub Actions로 실행 (컴퓨터 꺼도 됨)
"""

import os
import json
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime

# ─────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
POSITIONS_FILE = "positions.json"   # 포지션 상태 저장 파일 (저장소에 커밋됨)

ENTRY_PERIOD = 20    # 진입: 20일 최고가 돌파
EXIT_PERIOD  = 20    # 익절: 20일 최저가 역돌파
ATR_PERIOD   = 14    # ATR 기간
ATR_MULT     = 2.0   # 손절: 진입가 - 2×ATR

# 50종목 + 섹터 정보
SYMBOLS = {
    "NVDA":  "AI반도체", "MSFT":  "테크/AI", "AAPL":  "테크/AI",
    "GOOGL": "테크/AI",  "META":  "테크/AI", "AMZN":  "테크/AI",
    "TSLA":  "전기차/AI", "PLTR":  "AI소프트웨어",
    "AMD":   "반도체", "AVGO":  "반도체", "QCOM":  "반도체",
    "MU":    "반도체", "AMAT":  "반도체장비", "ASML":  "반도체장비", "ARM": "반도체설계",
    "CRWD":  "사이버보안", "NOW":   "클라우드", "DDOG":  "클라우드",
    "NET":   "클라우드", "PANW":  "사이버보안", "ORCL":  "클라우드", "INTU": "소프트웨어",
    "JPM":   "금융", "GS":    "금융", "V":     "핀테크",
    "MA":    "핀테크", "BLK":   "자산운용", "HOOD":  "핀테크",
    "LMT":   "방산", "RTX":   "방산", "GE":    "산업재",
    "CAT":   "산업재", "DE":    "산업재", "NOC":   "방산",
    "XOM":   "에너지", "CVX":   "에너지", "COP":   "에너지",
    "SLB":   "에너지서비스", "EOG":   "에너지",
    "LLY":   "헬스케어", "UNH":   "헬스케어", "ABBV":  "헬스케어",
    "TMO":   "헬스케어", "MRK":   "헬스케어",
    "COST":  "소비재", "WMT":   "소비재", "HD":    "소비재", "MCD": "소비재",
    "UBER":  "모빌리티", "COIN":  "크립토", "IONQ":  "양자컴퓨팅", "MSTR": "크립토",
    "TMUS":  "통신", "SPOT":  "미디어", "T":     "통신",
}

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
# 0. 포지션 상태 관리 (이미 진입한 종목 기억)
# ─────────────────────────────────────────────────────────────
def load_positions():
    """positions.json 로드. 없으면 빈 딕셔너리."""
    if os.path.exists(POSITIONS_FILE):
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_positions(positions):
    """positions.json 저장"""
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(positions, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────────────────
# 1. 지표 계산
# ─────────────────────────────────────────────────────────────
def calc_atr(df, period):
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift()).abs()
    tr3 = (df["low"] - df["close"].shift()).abs()
    tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calc_adx(df, period):
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
    if len(df) < ENTRY_PERIOD + ATR_PERIOD + 2:
        return None

    prev  = df.iloc[:-1]
    today = df.iloc[-1]

    high_20  = prev["high"].rolling(ENTRY_PERIOD).max().iloc[-1]
    low_20   = prev["low"].rolling(EXIT_PERIOD).min().iloc[-1]
    atr      = calc_atr(prev, ATR_PERIOD).iloc[-1]
    adx_val  = calc_adx(prev, ATR_PERIOD).iloc[-1]

    price = float(today["close"])

    return {
        "price":    round(price, 2),
        "high_20":  round(float(high_20), 2),
        "low_20":   round(float(low_20), 2),
        "atr":      round(float(atr), 2),
        "adx":      round(float(adx_val), 2),
        "stop":     round(price - ATR_MULT * float(atr), 2),
        "entry":    bool(today["high"] > high_20),
        "exit":     bool(today["low"]  < low_20),
    }


# ─────────────────────────────────────────────────────────────
# 4. 디스코드 메시지 포맷
# ─────────────────────────────────────────────────────────────
def build_msg1(date_str, entry_signals):
    """메시지 1 — 롱 진입 (ADX 높은 순 정렬)"""
    lines = [f"📅 **{date_str}**\n", "🟢 **롱 진입**\n"]
    for e in entry_signals:
        s, sec, sig = e["symbol"], e["sector"], e["sig"]
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
            s, sec, sig, entry_price = e["symbol"], e["sector"], e["sig"], e["entry_price"]
            name = NAMES.get(s, s)
            pnl = round((sig['price'] - entry_price) / entry_price * 100, 2)
            sign = "+" if pnl >= 0 else ""
            lines.append(
                f"✅ {name} ({s} · {sec})\n"
                f"　- 진입가 : ${entry_price}\n"
                f"　- 익절가 : ${sig['price']}\n"
                f"　- 수익률 : {sign}{pnl}%\n"
                f"{'─'*24}"
            )

    if stop_list:
        lines.append("🛑 **손절**\n")
        for e in stop_list:
            s, sec, sig, entry_price = e["symbol"], e["sector"], e["sig"], e["entry_price"]
            name = NAMES.get(s, s)
            pnl = round((sig['price'] - entry_price) / entry_price * 100, 2)
            lines.append(
                f"❌ {name} ({s} · {sec})\n"
                f"　- 진입가 : ${entry_price}\n"
                f"　- 손절가 : ${sig['price']}\n"
                f"　- 수익률 : {pnl}%\n"
                f"{'─'*24}"
            )

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 5. 디스코드 전송
# ─────────────────────────────────────────────────────────────
def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        print("[미리보기]\n" + message)
        return
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

    date_str  = datetime.now().strftime('%Y-%m-%d')
    positions = load_positions()   # {"NVDA": {"entry_price": 245.3, "entry_date": "2026-06-15"}, ...}

    entry_signals = []
    exit_list     = []
    stop_list     = []

    for symbol, sector in SYMBOLS.items():
        try:
            df = get_data(symbol)
            if df is None:
                continue

            sig = check_signal(df)
            if sig is None:
                continue

            holding = symbol in positions   # 이미 진입 중인지

            if holding:
                entry_price = positions[symbol]["entry_price"]

                # 보유 중 → 익절/손절만 판정 (진입 알림 없음)
                if sig["price"] <= sig["stop"]:
                    stop_list.append({
                        "symbol": symbol, "sector": sector,
                        "sig": sig, "entry_price": entry_price,
                    })
                    del positions[symbol]   # 포지션 종료 → 기억 삭제

                elif sig["exit"]:
                    exit_list.append({
                        "symbol": symbol, "sector": sector,
                        "sig": sig, "entry_price": entry_price,
                    })
                    del positions[symbol]   # 포지션 종료 → 기억 삭제

            else:
                # 미보유 → 진입 신호만 판정
                if sig["entry"]:
                    entry_signals.append({
                        "symbol": symbol, "sector": sector,
                        "sig": sig, "adx": sig["adx"]
                    })
                    # 새 포지션 기억 (오늘 종가를 진입가로 기록)
                    positions[symbol] = {
                        "entry_price": sig["price"],
                        "entry_date": date_str,
                    }

            print(f"[완료] {symbol}: 보유중={holding} 진입={sig['entry']} 익절={sig['exit']} ADX={sig['adx']}")

        except Exception as e:
            print(f"[오류] {symbol}: {e}")

    # 진입 신호: ADX 높은 순 정렬
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

    # 포지션 상태 저장 (다음 실행에서 기억하도록)
    save_positions(positions)
    print(f"[포지션] 현재 보유: {list(positions.keys())}")
    print("완료")


if __name__ == "__main__":
    main()
