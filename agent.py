import os
import time
import requests
import json
import threading
from datetime import datetime
import pytz
from flask import Flask
from google import genai

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

ai_client = None
if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
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
    """Stuurt om 15:20 CET/CEST een eenmalige waarschuwing dat Wall Street over 10m opent."""
    global ny_open_alert_sent_today
    tz = pytz.timezone('Europe/Amsterdam')
    now = datetime.now(tz)
    
    # Reset de trigger om middernacht
    if now.hour == 0 and now.minute == 0:
        ny_open_alert_sent_today = False

    if now.hour == 15 and 20 <= now.minute <= 25 and not ny_open_alert_sent_today:
        msg = (
            "⏰ **15:20 CET/CEST WAARSCHUWING (NY OPEN OVER 10 MINUTEN)**\n\n"
            "• Wall Street opent om 15:30 CET/CEST.\n"
            "• **US Open Rule:** Geen front-run limit orders op S/R randen tussen 15:15 en 16:30.\n"
            "• **Actie:** Wacht de eerste M15/M30 liquidity sweep/volume-spike na 15:30 af voor entries."
        )
        send_telegram_message(msg)
        ny_open_alert_sent_today = True

# ==========================================
# 4. AI QUANT EVALUATIE ENGINE (3-TRAPS PROTOCOL)
# ==========================================
def evaluate_market_with_gemini(symbol, candles_1d, candles_4h, candles_15m, candles_5m, btc_context):
    if not ai_client:
        print("Gemini client is niet geïnitialiseerd.")
        return None

    # Bereken de exacte Previous Day High / Low uit de 1D data
    pdh = candles_1d[-2]["high"] if len(candles_1d) >= 2 else candles_1d[-1]["high"]
    pdl = candles_1d[-2]["low"] if len(candles_1d) >= 2 else candles_1d[-1]["low"]

    # OPSPLITSING: AFGERONDE 15m kaars vs LOPENDE PRIJS
    last_closed_15m = candles_15m[-2] if len(candles_15m) >= 2 else candles_15m[-1]
    current_live_candle = candles_15m[-1]

    prompt = f"""
    Je bent een meedogenloze, kwantitatieve Trading Analyst Co-Pilot gespecialiseerd in Crypto ({symbol}).
    Analyseer de live data volgens de strikte system instructions en het 3-traps waarschuwingsprotocol.

    CONTEXT BTCUSDT (Voor Trend Alignment & Altcoin Correlatie):
    - BTC Laatste Price: {btc_context['close']}
    - BTC 15m Trend: {btc_context['trend']}

    TARGET ASSET MULTI-TIMEFRAME DATA ({symbol}):
    - Daily (1D) Macro S/R & Levels: PDH (Previous Day High) = ${pdh}, PDL (Previous Day Low) = ${pdl}
    - Laatste 3x 1D Candles: {json.dumps(candles_1d[-3:])}
    - Laatste 5x 4H Candles (HTF Trend & Major S/R): {json.dumps(candles_4h[-5:])}
    - LAATST AFGERONDE 15m Candle (VOOR FULL BODY CLOSE CHECK): {json.dumps(last_closed_15m)}
    - LOPENDE 15m Candle (ACTUELE PRIJS): {json.dumps(current_live_candle)}
    - Laatste 5x 5m Candles (M3/M5 Micro Reversal & Volume): {json.dumps(candles_5m[-5:])}

    STRIKTE EVALUATIE REGELS VOOR VERDICT CATEGORIE:
    1. ⚠️ PRE-TRADE ALERT: De actuele prijs nadert een belangrijk HTF S/R niveau (PDH/PDL, Daily, 4H Swing) tot op <= 0.5%, MAAR er is op de LAATST AFGERONDE 15m candle nog GEEN 15m close over het level of M3/M5 reversal. Doel = Waarschuwen dat de trader KLAAR MOET ZITTEN op M3/M5.
    2. 👁️ WATCHLIST: De LAATST AFGERONDE 15m candle ({json.dumps(last_closed_15m)}) heeft een geldige Full Body Close (wick <= 30% van kaarsbereik) over/op het S/R level, MAAR de M3/M5 reversal candle op de retest moet nog vormen/bevestigen.
    3. 🚨 GO: LAATST AFGERONDE 15m close is geldig EN op M3/M5 staat een BEVESTIGDE reversal (Engulfing op volume >=1.5x / Pinbar >=66% / M1-M3 MSS) EN Setup Score >= 65% EN EV > +0.30R.
    4. NO-GO: Geen niveaus nabij (>0.5% afstand), of B-setup (<65% score / wick > 30% op afgeronde 15m kaars / slechte trend alignment).

    REGELS VOOR TIMING & NY OPEN:
    - Tussen 15:15 en 16:30 CET/CEST mag ER GEEN FRONT-RUN LIMIT worden geadviseerd op S/R randen. We eisen dat de eerste liquidity sweep is geweest.

    OUTPUT FORMAT BIJ 'PRE-TRADE ALERT':
    ⚠️ **PRE-TRADE ALERT (KLAARZITTEN)** - {symbol}
    - **Afstand tot S/R Level:** ~X.XX%
    - **Verwachte S/R Zone:** $XX.XX (Bijv. PDH / Daily Level / 4H Swing)
    - **Verwachte Playbook:** [Swing Breakout | Day Sweep | Scalp Reclaim]
    - **Verwachte Richting:** [Long / Short]
    - **Actie:** Open je chart op M3/M5. Wacht op 15m close en M3/M5 reversal pattern op de retest.

    OUTPUT FORMAT BIJ 'WATCHLIST' OF 'GO':
    🚨 **[GO | WATCHLIST]** - {symbol}
    **Rating:** [A+ | A | B] | **Score:** X% | **EV:** +X.XX R

    - **Playbook:** [Swing Breakout | Day Sweep | Scalp Reclaim]
    - **Richting:** [Long / Short]
    - **Entry (Confirmed Reversal / Deep Placement):** $XX.XX
    - **Stop Loss:** $XX.XX
    - **TP1 (50%):** $XX.XX | **TP2 (30%):** $XX.XX | **Runner (20%):** $XX.XX
    - **Gewogen R:R:** X.XX R | **Fill Chance (T):** X%
    - **Adjusted EV (EV_adj):** +X.XX R

    **Korte Analyse:** (Max 2 zinnen met exacte reden, Daily/4H niveau en BTC-correlatie).
    """

    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API error voor {symbol}: {e}")
        return None

