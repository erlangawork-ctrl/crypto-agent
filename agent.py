import os
import time
import requests
import json
import threading
from datetime import datetime
import pytz
from flask import Flask
import google.generativeai as genai

# ==========================================
# 1. MINI FLASK WEBSERVER (Render 24/7 Keep-Alive)
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

# Alle 9 gemonitorde assets
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
ny_open_alert_sent_today = False

# ==========================================
# 3. HELPER FUNCTIES & TIME CHECKS
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

def check_ny_open_warning():
    """Stuurt om 15:20 CET een eenmalige waarschuwing dat Wall Street over 10m opent."""
    global ny_open_alert_sent_today
    tz = pytz.timezone('Europe/Amsterdam')
    now = datetime.now(tz)
    
    # Reset de trigger om middernacht
    if now.hour == 0 and now.minute == 0:
        ny_open_alert_sent_today = False

    if now.hour == 15 and 20 <= now.minute <= 25 and not ny_open_alert_sent_today:
        msg = (
            "⏰ **15:20 CET WAARSCHUWING (NY OPEN OVER 10 MINUTEN)**\n\n"
            "• Wall Street opent om 15:30 CET.\n"
            "• **Regel:** Geen front-run limit orders op S/R randen plaatsen.\n"
            "• **Actie:** Wacht de eerste M15 liquidity sweep/spike na 15:30 af voor entries."
        )
        send_telegram_message(msg)
        ny_open_alert_sent_today = True

# ==========================================
# 4. AI QUANT EVALUATIE ENGINE (3-TRAPS PROTOCOL)
# ==========================================
def evaluate_market_with_gemini(symbol, candles_15m, candles_5m, btc_context):
    prompt = f"""
    Je bent een kwantitatieve Trading Analyst Co-Pilot gespecialiseerd in Crypto.
    Analyseer de live data voor {symbol} volgens het strikte 3-traps waarschuwings- en executionprotocol.

    CONTEXT BTCUSDT (Voor Trend Alignment & Altcoin Correlatie):
    - BTC 15m Laatste Close: {btc_context['close']}
    - BTC 15m Trend: {btc_context['trend']}

    TARGET ASSET DATA ({symbol}):
    - Laatste 5x 15m Candles: {json.dumps(candles_15m[-5:])}
    - Laatste 5x 5m Candles: {json.dumps(candles_5m[-5:])}

    STRIKTE EVALUATIE REGELS VOOR VERDICT CATEGORIE:
    1. ⚠️ PRE-TRADE ALERT: Prijs nadert een belangrijk HTF/Swing S/R niveau tot op <= 0.5%, MAAR er is nog GEEN 15m close over het level of M3/M5 reversal. Doel = Waarschuwen dat de trader KLAAR MOET ZITTEN.
    2. 👁️ WATCHLIST: Er is sprake van een geldige 15m Full Body Close (wick <= 30%) op/over het S/R level, maar de M3/M5 reversal candle op de retest moet nog vormen.
    3. 🚨 GO: 15m close is geldig EN op M3/M5 timeframe staat een bevestigde reversal (Engulfing / Pinbar >=66% / MSS op volume >=1.5x) EN Setup Score >= 65% EN EV > +0.30R.
    4. NO-GO: Geen niveaus nabij, of B-setup (<65% score / slechte BTC correlatie / wick > 30%).

    FORMAT BIJ 'PRE-TRADE ALERT':
    ⚠️ **PRE-TRADE ALERT (KLAARZITTEN)** - {symbol}
    - **Afstand tot S/R Level:** ~X.XX%
    - **Verwachte S/R Zone:** $XX.XX
    - **Verwachte Playbook:** [Swing Breakout | Day Sweep | Scalp Reclaim]
    - **Verwachte Richting:** [Long / Short]
    - **Actie:** Open je chart op M3/M5. Wacht op 15m close en M3/M5 reversal pattern.

    FORMAT BIJ 'WATCHLIST' OF 'GO':
    🚨 **[GO | WATCHLIST]** - {symbol}
    **Rating:** [A+ | A | B] | **Score:** X% | **EV:** +X.XX R

    - **Playbook:** [Swing Breakout | Day Sweep | Scalp Reclaim]
    - **Richting:** [Long / Short]
    - **Entry:** $XX.XX
    - **Stop Loss:** $XX.XX
    - **TP1 (50%):** $XX.XX | **TP2 (30%):** $XX.XX | **Runner (20%):** $XX.XX
    - **Gewogen R:R:** X.XX R | **Fill Chance (T):** X%
    - **Adjusted EV (EV_adj):** +X.XX R

    **Korte Analyse:** (Max 2 zinnen met exacte reden en BTC-correlatie).
    """

    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API error voor {symbol}: {e}")
        return None

# ==========================================
# 5. MAIN SCANNER LOOP
# ==========================================
def run_scanner():
    print("🔍 Markt-scan gestart voor alle 9 symbolen...")
    
    # Check 15:20 CET NY Open waarschuwing
    check_ny_open_warning()

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
            
            # Vang nu ALLE 3 de alert-types op: Pre-Trade, Watchlist én GO!
            if analysis and ("⚠️ **PRE-TRADE ALERT**" in analysis or "🚨 **GO**" in analysis or "🚨 **WATCHLIST**" in analysis):
                send_telegram_message(analysis)
                last_alerted_candles[symbol] = last_candle_time

            time.sleep(2)
        except Exception as e:
            print(f"Error bij verwerken {symbol}: {e}")

if __name__ == "__main__":
    startup_msg = (
        "🤖 **MyCryptoAgent Master Service IS LIVE!**\n\n"
        "**Geïntegreerd 3-Traps Alert Systeem:**\n"
        "1. ⚠️ **Pre-Trade Alert:** Prijs binnen 0.5% van S/R (Klaarzitten)\n"
        "2. 👁️ **Watchlist:** 15m Full Body Close bevestigd\n"
        "3. 🚨 **GO Execution:** M3/M5 Reversal + EV > +0.30R\n\n"
        "• **Timing:** Inclusief 15:20 CET NY Open Alert\n"
        "• **Assets:** 9 Symbolen gemonitord op Render Free Tier (Poort 10000)"
    )
    send_telegram_message(startup_msg)

    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f"Loop error: {e}")
        
        time.sleep(300)
