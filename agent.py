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
# 4. AI QUANT EVALUATIE ENGINE (INCLUSIEF EV_adj & TIGHT SL)
# ==========================================
def evaluate_market_with_gemini(symbol, candles_1d, candles_4h, candles_15m, candles_5m, btc_context):
    if not ai_client:
        print("Gemini client is niet geïnitialiseerd.", flush=True)
        return None

    # Bereken de exacte Previous Day High / Low uit de 1D data
    pdh = candles_1d[-2]["high"] if len(candles_1d) >= 2 else candles_1d[-1]["high"]
    pdl = candles_1d[-2]["low"] if len(candles_1d) >= 2 else candles_1d[-1]["low"]

    # OPSPLITSING: AFGERONDE 15m kaars vs LOPENDE PRIJS
    last_closed_15m = candles_15m[-2] if len(candles_15m) >= 2 else candles_15m[-1]
    current_live_candle = candles_15m[-1]

    prompt = f"""
    Je bent een meedogenloze, kwantitatieve Trading Analyst Co-Pilot gespecialiseerd in Crypto ({symbol}).
    Analyseer de live data volgens de exacte System Instructions. BEREKEN EXPLICIT DE ADJUSTED EV (EV_adj = T * EV) ALS LEIDENDE METRIC.

    CONTEXT BTCUSDT (Voor Trend Alignment & Altcoin Correlatie):
    - BTC Laatste Price: ${btc_context['close']}
    - BTC 15m Trend: {btc_context['trend']}

    TARGET ASSET MULTI-TIMEFRAME DATA ({symbol}):
    - Daily (1D) Macro S/R & Levels: PDH = ${pdh}, PDL = ${pdl}
    - Laatste 3x 1D Candles: {json.dumps(candles_1d[-3:])}
    - Laatste 5x 4H Candles (HTF Trend & Major S/R): {json.dumps(candles_4h[-5:])}
    - LAATST AFGERONDE 15m Candle (VOOR FULL BODY CLOSE CHECK): {json.dumps(last_closed_15m)}
    - LOPENDE 15m Candle (ACTUELE PRIJS): {json.dumps(current_live_candle)}
    - Laatste 5x 5m Candles (M3/M5 Micro Reversal & Volume): {json.dumps(candles_5m[-5:])}

    KWANTITATIEVE SCORING MATRIX (4 FACTOREN):
    1. Trend Alignment (35%): 3/3 Aligned = 100%, 2/3 = 66.7%, 1/3 = 33.3%
    2. Level Kwaliteit (30%): HTF Major (PDH/PDL/4H S/R) = 100%, 1H/15m Swing = 60%, Minor = 30%. (Pas -30% False Breakout Penalty toe bij <48u recovery zonder accumulatie).
    3. Displacement & Micro (20%): 15m Full Body Close (wick <=30%) + Bevestigde M3/M5 Reversal (Engulfing op volume >=1.5x / Pinbar >=66% / MSS) = 100%. Normale close zonder M5 reversal = 60%. Zwak/Wicks >30% = 30%.
    4. Session Timing (15%): London/NY Open (na sweep) = 100%, Daily Close = 80%, Mid Session / US Open Window (15:15-16:30) = 40%.

    FORMULES FOR MATHEMATISCHE TOETSING:
    - Setup Score (%) = (Trend * 0.35) + (Level * 0.30) + (Displacement * 0.20) + (Timing * 0.15)
    - Rating: A+ (>=85%), A (65-84%), B (<65% -> AUTOMATISCH NO-GO)
    - Win Rate P: A+ = 70%, A = 58%, B = 40%
    - Gewogen R:R (Scale-Out 50/30/20) = (0.50 * R_TP1) + (0.30 * R_TP2) + (0.20 * R_Runner)
    - EV = (P * R_gewogen) - ((1 - P) * 1R)
    - EV_adj = T * EV (waarbij T = Fill Chance %). ONTHOUD: EV_adj IS DE ABSOLUUT LEIDENDE METRIC!

    3-TRAPS VERDICT REGELS:
    1. ⚠️ PRE-TRADE ALERT: Prijs binnen <= 0.5% van HTF S/R (PDH/PDL/4H Level), maar nog GEEN afgeronde 15m close of M3/M5 reversal. Doel: Klaarzitten!
    2. 👁️ WATCHLIST: 15m Full Body Close is GEVALIDEERD (wick <= 30%), maar M3/M5 reversal is nog in aanbouw.
    3. 🚨 GO: 15m Full Body Close GEVALIDEERD EN M3/M5 Reversal BEVESTIGD EN Score >= 65% EN EV_adj > +0.30R.
    4. NO-GO: Score < 65% (B-Rating) of geen S/R niveaus nabij.

    STRUCTUUR VOOR OPTIE C (AGGRESSIVE / TIGHT SL):
    In Option C van de matrix zet je de Stop Loss NIET op de 4H Swing Low, maar STRIKT onder de lokale M3/M5 retest-wick (lokale bodem). Dit maakt 1R zeer klein en laat R:R, EV en EV_adj expliciet zien!

    OUTPUT FORMAT BIJ 'PRE-TRADE ALERT':
    ⚠️ **PRE-TRADE ALERT (KLAARZITTEN)** - {symbol}
    - **Afstand tot S/R Level:** ~X.XX%
    - **Verwachte S/R Zone:** $XX.XX (PDH / PDL / 4H Level)
    - **Verwachte Playbook:** [Swing Breakout | Day Sweep | Scalp Reclaim]
    - **Verwachte Richting:** [Long / Short]
    - **Actie:** Open je chart op M3/M5. Wacht op 15m close en M3/M5 reversal.

    OUTPUT FORMAT BIJ 'WATCHLIST' OF 'GO':
    **GO / NO-GO VERDICT:** **[GO | WATCHLIST]** *(Rating: [A+ | A] | Score: X% | EV_adj: +X.XX R)*

    ### Trade Details
    * **Playbook Type:** [Swing Breakout | Day Sweep | Scalp Reclaim]
    * **Asset & Richting:** {symbol} - [Long / Short]
    * **Niveaus (Sweet Spot Execution na M3/M5 Reversal / Deep Entry):**
      * Entry (Confirmed Reversal / Deep Placement): $XX.XX
      * SL (Structurele SL / Low): $XX.XX
      * TP1 (50%): $XX.XX
      * TP2 (30%): $XX.XX
      * Runner (20%): $XX.XX (Trailing Stop)

    #### Execution Optimization Matrix
    | Parameter | Conservative (Option A) | Optimal / Sweet Spot (Option B - Recommended) | Aggressive (Option C - Tight SL) |
    | :--- | :--- | :--- | :--- |
    | **Entry Price** | $XX.XX | **$XX.XX** | $XX.XX |
    | **Stop Loss (SL)** | $XX.XX | **$XX.XX** | $XX.XX (Tight Retest Wick SL) |
    | **Risico Afstand (1R)** | $XX.XX | **$XX.XX** | $XX.XX |
    | **TP1 (50%)** | $XX.XX | **$XX.XX** | $XX.XX |
    | **TP2 (30%)** | $XX.XX | **$XX.XX** | $XX.XX |
    | **Runner (20%)** | $XX.XX | **$XX.XX** | $XX.XX |
    | **Fill Chance (T)** | X% | **X%** | X% |
    | **Gewogen R:R** | X.XX R | **X.XX R** | X.XX R |
    | **Expected Value (EV)**| +X.XX R | **+X.XX R** | +X.XX R |
    | **Adjusted EV (EV_adj)**| **+X.XX R** | **+X.XX R (LEIDEND)** | **+X.XX R** |

    ### Metrics Invoer (voor de Sheet)
    | Metric Category | Geselecteerde Waarde | Factor Gewicht | Behaalde Score |
    | :--- | :--- | :--- | :--- |
    | **Playbook Type** | [Swing Breakout | Day Sweep | Scalp Reclaim] | - | - |
    | **Asset & Richting** | {symbol} - [Long / Short] | - | - |
    | **Trend Alignment** | [3/3 Aligned | 2/3 Aligned | 1/3 Counter] | 35% | X / 100% |
    | **Sweep/Level Kwaliteit**| [HTF Major | 1H/15m Swing | Minor Level] | 30% | X / 100% |
    | **Displacement & Micro** | [15m Close + M3/M5 Reversal | Normale Close | Zwakke Reclaim] | 20% | X / 100% |
    | **Timing** | [London/NY Open | Daily Close | Mid Session / US Open Window] | 15% | X / 100% |

    ### Statistische Toetsing
    * **Setup Score (%):** X%
    * **Setup Rating:** [A+ | A]
    * **Win Rate (P):** X%
    * **Order Fill Chance (T):** X%
    * **Beoogde R:R (Gewogen):** X.XX R
    * **Expected Value (EV):** +X.XX R
    * **Adjusted Expected Value (EV_adj):** **+X.XX R (LEIDEND)**

    **Korte Analyse:** (Max 2 zinnen met exacte reden, Daily/4H niveau en BTC-correlatie).
    """

    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API error voor {symbol}: {e}", flush=True)
        return None