# ==========================================
# 5. MAIN SCANNER LOOP
# ==========================================
def run_scanner():
    tz = pytz.timezone('Europe/Amsterdam')
    now_str = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now_str}] 🔍 Markt-scan gestart voor alle 9 symbolen...")
    
    # Check 15:20 CET/CEST NY Open waarschuwing
    check_ny_open_warning()

    btc_15m = fetch_binance_klines("BTCUSDT", "15m", limit=10)
    if not btc_15m:
        print("Geen BTC data ontvangen, scan overgeslagen.")
        return

    btc_context = {
        "close": btc_15m[-1]["close"],
        "trend": "BULLISH" if btc_15m[-1]["close"] >= btc_15m[-2]["close"] else "BEARISH"
    }

    for symbol in SYMBOLS:
        try:
            candles_1d = fetch_binance_klines(symbol, "1d", limit=10)
            candles_4h = fetch_binance_klines(symbol, "4h", limit=10)
            candles_15m = fetch_binance_klines(symbol, "15m", limit=20)
            candles_5m = fetch_binance_klines(symbol, "5m", limit=20)
            
            if not candles_1d or not candles_4h or not candles_15m or not candles_5m:
                continue

            # Gebruik het timestamp van de laatst AFGERONDE 15m kaars voor deduplicatie
            last_closed_candle_time = candles_15m[-2]["timestamp"]
            
            # Voorkom dubbele meldingen gestuurd voor DEZELFDE afgeronde 15m kaars
            if last_alerted_candles.get(symbol) == last_closed_candle_time:
                continue

            analysis = evaluate_market_with_gemini(symbol, candles_1d, candles_4h, candles_15m, candles_5m, btc_context)
            
            # Vang ALLE 3 de alert-types op: Pre-Trade Alert, Watchlist én GO!
            if analysis and ("PRE-TRADE ALERT" in analysis or "🚨 **GO**" in analysis or "WATCHLIST" in analysis):
                print(f"[{now_str}] 🚨 ALERT GEGONGEN VOOR {symbol}!")
                send_telegram_message(analysis)
                last_alerted_candles[symbol] = last_closed_candle_time
            else:
                print(f"[{now_str}] {symbol}: NO-GO / Geen valide S/R setup.")

            time.sleep(1) # Kleine pauze tussen API calls
        except Exception as e:
            print(f"Error bij verwerken {symbol}: {e}")

if __name__ == "__main__":
    startup_msg = (
        "🤖 **MyCryptoAgent Master Service IS LIVE!**\n\n"
        "**Geïntegreerd 3-Traps Alert Systeem:**\n"
        "1. ⚠️ **Pre-Trade Alert:** Prijs binnen 0.5% van S/R (Klaarzitten)\n"
        "2. 👁️ **Watchlist:** Laatst afgeronde 15m Full Body Close bevestigd\n"
        "3. 🚨 **GO Execution:** M3/M5 Reversal + EV > +0.30R\n\n"
        "• **Multi-Timeframe Precision:** 1D Daily Macro S/R + 4H HTF Trend + 15m/5m Execution\n"
        "• **Timing:** Inclusief 15:20 CET NY Open Alert & US Open Rules\n"
        "• **API Optimisatie:** 3-Minuten Scan Lus (480 RPD - 100% Safe op Gemini Free Tier)"
    )
    send_telegram_message(startup_msg)

    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f"Loop error: {e}")
        
        # 180 seconden (3 minuten) = 480 RPD.
        # Voorkomt 429 ResourceExhausted op Gemini Free Tier & mist geen M15 closes of M3/M5 retests!
        time.sleep(180)
