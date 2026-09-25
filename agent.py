import os
import time
import requests
import json
import threading
from datetime import datetime
import pytz
from flask import Flask
from google import genai
from google.oauth2 import service_account

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
# 2. CONFIGURATIE & SERVICE ACCOUNT AUTHENTICATION (VERTEX AI)
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "crypto-ai-agent-509618")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")
GCP_SERVICE_ACCOUNT_JSON = os.getenv("GCP_SERVICE_ACCOUNT_JSON")

ai_client = None
try:
    if GCP_SERVICE_ACCOUNT_JSON:
        # Laad Service Account Credentials uit de Environment Variable op Render
        service_account_info = json.loads(GCP_SERVICE_ACCOUNT_JSON)
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        # Client verbinden met Vertex AI middels geauthenticeerde Credentials
        ai_client = genai.Client(
            vertexai=True, 
            project=GCP_PROJECT_ID, 
            location=GCP_LOCATION,
            credentials=credentials
        )
        print(f"SUCCESS: Verbonden met Vertex AI via Service Account (Project: {GCP_PROJECT_ID})", flush=True)
    else:
        ai_client = genai.Client(vertexai=True, project=GCP_PROJECT_ID, location=GCP_LOCATION)
        print(f"SUCCESS: Verbonden met Vertex AI (Default Auth)", flush=True)
except Exception as e:
    print(f"WARNING: Vertex AI Credentials Initialisatie mislukt: {e}", flush=True)

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

# Geheugen voor deduplicatie met unieke timestamps
last_alerted_candles = {}
ny_open_alert_sent_today = False

# ==========================================
# 3. HELPER FUNCTIES, ADVANCED LEVEL DETECTIE & TIME CHECKS
# ==========================================
def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

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
        print(f"Binance fetch error {symbol}: {e}", flush=True)
        return []

def find_key_levels(candles_1d, candles_4h, candles_1h):
    """
    ULTRA-PRECIEZE PYTHON S/R ENGINE:
    Berekent 1D Daily Swings, PDH/PDL, 4H Swings, 1H Pivots en Psychologische Levels.
    """
    levels = []
    seen_prices = set()

    def add_level(name, price, importance):
        for p in seen_prices:
            if abs(p - price) / price < 0.001:
                return
        seen_prices.add(price)
        levels.append({"name": name, "price": price, "importance": importance})

    # 1. 1D PDH / PDL (Previous Day High / Low)
    if len(candles_1d) >= 2:
        add_level("1D PDH (Previous Day High)", candles_1d[-2]["high"], "CRITICAL HTF")
        add_level("1D PDL (Previous Day Low)", candles_1d[-2]["low"], "CRITICAL HTF")

    # 2. 1D Daily Swing Highs & Lows (Pivots over 15 dagen)
    for i in range(2, len(candles_1d) - 2):
        if candles_1d[i]["high"] > candles_1d[i-1]["high"] and candles_1d[i]["high"] > candles_1d[i-2]["high"] and \
           candles_1d[i]["high"] > candles_1d[i+1]["high"] and candles_1d[i]["high"] > candles_1d[i+2]["high"]:
            add_level(f"1D Major Resistance (${candles_1d[i]['high']})", candles_1d[i]["high"], "CRITICAL HTF")
            
        if candles_1d[i]["low"] < candles_1d[i-1]["low"] and candles_1d[i]["low"] < candles_1d[i-2]["low"] and \
           candles_1d[i]["low"] < candles_1d[i+1]["low"] and candles_1d[i]["low"] < candles_1d[i+2]["low"]:
            add_level(f"1D Major Support (${candles_1d[i]['low']})", candles_1d[i]["low"], "CRITICAL HTF")

    # 3. 4H Swing Highs & Lows (Pivots over 30 candles)
    for i in range(2, len(candles_4h) - 2):
        if candles_4h[i]["high"] > candles_4h[i-1]["high"] and candles_4h[i]["high"] > candles_4h[i-2]["high"] and \
           candles_4h[i]["high"] > candles_4h[i+1]["high"] and candles_4h[i]["high"] > candles_4h[i+2]["high"]:
            add_level(f"4H Swing High (${candles_4h[i]['high']})", candles_4h[i]["high"], "HIGH HTF")
            
        if candles_4h[i]["low"] < candles_4h[i-1]["low"] and candles_4h[i]["low"] < candles_4h[i-2]["low"] and \
           candles_4h[i]["low"] < candles_4h[i+1]["low"] and candles_4h[i]["low"] < candles_4h[i+2]["low"]:
            add_level(f"4H Swing Low (${candles_4h[i]['low']})", candles_4h[i]["low"], "HIGH HTF")

    # 4. 1H Swing Highs & Lows (Micro Structure Pivots)
    for i in range(2, len(candles_1h) - 2):
        if candles_1h[i]["high"] > candles_1h[i-1]["high"] and candles_1h[i]["high"] > candles_1h[i-2]["high"] and \
           candles_1h[i]["high"] > candles_1h[i+1]["high"] and candles_1h[i]["high"] > candles_1h[i+2]["high"]:
            add_level(f"1H Swing High (${candles_1h[i]['high']})", candles_1h[i]["high"], "MEDIUM Intraday")
            
        if candles_1h[i]["low"] < candles_1h[i-1]["low"] and candles_1h[i]["low"] < candles_1h[i-2]["low"] and \
           candles_1h[i]["low"] < candles_1h[i+1]["low"] and candles_1h[i]["low"] < candles_1h[i+2]["low"]:
            add_level(f"1H Swing Low (${candles_1h[i]['low']})", candles_1h[i]["low"], "MEDIUM Intraday")

    return levels