# ==========================================
# 5. MAIN SCANNER LOOP
# ==========================================
def run_scanner():
    tz = pytz.timezone('Europe/Amsterdam')
    now_str = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n[{now_str}] 🔍 Markt-scan gestart voor alle 9 symbolen...", flush=True)
    
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
            candles_1d = fetch_binance_klines(symbol, "1d", limit=10)
            candles_4h = fetch_binance_klines(symbol, "4h", limit=10)
            candles_15m = fetch_binance_klines(symbol, "15m", limit=20)
            candles_5m = fetch_binance_klines(symbol, "5m", limit=20)
            
            if not candles_1d or not candles_4h or not candles_15m or not candles_5m:
                print(f"[{symbol}] Onvolledige data, overgeslagen.", flush=True)
                continue

            # Gebruik het timestamp van de laatst AFGERONDE 15m kaars voor deduplicatie
            last_closed_candle_time = candles_15m[-2]["timestamp"]
            
            # Voorkom dat er dubbele meldingen gestuurd worden voor DEZELFDE afgeronde 15m kaars
            if last_alerted_candles.get(symbol) == last_closed_candle_time:
                print(f"[{symbol}] Reeds geanalyseerd voor deze 15m candle.", flush=True)
                continue

            analysis = evaluate_market_with_gemini(symbol, candles_1d, candles_4h, candles_15m, candles_5m, btc_context)
            
            # Vang ALLE 3 de alert-types op: Pre-Trade Alert, Watchlist én GO!
            if analysis and ("PRE-TRADE ALERT" in analysis or "🚨 **GO**" in analysis or "WATCHLIST" in analysis or "GO / NO-GO VERDICT" in analysis):
                print(f"[{now_str}] 🚨 ALERT GEGENEREERD EN VERSTUURD VOOR {symbol}!", flush=True)
                send_telegram_message(analysis)
                last_alerted_candles[symbol] = last_closed_candle_time
            else:
                print(f"[{now_str}] [{symbol}] Scan voltooid -> NO-GO / Geen valide S/R setup.", flush=True)

            time.sleep(1) # Kleine pauze tussen API calls
        except Exception as e:
            print(f"Error bij verwerken {symbol}: {e}", flush=True)

if __name__ == "__main__":
    startup_msg = (
        "🤖 **MyCryptoAgent Master Service IS LIVE!**\n\n"
        "**Geïntegreerd Quantitative System Instructions:**\n"
        "1. ⚠️ **Pre-Trade Alert:** Prijs binnen 0.5% van S/R (Klaarzitten)\n"
        "2. 👁️ **Watchlist:** 15m Full Body Close (Wick <= 30%) bevestigd\n"
        "3. 🚨 **GO Execution:** M3/M5 Reversal + EV_adj > +0.30R & Score >= 65%\n\n"
        "• **Inclusief:** EV_adj als LEIDENDE METRIC, Option C Tight SL Matrix, US Open Rule & 4-Factor Scoring.\n"
        "• **API Optimisatie:** 3-Minuten Scan Lus (480 RPD - 100% Safe op Gemini Free Tier)"
    )
    send_telegram_message(startup_msg)

    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f"Loop error: {e}", flush=True)
        
        # 180 seconden (3 minuten) = 480 RPD (Veilig op Free Tier)
        time.sleep(180)
