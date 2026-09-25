import os
import time
import requests
import json
import threading
from flask import Flask
import google.generativeai as genai

# ==========================================
# 1. MINI FLASK WEBSERVER (Voor Render Free Tier)
# ==========================================
app = Flask(__name__)

@app.route('/')
def health_check():
    return "MyCryptoAgent Co-Pilot is 24/7 Online & Active!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Start de webserver in een aparte achtergrond-thread
threading.Thread(target=run_flask, daemon=True).start()

# ==========================================
# 2. CONFIGURATIE & ENVIRONMENT VARIABLES
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash')
else:
    print("WARNING: GEMINI_API_KEY niet gevonden!")

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "ADAUSDT",
    "AAVEUSDT",
    "TAOUSDT",
    "UNIUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "SOLUSDT",
]

last_alerted_candles = {}

# ==========================================
# 3. HELPER FUNCTIES
# ==========================================
def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def fetch_binance_klines(symbol, interval, limit=50):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        return [{
            "timestamp": c[0],
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5])
        } for c in data]
    except Exception as e:
        print(f"Binance fetch error {symbol}: {e}")
        return []

# ==========================================
# 4. AI QUANT EVALUATIE ENGINE
# ==========================================
def evaluate_market_with_gemini(symbol, candles_15m, candles_5m, btc_context):
    prompt = f"""
    Je bent een kwantitatieve Trading Analyst Co-Pilot gespecialiseerd in Crypto.
    Analyseer de volgende live marktdata voor {symbol} en geef een strikt wiskundig oordeel.

    CONTEXT BTCUSDT:
    - BTC 15m Laatste Close: {btc_context['close']}
    - BTC 15m Trend: {btc_context['trend']}

    TARGET ASSET DATA ({symbol}):
    - Laatste 5x 15m Candles: {json.dumps(candles_15m[-5:])}
    - Laatste 5x 5m Candles: {json.dumps(candles_5m[-5:])}

    STRIKTE EVALUATIE REGELS:
    1. Trend Alignment (35% Gewicht): Als {symbol} != 'BTCUSDT' en {symbol} vertoont een bullish setup TERWIJL BTC bearish is, verlaag de Trend Alignment score direct naar max 33.3% (Counter-Trend Penalty).
    2. 15m Full Body Close Rule: Upper/lower wick mag niet groter zijn dan 30% van het totale bereik. Zo niet -> No-Go / Watchlist.
    3. Reversal Confirmation (M3/M5): Engulfing, Pinbar (wick >= 66%), of MSS op volume >= 1.5x gemiddelde.
    4. False Breakout Penalty (-30%): Bij snelle stijging/daling <48u na breakdown zonder accumulatie.
    5. Bereken Setup Score (%), Win Rate (P), Gewogen R:R, EV = (P * R) - ((1-P) * 1) en Adjusted EV (EV_adj = T * EV).
    6. Als Setup Score < 65% of EV < +0.30R -> VERDICT IS AUTOMATISCH 'NO-GO' (B-Setup = 0.0% Risico).

    FORMAT:
    🚨 **[GO | WATCHLIST | NO-GO]** - {symbol}
    **Rating:** [A+ | A | B] | **Score:** X% | **EV:** +X.XX R

    - **Playbook:** [Swing Breakout | Day Sweep | Scalp Reclaim]
    - **Richting:** [Long / Short]
    - **Entry:** $XX.XX
    - **Stop Loss:** $XX.XX
    - **TP1 (50%):** $XX.XX | **TP2 (30%):** $XX.XX | **Runner (20%):** $XX.XX
    - **Gewogen R:R:** X.XX R | **Fill Chance (T):** X%
    - **Adjusted EV (EV_adj):** +X.XX R

    **Korte Analyse:** (Max 2 zinnen over de score en BTC-correlatie).
    """
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API error {symbol}: {e}")
        return None

# ==========================================
# 5. MAIN TRADING SCANNER LOOP
# ==========================================
def run_scanner():
    print("🔍 Markt-scan gestart voor 9 symbolen...")
    btc_15m = fetch_binance_klines("BTCUSDT", "15m", limit=10)
    if not btc_15m:
        return

    btc_context = {
        "close": btc_15m[-1]["close"],
        "trend": "BULLISH" if btc_15m[-1]["close"] >= btc_15m[-2]["close"] else "BEARISH"
    }

    for symbol in SYMBOLS:
        try:
            candles_15m = fetch_binance_klines(symbol, "15m", limit=20)
            candles_5m = fetch_binance_klines(symbol, "5m", limit=20)
            if not candles_15m or not candles_5m:
                continue

            last_candle_time = candles_15m[-1]["timestamp"]
            if last_alerted_candles.get(symbol) == last_candle_time:
                continue

            analysis = evaluate_market_with_gemini(symbol, candles_15m, candles_5m, btc_context)
            if analysis and ("🚨 **GO**" in analysis or "🚨 **WATCHLIST**" in analysis):
                send_telegram_message(analysis)
                last_alerted_candles[symbol] = last_candle_time

            time.sleep(2)
        except Exception as e:
            print(f"Error bij {symbol}: {e}")

if __name__ == "__main__":
    startup_msg = "🤖 **MyCryptoAgent WebService IS LIVE!**\n9 Symbolen gemonitord op Free Tier."
    send_telegram_message(startup_msg)

    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f"Loop error: {e}")
        time.sleep(300)