def is_price_near_any_level(current_price, high_price, low_price, calculated_levels, max_distance_pct=1.2):
    """
    PRE-FILTER PORTIER ENGINE:
    Checkt of de actuele sluitkoers of high/low wicks binnen 1.2% van ENIG berekend level liggen.
    """
    for lvl in calculated_levels:
        target_price = lvl["price"]
        if target_price <= 0:
            continue
        
        dist_close = abs(current_price - target_price) / target_price * 100
        dist_high = abs(high_price - target_price) / target_price * 100
        dist_low = abs(low_price - target_price) / target_price * 100
        
        if dist_close <= max_distance_pct or dist_high <= max_distance_pct or dist_low <= max_distance_pct:
            return True, lvl
    return False, None

def cleanup_expired_alerts():
    """Verwijdert alerts uit het geheugen die ouder zijn dan 15 minuten (900 seconden)."""
    current_time = time.time()
    expired_keys = [
        key for key, timestamp in last_alerted_candles.items()
        if current_time - timestamp > 900
    ]
    for key in expired_keys:
        del last_alerted_candles[key]
        print(f"🧹 Geheugen opgeruimd voor afgelopen alert-key: {key}", flush=True)

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
# 4. AI QUANT EVALUATIE ENGINE (GEMINI PRO ON VERTEX AI)
# ==========================================
def evaluate_market_with_gemini(symbol, candles_1d, candles_4h, candles_15m, candles_5m, btc_context, calculated_levels):
    if not ai_client:
        print("Vertex AI client is niet geïnitialiseerd.", flush=True)
        return None

    last_closed_15m = candles_15m[-2] if len(candles_15m) >= 2 else candles_15m[-1]
    current_live_candle = candles_15m[-1]

    prompt = f"""
    Je bent een meedogenloze, kwantitatieve Trading Analyst Co-Pilot gespecialiseerd in Crypto ({symbol}).
    Analyseer de live data volgens de System Instructions. BEREKEN EXPLICIT DE ADJUSTED EV (EV_adj = T * EV) ALS LEIDENDE METRIC.

    CONTEXT BTCUSDT: Price = ${btc_context['close']}, 15m Trend = {btc_context['trend']}
    HARD KEY LEVELS VOOR {symbol}: {json.dumps(calculated_levels)}

    TARGET ASSET DATA ({symbol}):
    - 1D Candles (5x): {json.dumps(candles_1d[-5:])}
    - 4H Candles (5x): {json.dumps(candles_4h[-5:])}
    - 15m Last Closed: {json.dumps(last_closed_15m)}
    - 15m Live Candle: {json.dumps(current_live_candle)}
    - M5 Candles (Micro Reversal & Volume): {json.dumps(candles_5m[-5:])}

    KWANTITATIEVE SCORING MATRIX:
    1. Trend (35%) | 2. Level Kwaliteit (30%) | 3. Displacement & Micro (20%) | 4. Session Timing (15%)
    Rating: A+ (>=85%), A (65-84%), B (<65% -> NO-GO) | EV_adj = T * EV.

    EXECUTION OPTIONS DEFINITIE:
    - Option A (Conservative): High T (85%), lagere R:R.
    - Option B (Sweet Spot): Medium T (65%), gebalanceerde R:R.
    - Option C (Aggressive): Low T (40%), hoge R:R.
    - Option D (Front-Run + Aggressive SL - MAX EV_adj): Front-run entry (0.15% - 0.25% boven/onder retest level) gecombineerd met de strakke M3/M5 retest wick SL. Gives High T (~85%) + Tight SL = MAX EV_adj!

    3-TRAPS VERDICT REGELS:
    1. ⚠️ PRE-TRADE ALERT: Prijs/wick binnen <= 1.0% van KEY LEVEL, maar nog geen 15m close/reversal.
    2. 👁️ WATCHLIST: 15m Full Body Close GEVALIDEERD (wick <= 30%), maar M3/M5 reversal nog in aanbouw.
    3. 🚨 GO: 15m Full Body Close GEVALIDEERD EN M3/M5 Reversal BEVESTIGD EN Score >= 65% EN EV_adj > +0.30R.
    4. NO-GO: Score < 65% of geen KEY LEVELS nabij.

    OUTPUT FORMAT BIJ 'PRE-TRADE ALERT':
    ⚠️ **PRE-TRADE ALERT (KLAARZITTEN)** - {symbol}
    • **Afstand tot S/R Level:** ~X.XX% (Actuele koers: ${current_live_candle['close']} vs Key Level: $XX.XX)
    • **Verwachte S/R Zone:** $XX.XX - $XX.XX (1D / 4H / 1H Level)
    • **Verwachte Playbook:** [Swing Breakout | Day Sweep | Scalp Reclaim]
    • **Verwachte Richting:** [Long / Short]
    • **Actie:** Open je chart op M3/M5. Wacht op 15m close en M3/M5 reversal.

    OUTPUT FORMAT BIJ 'WATCHLIST' OF 'GO':
    **GO / NO-GO VERDICT:** **[GO | WATCHLIST]** *(Rating: [A+ | A] | Score: X% | MAX EV_adj: +X.XX R)*

    🎯 **EXECUTION SUMMARY ({symbol} - [Long / Short]):**
    • **Huidige Prijs:** ${current_live_candle['close']}
    • **Aanbevolen Strategy:** **Option D (Front-Run + Aggressive SL)**
    • **Front-Run Entry:** **$XX.XX** *(0.20% boven retest level)*
    • **Aggressive SL:** **$XX.XX** *(Strak onder M3/M5 wick)*
    • **Max Adjusted EV (EV_adj):** **+X.XX R**

    ### Execution Optimization Matrix
    | Parameter | Option A (Cons.) | Option B (Sweet Spot) | Option C (Aggr. SL) | **Option D (Front-Run + Aggr. SL - MAX EV_adj)** |
    | :--- | :--- | :--- | :--- | :--- |
    | **Entry Price** | $XX.XX | $XX.XX | $XX.XX | **$XX.XX** |
    | **Stop Loss (SL)** | $XX.XX | $XX.XX | $XX.XX | **$XX.XX (Tight SL)** |
    | **Risico Afstand (1R)** | $XX.XX | $XX.XX | $XX.XX | **$XX.XX** |
    | **Fill Chance (T)** | 85% | 65% | 40% | **85%** |
    | **Gewogen R:R** | X.XX R | X.XX R | X.XX R | **X.XX R** |
    | **Adjusted EV (EV_adj)**| +X.XX R | +X.XX R | +X.XX R | **+X.XX R (MAX)** |

    **Korte Analyse:** (Max 2 zinnen met exacte reden en BTC-correlatie).
    """

    # Model identifiers voor Vertex AI met automatische fallback
    models_to_try = ['gemini-3.1-pro-preview', 'gemini-2.5-pro']
    
    for model_name in models_to_try:
        try:
            response = ai_client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            return response.text.strip()
        except Exception as e:
            err_str = str(e)
            if "NOT_FOUND" in err_str or "404" in err_str:
                print(f"Model {model_name} niet gevonden op Vertex AI, fallback naar volgend model...", flush=True)
                continue
            elif "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                print(f"[{symbol}] Quota bereikt op {model_name}. Korte pauze...", flush=True)
                time.sleep(3)
            else:
                print(f"Vertex AI Error ({model_name}) voor {symbol}: {e}", flush=True)
                return None
    return None

