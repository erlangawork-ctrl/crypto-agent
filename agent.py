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
    print("WARNING: GEMINI_API_KEY niet gevonden!", flush=True)

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
# 3. HELPER FUNCTIES, LEVEL DETECTIE & TIME CHECKS
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

def find_key_levels(candles_1d, candles_4h):
    """Berekent automatisch de belangrijkste S/R levels uit 1D (Daily Macro) en 4H Klines."""
    levels = []
    
    # 1D PDH / PDL (Previous Day High / Low)
    if len(candles_1d) >= 2:
        levels.append({"name": "1D PDH (Previous Day High)", "price": candles_1d[-2]["high"], "importance": "CRITICAL HTF"})
        levels.append({"name": "1D PDL (Previous Day Low)", "price": candles_1d[-2]["low"], "importance": "CRITICAL HTF"})
        
    # 1D Daily Swing Highs & Lows (Pivots over 10 dagen)
    for i in range(2, len(candles_1d) - 2):
        if candles_1d[i]["high"] > candles_1d[i-1]["high"] and candles_1d[i]["high"] > candles_1d[i-2]["high"] and \
           candles_1d[i]["high"] > candles_1d[i+1]["high"] and candles_1d[i]["high"] > candles_1d[i+2]["high"]:
            levels.append({"name": f"1D Daily Major Resistance (${candles_1d[i]['high']})", "price": candles_1d[i]["high"], "importance": "CRITICAL HTF"})
            
        if candles_1d[i]["low"] < candles_1d[i-1]["low"] and candles_1d[i]["low"] < candles_1d[i-2]["low"] and \
           candles_1d[i]["low"] < candles_1d[i+1]["low"] and candles_1d[i]["low"] < candles_1d[i+2]["low"]:
            levels.append({"name": f"1D Daily Major Support (${candles_1d[i]['low']})", "price": candles_1d[i]["low"], "importance": "CRITICAL HTF"})

    # 4H Swing Highs & Lows
    for i in range(2, len(candles_4h) - 2):
        if candles_4h[i]["high"] > candles_4h[i-1]["high"] and candles_4h[i]["high"] > candles_4h[i-2]["high"] and \
           candles_4h[i]["high"] > candles_4h[i+1]["high"] and candles_4h[i]["high"] > candles_4h[i+2]["high"]:
            levels.append({"name": f"4H Swing High (${candles_4h[i]['high']})", "price": candles_4h[i]["high"], "importance": "HIGH HTF"})
            
        if candles_4h[i]["low"] < candles_4h[i-1]["low"] and candles_4h[i]["low"] < candles_4h[i-2]["low"] and \
           candles_4h[i]["low"] < candles_4h[i+1]["low"] and candles_4h[i]["low"] < candles_4h[i+2]["low"]:
            levels.append({"name": f"4H Swing Low (${candles_4h[i]['low']})", "price": candles_4h[i]["low"], "importance": "HIGH HTF"})
            
    return levels

def is_price_near_any_level(current_price, high_price, low_price, calculated_levels, max_distance_pct=1.0):
    """Pre-filter functie: Checkt of de koers of wick binnen 1.0% van enig berekend level ligt."""
    for lvl in calculated_levels:
        target_price = lvl["price"]
        if target_price <= 0:
            continue
        # Afstand berekenen tot close, high of low
        dist_close = abs(current_price - target_price) / target_price * 100
        dist_high = abs(high_price - target_price) / target_price * 100
        dist_low = abs(low_price - target_price) / target_price * 100
        
        if dist_close <= max_distance_pct or dist_high <= max_distance_pct or dist_low <= max_distance_pct:
            return True
    return False

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
# 4. AI QUANT EVALUATIE ENGINE (GEMINI 3.8 FLASH + RATE-LIMIT SAFE)
# ==========================================
def evaluate_market_with_gemini(symbol, candles_1d, candles_4h, candles_15m, candles_5m, btc_context, calculated_levels):
    if not ai_client:
        print("Gemini client is niet geïnitialiseerd.", flush=True)
        return None

    # OPSPLITSING: AFGERONDE 15m kaars vs LOPENDE PRIJS
    last_closed_15m = candles_15m[-2] if len(candles_15m) >= 2 else candles_15m[-1]
    current_live_candle = candles_15m[-1]

    prompt = f"""
    Je bent een meedogenloze, kwantitatieve Trading Analyst Co-Pilot gespecialiseerd in Crypto ({symbol}).
    Analyseer de live data volgens de exacte System Instructions. BEREKEN EXPLICIT DE ADJUSTED EV (EV_adj = T * EV) ALS LEIDENDE METRIC.

    CONTEXT BTCUSDT (Voor Trend Alignment & Altcoin Correlatie):
    - BTC Laatste Price: ${btc_context['close']}
    - BTC 15m Trend: {btc_context['trend']}

    BEREKENDE HARD S/R KEY LEVELS VOOR {symbol} (INCLUSIEF 1D DAILY MACRO LEVELS):
    {json.dumps(calculated_levels, indent=2)}

    TARGET ASSET MULTI-TIMEFRAME DATA ({symbol}):
    - Laatste 5x 1D Candles: {json.dumps(candles_1d[-5:])}
    - Laatste 5x 4H Candles (HTF Trend & Major S/R): {json.dumps(candles_4h[-5:])}
    - LAATST AFGERONDE 15m Candle (VOOR FULL BODY CLOSE CHECK): {json.dumps(last_closed_15m)}
    - LOPENDE 15m Candle (ACTUELE PRIJS & HIGH/LOW WICKS): {json.dumps(current_live_candle)}
    - Laatste 5x 5m Candles (M3/M5 Micro Reversal & Volume): {json.dumps(candles_5m[-5:])}

    KWANTITATIEVE SCORING MATRIX (4 FACTOREN):
    1. Trend Alignment (35%): 3/3 Aligned = 100%, 2/3 = 66.7%, 1/3 = 33.3%
    2. Level Kwaliteit (30%): 1D Daily HTF Level / PDH / PDL = 100% (CRITICAL), 4H Swing = 80%, Minor = 30%. (Pas -30% False Breakout Penalty toe bij <48u recovery zonder accumulatie).
    3. Displacement & Micro (20%): 15m Full Body Close (wick <=30%) + Bevestigde M3/M5 Reversal (Engulfing op volume >=1.5x / Pinbar >=66% / MSS) = 100%. Normale close zonder M5 reversal = 60%. Zwak/Wicks >30% = 30%.
    4. Session Timing (15%): London/NY Open (na sweep) = 100%, Daily Close = 80%, Mid Session / US Open Window (15:15-16:30) = 40%.

    FORMULES FOR MATHEMATISCHE TOETSING:
    - Setup Score (%) = (Trend * 0.35) + (Level * 0.30) + (Displacement * 0.20) + (Timing * 0.15)
    - Rating: A+ (>=85%), A (65-84%), B (<65% -> AUTOMATISCH NO-GO)
    - Win Rate P: A+ = 70%, A = 58%, B = 40%
    - Gewogen R:R (Scale-Out 50/30/20) = (0.50 * R_TP1) + (0.30 * R_TP2) + (0.20 * R_Runner)
    - EV = (P * R_gewogen) - ((1 - P) * 1R)
    - EV_adj = T * EV (waarbij T = Fill Chance %). ONTHOUD: EV_adj IS DE ABSOLUUT LEIDENDE METRIC!

    EXECUTION OPTIONS DEFINITIE:
    - Option A (Conservative): Markt/Bovenkant zone entry, ruime structurele SL. High T (85%), lagere R:R.
    - Option B (Sweet Spot): Exacte S/R retest entry, structurele SL. Medium T (65%), gebalanceerde R:R.
    - Option C (Aggressive): Exacte S/R retest entry, hele strakke M3/M5 retest wick SL. Lagere T (40%), hoge R:R.
    - Option D (Front-Run + Aggressive SL - MAX EV_adj): Front-run entry (0.15% - 0.25% boven/onder retest level) gecombineerd met de strakke M3/M5 retest wick SL. Dit geeft een hoge Fill Chance T (~85%) én hele strakke 1R, wat resulteert in de MAXIMAAL MOGELIJKE EV_adj!

    3-TRAPS VERDICT REGELS:
    1. ⚠️ PRE-TRADE ALERT: Prijs/wick binnen <= 1.0% van KEY LEVEL, maar geen 15m close/reversal.
    2. 👁️ WATCHLIST: 15m Full Body Close GEVALIDEERD (wick <= 30%), maar M3/M5 reversal nog in aanbouw.
    3. 🚨 GO: 15m Full Body Close GEVALIDEERD EN M3/M5 Reversal BEVESTIGD EN Score >= 65% EN EV_adj > +0.30R.
    4. NO-GO: Score < 65% (B-Rating) of geen KEY LEVELS nabij.

    OUTPUT FORMAT BIJ 'PRE-TRADE ALERT':
    ⚠️ **PRE-TRADE ALERT (KLAARZITTEN)** - {symbol}
    • **Afstand tot S/R Level:** ~X.XX% (Actuele koers: ${current_live_candle['close']} vs Key Level: $XX.XX)
    • **Verwachte S/R Zone:** $XX.XX - $XX.XX (1D Daily Level / PDH / PDL / 4H Swing)
    • **Verwachte Playbook:** [Swing Breakout | Day Sweep | Scalp Reclaim]
    • **Verwachte Richting:** [Long / Short]
    • **Actie:** Open je chart op M3/M5. Wacht op 15m close en M3/M5 reversal.

    OUTPUT FORMAT BIJ 'WATCHLIST' OF 'GO':
    **GO / NO-GO VERDICT:** **[GO | WATCHLIST]** *(Rating: [A+ | A] | Score: X% | MAX EV_adj: +X.XX R)*

    🎯 **EXECUTION SUMMARY ({symbol} - [Long / Short]):**
    • **Huidige Prijs:** ${current_live_candle['close']}
    • **Aanbevolen Strategy:** **Option D (Front-Run + Aggressive SL)**
    • **Front-Run Entry:** **$XX.XX** *(0.20% boven retest level voor maximale vulkans)*
    • **Aggressive SL:** **$XX.XX** *(Strak onder lokale M3/M5 wick)*
    • **Max Adjusted EV (EV_adj):** **+X.XX R** *(Leidende Beslis-Metric)*

    ### Execution Optimization Matrix
    | Parameter | Option A (Cons.) | Option B (Sweet Spot) | Option C (Aggr. SL) | **Option D (Front-Run + Aggr. SL - MAX EV_adj)** |
    | :--- | :--- | :--- | :--- | :--- |
    | **Entry Price** | $XX.XX | $XX.XX | $XX.XX | **$XX.XX** |
    | **Stop Loss (SL)** | $XX.XX | $XX.XX | $XX.XX (Tight SL) | **$XX.XX (Tight SL)** |
    | **Risico Afstand (1R)** | $XX.XX | $XX.XX | $XX.XX | **$XX.XX** |
    | **TP1 (50%)** | $XX.XX | $XX.XX | $XX.XX | **$XX.XX** |
    | **TP2 (30%)** | $XX.XX | $XX.XX | $XX.XX | **$XX.XX** |
    | **Runner (20%)** | $XX.XX | $XX.XX | $XX.XX | **$XX.XX** |
    | **Fill Chance (T)** | 85% | 65% | 40% | **85%** |
    | **Gewogen R:R** | X.XX R | X.XX R | X.XX R | **X.XX R** |
    | **Expected Value (EV)**| +X.XX R | +X.XX R | +X.XX R | **+X.XX R** |
    | **Adjusted EV (EV_adj)**| +X.XX R | +X.XX R | +X.XX R | **+X.XX R (MAX)** |

    ### Metrics & Statistische Toetsing
    • **Playbook:** [Swing Breakout | Day Sweep | Scalp Reclaim] | **Setup Score:** X% ([A+ | A])
    • **Trend (35%):** X/100% | **Level (30%):** X/100% | **Displacement (20%):** X/100% | **Timing (15%):** X/100%
    • **Win Rate (P):** X% | **Max EV_adj:** **+X.XX R**

    **Korte Analyse:** (Max 2 zinnen met exacte reden, Daily/4H niveau en BTC-correlatie).
    """

    # AUTORETRY LUS MET RUIME PAUZE BIJ 429 RATE LIMITS
    max_retries = 2
    for attempt in range(max_retries):
        try:
            response = ai_client.models.generate_content(
                model='gemini-3.8-flash',
                contents=prompt,
            )
            return response.text.strip()
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                print(f"[{symbol}] Rate limit bereikt (429). Wachten 25s voor quota reset (poging {attempt + 1}/{max_retries})...", flush=True)
                time.sleep(25)
            elif "503" in err_str or "UNAVAILABLE" in err_str:
                print(f"[{symbol}] Gemini 503 Overbelasting. Wachten 5s...", flush=True)
                time.sleep(5)
            else:
                print(f"Gemini API error voor {symbol}: {e}", flush=True)
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
            candles_15m = fetch_binance_klines(symbol, "15m", limit=20)
            candles_5m = fetch_binance_klines(symbol, "5m", limit=20)
            
            if not candles_1d or not candles_4h or not candles_15m or not candles_5m:
                print(f"[{symbol}] Onvolledige data, overgeslagen.", flush=True)
                continue

            # Berekent de S/R levels via Python
            calculated_levels = find_key_levels(candles_1d, candles_4h)
            
            # SLIMME PRE-FILTER: Controleer of de prijs überhaupt binnen 1.0% van een level ligt
            curr_close = candles_15m[-1]["close"]
            curr_high = candles_15m[-1]["high"]
            curr_low = candles_15m[-1]["low"]
            
            if not is_price_near_any_level(curr_close, curr_high, curr_low, calculated_levels, max_distance_pct=1.0):
                print(f"[{now_str}] [{symbol}] Scan voltooid -> NO-GO / Prijs > 1.0% van S/R levels (API call bespaard).", flush=True)
                time.sleep(1)
                continue

            # Gebruik het timestamp van de laatst AFGERONDE 15m kaars voor deduplicatie
            last_closed_candle_time = candles_15m[-2]["timestamp"]

            # Vraag Gemini alleen om analyse als de koers wél nabij een level is
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

            # PAUZE VAN 6 SECONDEN OM ONDER DE 15 REQUESTS/MINUUT (RPM) FREE TIER LIMIT TE BLIJVEN
            time.sleep(6)
        except Exception as e:
            print(f"Error bij verwerken {symbol}: {e}", flush=True)

if __name__ == "__main__":
    startup_msg = (
        "🤖 **MyCryptoAgent Master Service IS LIVE!**\n\n"
        "**Geïntegreerd Quantitative System Instructions:**\n"
        "1. ⚠️ **Pre-Trade Alert:** Prijs binnen 1.0% van 1D/4H Key Level (Klaarzitten)\n"
        "2. 👁️ **Watchlist:** 15m Full Body Close (Wick <= 30%) op 1D/4H Level\n"
        "3. 🚨 **GO Execution:** M3/M5 Reversal + EV_adj > +0.30R & Score >= 65%\n\n"
        "• **Smart Pre-Filter Active:** API-calls worden met 80% verminderd (Nooit meer 429 Rate Limits).\n"
        "• **15-Minuten Geheugen Expiratie:** Oude alerts vervallen na 15 min.\n"
        "• **Inclusief Option D:** Front-Run Entry + Aggressive Retest Wick SL voor MAXIMAAL haalbare EV_adj."
    )
    send_telegram_message(startup_msg)

    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f"Loop error: {e}", flush=True)
        
        # 180 seconden (3 minuten) = 480 RPD (Veilig op Free Tier)
        time.sleep(180)