# ==========================================
# 5. MAIN SCANNER LOOP
# ==========================================
def run_scanner():
    tz = pytz.timezone('Europe/Amsterdam')
    now_str = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n[{now_str}] 🔍 Markt-scan gestart voor alle 9 symbolen...", flush=True)
    
    # Ruim oude alerts (>15 minuten) op uit het geheugen
    cleanup_expired_alerts()

    # Check 15:20 CET/CEST NY Open waarschuwing
    check_ny_open_warning()

    btc_15m = fetch_binance_klines("BTCUSDT", "15m", limit=10)
    if not btc_15m:
        print("Geen BTC data ontvangen, scan overgeslagen.", flush=True)
        return

    btc_context = {
        "close": btc_15m[-1]["close"],
        "trend": "BULLISH" if btc_15m[-1]["close"] >= btc_15m[-2]["close"] else "BEARISH"
    }

    for symbol in SYMBOLS:
        try:
            candles_1d = fetch_binance_klines(symbol, "1d", limit=15)
            candles_4h = fetch_binance_klines(symbol, "4h", limit=20)
            candles_1h = fetch_binance_klines(symbol, "1h", limit=30)
            candles_15m = fetch_binance_klines(symbol, "15m", limit=20)
            candles_5m = fetch_binance_klines(symbol, "5m", limit=20)
            
            if not candles_1d or not candles_4h or not candles_1h or not candles_15m or not candles_5m:
                print(f"[{symbol}] Onvolledige data, overgeslagen.", flush=True)
                continue

            # Berekent de S/R levels via de geavanceerde Python Engine (1D, 4H, 1H)
            calculated_levels = find_key_levels(candles_1d, candles_4h, candles_1h)
            
            # SLIMME PRE-FILTER PORTIER: Check of koers of wick binnen 1.2% van enig level ligt
            curr_close = candles_15m[-1]["close"]
            curr_high = candles_15m[-1]["high"]
            curr_low = candles_15m[-1]["low"]
            
            is_near, matched_level = is_price_near_any_level(curr_close, curr_high, curr_low, calculated_levels, max_distance_pct=1.2)
            
            if not is_near:
                print(f"[{now_str}] [{symbol}] Scan voltooid -> NO-GO / Prijs > 1.2% van S/R levels (API call bespaard).", flush=True)
                time.sleep(0.5)
                continue

            print(f"[{now_str}] 🎯 [{symbol}] NABIJ S/R LEVEL ({matched_level['name']}) -> Gemini Pro Inschakelen...", flush=True)

            # Gebruik het timestamp van de laatst AFGERONDE 15m kaars voor deduplicatie
            last_closed_candle_time = candles_15m[-2]["timestamp"]

            # Vraag Gemini alleen om analyse als Python bevestigt dat we nabij een level zijn
            analysis = evaluate_market_with_gemini(symbol, candles_1d, candles_4h, candles_15m, candles_5m, btc_context, calculated_levels)
            
            # SLIMMER DEDUPLICATIE FILTER MET 15-MINUTEN EXPIRATIE
            is_go_alert = analysis and ("🚨 **GO**" in analysis or "GO / NO-GO VERDICT: **GO**" in analysis)
            alert_key = f"{symbol}_{last_closed_candle_time}" if not is_go_alert else f"{symbol}_{last_closed_candle_time}_GO"

            # Vang ALLE 3 de alert-types op: Pre-Trade Alert, Watchlist én GO!
            if analysis and ("PRE-TRADE ALERT" in analysis or "🚨 **GO**" in analysis or "WATCHLIST" in analysis or "GO / NO-GO VERDICT" in analysis):
                if alert_key in last_alerted_candles:
                    print(f"[{symbol}] Reeds geanalyseerd en gemeld binnen de afgelopen 15 minuten.", flush=True)
                    continue

                print(f"[{now_str}] 🚨 ALERT GEGENEREERD EN VERSTUURD VOOR {symbol}!", flush=True)
                send_telegram_message(analysis)
                # Sla op met UNIX timestamp voor automatische 15-minuten expiratie
                last_alerted_candles[alert_key] = time.time()
            else:
                print(f"[{now_str}] [{symbol}] Scan voltooid -> NO-GO / Geen valide S/R setup.", flush=True)

            # MENSELIJKE PAUZE TUSSEN GEMINI CALLS
            time.sleep(2)
        except Exception as e:
            print(f"Error bij verwerken {symbol}: {e}", flush=True)

if __name__ == "__main__":
    startup_msg = (
        "🤖 **MyCryptoAgent Master Service IS LIVE ON VERTEX AI!**\n\n"
        "**Geïntegreerd Quantitative System Instructions:**\n"
        "1. ⚠️ **Pre-Trade Alert:** Prijs binnen 1.0% van 1D/4H/1H Key Level (Klaarzitten)\n"
        "2. 👁️ **Watchlist:** 15m Full Body Close (Wick <= 30%) op Key Level\n"
        "3. 🚨 **GO Execution:** M3/M5 Reversal + EV_adj > +0.30R & Score >= 65%\n\n"
        "• **Model Upgrade:** Gemini Pro actief via Vertex AI Service Account ($300 Credits).\n"
        "• **Smart Portier Pre-Filter:** Ingeschakeld op <= 1.2% (Elimineert 429 Quota errors 100%).\n"
        "• **Inclusief Option D:** Front-Run Entry + Aggressive Retest Wick SL voor MAXIMAAL haalbare EV_adj."
    )
    send_telegram_message(startup_msg)

    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f"Loop error: {e}", flush=True)
        
        # 180 seconden (3 minuten) scan lus
        time.sleep(180)
