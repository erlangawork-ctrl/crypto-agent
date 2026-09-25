import json
import os
import threading
import time
from datetime import datetime

from flask import Flask
from google import genai
from google.oauth2 import service_account
import pytz
import requests

# ==========================================
# 1. MINI FLASK WEBSERVER (Render 24/7 Keep-Alive)
# ==========================================
app = Flask(__name__)


@app.route('/')
def health_check():
    status = "AAN" if scalp_alerts_enabled else "UIT"
    return f'MyCryptoAgent Co-Pilot is Active! Scalp Alerts: {status}', 200


def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)


threading.Thread(target=run_flask, daemon=True).start()

# ==========================================
# 2. CONFIGURATIE & VERTEX AI AUTHENTICATIE
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

GCP_PROJECT_ID = os.getenv('GCP_PROJECT_ID', 'crypto-ai-agent-509618')
GCP_LOCATION = os.getenv('GCP_LOCATION', 'us-central1')
GCP_SERVICE_ACCOUNT_JSON = os.getenv('GCP_SERVICE_ACCOUNT_JSON')

ai_client = None
try:
    if GCP_SERVICE_ACCOUNT_JSON:
        service_account_info = json.loads(GCP_SERVICE_ACCOUNT_JSON)
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=['https://www.googleapis.com/auth/cloud-platform'],
        )
        ai_client = genai.Client(
            vertexai=True,
            project=GCP_PROJECT_ID,
            location=GCP_LOCATION,
            credentials=credentials,
        )
        print(
            'SUCCESS: Verbonden met Vertex AI via Service Account'
            f' (Project: {GCP_PROJECT_ID})',
            flush=True,
        )
    else:
        ai_client = genai.Client(
            vertexai=True, project=GCP_PROJECT_ID, location=GCP_LOCATION
        )
        print('SUCCESS: Verbonden met Vertex AI (Default Auth)', flush=True)
except Exception as e:
    print(f'WARNING: Vertex AI Credentials Initialisatie mislukt: {e}', flush=True)

SYMBOLS = [
    'BTCUSDT',
    'ETHUSDT',
    'ADAUSDT',
    'AAVEUSDT',
    'TAOUSDT',
    'UNIUSDT',
    'LINKUSDT',
    'AVAXUSDT',
    'SOLUSDT',
]

last_alerted_candles = {}
ny_open_alert_sent_today = False

# GLOBALE SCHAKELAAR VOOR SCALP ALERTS (Standaard: True)
scalp_alerts_enabled = True


# ==========================================
# 3. TELEGRAM COMMAND HANDLER (LIVE INTERACTIE)
# ==========================================
def listen_telegram_commands():
    """Luistert op de achtergrond naar Telegram commando's (/scalp_off, /scalp_on, /status)"""
    global scalp_alerts_enabled
    if not TELEGRAM_BOT_TOKEN:
        return

    last_update_id = 0
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    while True:
        try:
            params = {"timeout": 20, "offset": last_update_id + 1}
            response = requests.get(url, params=params, timeout=25)
            data = response.json()

            if "result" in data:
                for update in data["result"]:
                    last_update_id = update["update_id"]
                    message = update.get("message", {})
                    text = message.get("text", "").strip()

                    if text == "/scalp_off":
                        scalp_alerts_enabled = False
                        send_telegram_message("🔴 **SCALP ALERTS UITGESCHAKELD**\n\nGemini API calls en meldingen voor Scalp Reclaim trades worden overgeslagen om tokens te besparen.")
                        print("Telegram Command Executed: Scalp Alerts DISABLED", flush=True)
                    
                    elif text == "/scalp_on":
                        scalp_alerts_enabled = True
                        send_telegram_message("🟢 **SCALP ALERTS GEACTIVERD**\n\nScalp Reclaim analyses via Vertex AI zijn weer actief.")
                        print("Telegram Command Executed: Scalp Alerts ENABLED", flush=True)

                    elif text == "/status":
                        status_str = "🟢 ACTIEF" if scalp_alerts_enabled else "🔴 UITGESCHAKELD"
                        send_telegram_message(f"🤖 **MYCRYPTOAGENT SYSTEM STATUS**\n\n• Scalp Alerts: {status_str}\n• High-Freq Engine: ACTIVE (60s loop)")

        except Exception as e:
            print(f"Telegram listener error: {e}", flush=True)
            time.sleep(5)


threading.Thread(target=listen_telegram_commands, daemon=True).start()


# ==========================================
# 4. HELPER FUNCTIES & HARD S/R ENGINE
# ==========================================
def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f'Telegram error: {e}', flush=True)


def fetch_binance_klines(symbol, interval, limit=60):
    url = f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}'
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        return [
            {
                'timestamp': c[0],
                'open': float(c[1]),
                'high': float(c[2]),
                'low': float(c[3]),
                'close': float(c[4]),
                'volume': float(c[5]),
            }
            for c in data
        ]
    except Exception as e:
        print(f'Binance fetch error {symbol}: {e}', flush=True)
        return []


def find_key_levels(candles_1d, candles_4h, candles_1h, candles_3m=None):
    levels = []
    seen_prices = set()

    def add_level(name, price, importance):
        for p in seen_prices:
            if abs(p - price) / price < 0.001:
                return
        seen_prices.add(price)
        levels.append({'name': name, 'price': price, 'importance': importance})

    if len(candles_1d) >= 2:
        add_level(
            '1D PDH (Previous Day High)', candles_1d[-2]['high'], 'CRITICAL HTF'
        )
        add_level(
            '1D PDL (Previous Day Low)', candles_1d[-2]['low'], 'CRITICAL HTF'
        )

    for i in range(2, len(candles_1d) - 2):
        if (
            candles_1d[i]['high'] > candles_1d[i - 1]['high']
            and candles_1d[i]['high'] > candles_1d[i - 2]['high']
            and candles_1d[i]['high'] > candles_1d[i + 1]['high']
            and candles_1d[i]['high'] > candles_1d[i + 2]['high']
        ):
            add_level(
                f"1D Major Resistance (${candles_1d[i]['high']})",
                candles_1d[i]['high'],
                'CRITICAL HTF',
            )

        if (
            candles_1d[i]['low'] < candles_1d[i - 1]['low']
            and candles_1d[i]['low'] < candles_1d[i - 2]['low']
            and candles_1d[i]['low'] < candles_1d[i + 1]['low']
            and candles_1d[i]['low'] < candles_1d[i + 2]['low']
        ):
            add_level(
                f"1D Major Support (${candles_1d[i]['low']})",
                candles_1d[i]['low'],
                'CRITICAL HTF',
            )

    for i in range(2, len(candles_4h) - 2):
        if (
            candles_4h[i]['high'] > candles_4h[i - 1]['high']
            and candles_4h[i]['high'] > candles_4h[i - 2]['high']
            and candles_4h[i]['high'] > candles_4h[i + 1]['high']
            and candles_4h[i]['high'] > candles_4h[i + 2]['high']
        ):
            add_level(
                f"4H Swing High (${candles_4h[i]['high']})",
                candles_4h[i]['high'],
                'HIGH HTF',
            )

        if (
            candles_4h[i]['low'] < candles_4h[i - 1]['low']
            and candles_4h[i]['low'] < candles_4h[i - 2]['low']
            and candles_4h[i]['low'] < candles_4h[i + 1]['low']
            and candles_4h[i]['low'] < candles_4h[i + 2]['low']
        ):
            add_level(
                f"4H Swing Low (${candles_4h[i]['low']})",
                candles_4h[i]['low'],
                'HIGH HTF',
            )

    for i in range(2, len(candles_1h) - 2):
        if (
            candles_1h[i]['high'] > candles_1h[i - 1]['high']
            and candles_1h[i]['high'] > candles_1h[i - 2]['high']
            and candles_1h[i]['high'] > candles_1h[i + 1]['high']
            and candles_1h[i]['high'] > candles_1h[i + 2]['high']
        ):
            add_level(
                f"1H Swing High (${candles_1h[i]['high']})",
                candles_1h[i]['high'],
                'MEDIUM Intraday',
            )

        if (
            candles_1h[i]['low'] < candles_1h[i - 1]['low']
            and candles_1h[i]['low'] < candles_1h[i - 2]['low']
            and candles_1h[i]['low'] < candles_1h[i + 1]['low']
            and candles_1h[i]['low'] < candles_1h[i + 2]['low']
        ):
            add_level(
                f"1H Swing Low (${candles_1h[i]['low']})",
                candles_1h[i]['low'],
                'MEDIUM Intraday',
            )

    if candles_3m and len(candles_3m) >= 5:
        for i in range(2, len(candles_3m) - 2):
            if (
                candles_3m[i]['high'] > candles_3m[i - 1]['high']
                and candles_3m[i]['high'] > candles_3m[i - 2]['high']
                and candles_3m[i]['high'] > candles_3m[i + 1]['high']
                and candles_3m[i]['high'] > candles_3m[i + 2]['high']
            ):
                add_level(
                    f"M3 Micro High (${candles_3m[i]['high']})",
                    candles_3m[i]['high'],
                    'LOW Scalp',
                )

            if (
                candles_3m[i]['low'] < candles_3m[i - 1]['low']
                and candles_3m[i]['low'] < candles_3m[i - 2]['low']
                and candles_3m[i]['low'] < candles_3m[i + 1]['low']
                and candles_3m[i]['low'] < candles_3m[i + 2]['low']
            ):
                add_level(
                    f"M3 Micro Low (${candles_3m[i]['low']})",
                    candles_3m[i]['low'],
                    'LOW Scalp',
                )

    return levels


def get_nearest_target(current_price, calculated_levels, direction='LONG'):
    prices = [lvl['price'] for lvl in calculated_levels]
    if direction == 'LONG':
        targets = [p for p in prices if p > current_price]
        return min(targets) if targets else current_price * 1.015
    else:
        targets = [p for p in prices if p < current_price]
        return max(targets) if targets else current_price * 0.985


def is_price_near_any_level(
    current_price,
    high_price,
    low_price,
    calculated_levels,
    max_distance_pct=2.0,
):
    for lvl in calculated_levels:
        target_price = lvl['price']
        if target_price <= 0:
            continue
        dist_close = abs(current_price - target_price) / target_price * 100
        dist_high = abs(high_price - target_price) / target_price * 100
        dist_low = abs(low_price - target_price) / target_price * 100
        if (
            dist_close <= max_distance_pct
            or dist_high <= max_distance_pct
            or dist_low <= max_distance_pct
        ):
            return True, lvl
    return False, None


def cleanup_expired_alerts():
    current_time = time.time()
    expired_keys = [
        key
        for key, timestamp in last_alerted_candles.items()
        if current_time - timestamp > 900
    ]
    for key in expired_keys:
        del last_alerted_candles[key]


def check_ny_open_warning():
    global ny_open_alert_sent_today
    tz = pytz.timezone('Europe/Amsterdam')
    now = datetime.now(tz)
    if now.hour == 0 and now.minute == 0:
        ny_open_alert_sent_today = False
    if now.hour == 15 and 20 <= now.minute <= 25 and not ny_open_alert_sent_today:
        msg = (
            '⏰ **15:20 CET/CEST WAARSCHUWING (NY OPEN OVER 10 MINUTEN)**\n\n'
            '• Wall Street opent om 15:30 CET/CEST.\n'
            '• **US Open Rule:** Geen front-run limit orders op S/R randen tussen'
            ' 15:15 en 16:30.\n'
            '• **Actie:** Wacht de eerste M15/M30 liquidity sweep/volume-spike na'
            ' 15:30 af voor entries.'
        )
        send_telegram_message(msg)
        ny_open_alert_sent_today = True


# ==========================================
# 5. AI QUANT EVALUATIE ENGINE (VERTEX AI)
# ==========================================
def evaluate_market_with_gemini(
    symbol,
    candles_1d,
    candles_4h,
    candles_1h,
    candles_15m,
    candles_5m,
    candles_3m,
    btc_context,
    calculated_levels,
):
    if not ai_client:
        print('Vertex AI client is niet geïnitialiseerd.', flush=True)
        return None

    last_closed_15m = (
        candles_15m[-2] if len(candles_15m) >= 2 else candles_15m[-1]
    )
    current_live_candle = candles_15m[-1]
    curr_price = current_live_candle['close']

    nearest_long_tp = get_nearest_target(curr_price, calculated_levels, 'LONG')
    nearest_short_tp = get_nearest_target(curr_price, calculated_levels, 'SHORT')

    min_sl_pct = 0.25 if symbol in ['BTCUSDT', 'ETHUSDT'] else 0.40

    prompt = f"""
    Je bent een meedogenloze, kwantitatieve Trading Analyst Co-Pilot gespecialiseerd in Crypto ({symbol}).
    Analyseer de live data volgens de System Instructions. BEREKEN EXPLICIT DE ADJUSTED EV (EV_adj = T * EV) ALS LEIDENDE METRIC.

    CONTEXT BTCUSDT: Price = ${btc_context['close']}, 15m Trend = {btc_context['trend']}
    HARD KEY LEVELS VOOR {symbol}: {json.dumps(calculated_levels)}

    VERPLICHTE BEREKENDE HARD TP1 TARGETS:
    - Als LONG trade: TP1 IS VERPLICHT MATEMATISCH $ {nearest_long_tp}
    - Als SHORT trade: TP1 IS VERPLICHT MATEMATISCH $ {nearest_short_tp}

    TARGET ASSET UITGEBREIDE DIEPE HISTORIE DATA ({symbol}):
    - 1D Candles (Laatste 10): {json.dumps(candles_1d[-10:])}
    - 4H Candles (Laatste 15): {json.dumps(candles_4h[-15:])}
    - 1H Candles (Laatste 20): {json.dumps(candles_1h[-20:])}
    - 15m Candles (Laatste 20): {json.dumps(candles_15m[-20:])}
    - M5 Candles (Laatste 20): {json.dumps(candles_5m[-20:])}
    - M3 Candles (Micro Reversal & Volume - Laatste 20): {json.dumps(candles_3m[-20:])}

    ALPHA TRADE SELECTION & BTC CORRELATIE LOGICA:
    - BTC ANKER LOGICA: BTCUSDT bepaalt de algemene markt-richting. Als BTC op S/R stuit en afketst, worden altcoins meegesleurd.
    - RELATIVE WEAKNESS BONUS: Als dit een altcoin is ({symbol} != BTCUSDT) en BTC geeft een Short-rejection, maar {symbol} heeft een nog zwakkere marktstructuur (gebroken 1H support) of strakkere M3 wick SL, verhoog P met +12% tot +15%.
    - ALPHA VERGELIJKING: Vermeld in het bericht expliciet of deze asset een HOGERE EV_adj levert dan BTCUSDT als ALPHA TRADE SELECTION.

    PLAYBOOK SPECIFIEKE SL / TP EXECUTION REGELS:
    1. ALS PLAYBOOK = [SCALP RECLAIM] (M3/M5 Micro Reclaim):
       - Stop Loss (SL): Strak onder/boven de M3/M5 wick (Minimaal {min_sl_pct}%).
       - TP1 Level (70% SCALE-OUT): Het EERSTVOLGENDE M15 of 1H Micro-level. Snel cashen!
       - R:R Target: TP1 vanaf 1.2R tot 2.0R is voldoende voor een GO.
    2. ALS PLAYBOOK = [DAY SWEEP] (15m/1H Sweep van PDH/PDL/Swings):
       - Stop Loss (SL): Onder/boven de 15m/1H sweep wick high/low + ademruimte.
       - TP1 Level (50% SCALE-OUT): Het eerstvolgende 1H/4H Key Level.
       - R:R Target: TP1 MOET minimaal >= 1.5R tot 3.0R bieden.
    3. ALS PLAYBOOK = [SWING BREAKOUT] (4H/Daily Retest):
       - Stop Loss (SL): Ruim ingesteld onder/boven de 4H/Daily swing structuur zone.
       - TP1 Level (30% SCALE-OUT): Het eerstvolgende Major Daily/Weekly Resistance/Support level.
       - R:R Target: TP1 MOET minimaal >= 2.0R bieden.

    STRIKTE WISKUNDIGE GUARDRAILS (HARD ENFORCED):
    1. Risico 1R = |Entry - StopLoss|.
    2. Beloning naar TP1 = |TP1 - Entry|.
    3. R:R naar TP1 = Beloning / 1R.
    4. Als R:R naar TP1 voor Optie A of B < 1.20R IS HET VERDICT AUTOMATISCH 'NO-GO'!
    5. MINIMUM SL AFSTAND: De afstand tussen Entry en SL MOET minimaal {min_sl_pct}% bedragen op deze asset ({symbol}).
    6. Formule EV_adj = T * ((P * R_gewogen) - ((1 - P) * 1R)). Reken dit MATHEMATISCH EXACT UIT zonder hallucinaties!
    7. GEEN BLINDE LIMIT ORDERS: Optie D mag alleen gekozen worden als er al een M3/M5 reversal candle IS AFGEROND!

    KWANTITATIEVE SCORING MATRIX:
    1. Trend (35%) | 2. Level Kwaliteit (30%) | 3. Displacement & Micro (20%) | 4. Session Timing (15%)
    Rating: A+ (>=85%), A (65-84%), B (<65% -> AUTOMATISCH NO-GO)

    3-TRAPS VERDICT REGELS:
    1. PRE-TRADE ALERT: Prijs/wick binnen <= 2.0% van KEY LEVEL, maar nog geen 15m close/reversal.
    2. WATCHLIST: 15m Full Body Close GEVALIDEERD, maar M3/M5 reversal nog in aanbouw.
    3. GO: 15m Full Body Close GEVALIDEERD EN M3/M5 Reversal BEVESTIGD EN Score >= 65% EN EV_adj > +0.30R EN R:R naar TP1 >= 1.2R.
    4. NO-GO: Score < 65%, onvoldoende R:R (<1.2R) naar TP1, of SL < {min_sl_pct}%.

    OUTPUT FORMAT BIJ 'NO-GO':
    **GO / NO-GO VERDICT:** **[NO-GO]** *(Rating: B | Score: X% | EV_adj: -X.XX R)*
    Korte Analyse: (Leg uit waarom de R:R onvoldoende is naar TP1, de SL te krap is (<{min_sl_pct}%), of de M5 reversal ontbreekt).

    OUTPUT FORMAT BIJ 'PRE-TRADE ALERT':
    **PRE-TRADE ALERT (KLAARZITTEN)** - {symbol}
    - **Afstand tot S/R Level:** ~X.XX% (Actuele koers: ${current_live_candle['close']} vs Key Level:$XX.XX)
    - **Verwachte S/R Zone:** $XX.XX -$XX.XX (1D / 4H / 1H Level)
    - **Verwachte Playbook:** [Swing Breakout | Day Sweep | Scalp Reclaim]
    - **Verwachte Richting:** [Long / Short]
    - **Actie:** Open je chart op M3/M5. Wacht op 15m close en M3/M5 reversal.

    OUTPUT FORMAT BIJ 'WATCHLIST' OF 'GO':
    **GO / NO-GO VERDICT:** **[GO | WATCHLIST]** *(Rating: [A+ | A] | Score: X% | MAX EV_adj: +X.XX R)*

    **ALPHA TRADE ANALYSIS ({symbol}):**
    - **BTC Context:** BTC Price = ${btc_context['close']} ({btc_context['trend']})
    - **Relative Strength/Weakness:** [Beschrijf of {symbol} zwakker/sterker is dan BTC en waarom dit extra EV geeft].

    **EXECUTION SUMMARY ({symbol} - [Long / Short]):**
    - **Playbook Type & Profile:** [Swing Breakout | Day Sweep | Scalp Reclaim]
    - **Huidige Prijs:** ${current_live_candle['close']}
    - **Aanbevolen Strategy:** **Option B (Sweet Spot)**
    - **Entry Price:** **$XX.XX**
    - **Stop Loss (SL):** **$XX.XX** *(Structurele M3/M5 wick SL)*
    - **TP1 Level:** **$XX.XX** *(Scale-out: 70% Scalp | 50% Day Sweep | 30% Swing)*
    - **TP2 Level:** **$XX.XX**
    - **Runner:** **$XX.XX**
    - **Max Adjusted EV (EV_adj):** **+X.XX R**

    ### Execution Optimization Matrix
    | Parameter | Option A (Cons.) | Option B (Sweet Spot) | Option C (Aggr. SL) | Option D (Retest Reversal - MAX EV_adj) |
    | :--- | :--- | :--- | :--- | :--- |
    | Entry Price | $XX.XX \vert{}$XX.XX | $XX.XX \vert{}$XX.XX |
    | Stop Loss (SL) | $XX.XX \vert{}$XX.XX | $XX.XX \vert{}$XX.XX |
    | Risico Afstand (1R) | $XX.XX \vert{}$XX.XX | $XX.XX \vert{}$XX.XX |
    | TP1 Level | $XX.XX \vert{}$XX.XX | $XX.XX \vert{}$XX.XX |
    | Fill Chance (T) | 85% | 65% | 40% | 85% |
    | Gewogen R:R | X.XX R | X.XX R | X.XX R | X.XX R |
    | Adjusted EV (EV_adj) | +X.XX R | +X.XX R | +X.XX R | +X.XX R (MAX) |

    **Korte Analyse:** (Max 2 zinnen met exacte reden, Daily/4H/1H niveau, BTC-correlatie en eventuele Relative Strength/Weakness Bonus).
    """

    models_to_try = [
        'gemini-1.5-pro-002',
        'gemini-1.5-flash-002',
        'publishers/google/models/gemini-1.5-pro',
    ]

    for model_name in models_to_try:
        try:
            response = ai_client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            return response.text.strip()
        except Exception as e:
            err_str = str(e)
            if 'NOT_FOUND' in err_str or '404' in err_str:
                continue
            elif '429' in err_str or 'RESOURCE_EXHAUSTED' in err_str:
                time.sleep(2)
            else:
                print(f'Vertex AI Error ({model_name}) voor {symbol}: {e}', flush=True)
                return None
    return None


# ==========================================
# 6. MAIN SCANNER LOOP (HIGH FREQUENCY)
# ==========================================
def run_scanner():
    tz = pytz.timezone('Europe/Amsterdam')
    now_str = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    print(
        f'\n[{now_str}] 🔍 Markt-scan gestart voor alle 9 symbolen...',
        flush=True,
    )

    cleanup_expired_alerts()
    check_ny_open_warning()

    btc_15m = fetch_binance_klines('BTCUSDT', '15m', limit=10)
    if not btc_15m:
        print('Geen BTC data ontvangen, scan overgeslagen.', flush=True)
        return

    btc_context = {
        'close': btc_15m[-1]['close'],
        'trend': (
            'BULLISH'
            if btc_15m[-1]['close'] >= btc_15m[-2]['close']
            else 'BEARISH'
        ),
    }

    for symbol in SYMBOLS:
        try:
            candles_1d = fetch_binance_klines(symbol, '1d', limit=15)
            candles_4h = fetch_binance_klines(symbol, '4h', limit=25)
            candles_1h = fetch_binance_klines(symbol, '1h', limit=35)
            candles_15m = fetch_binance_klines(symbol, '15m', limit=35)
            candles_5m = fetch_binance_klines(symbol, '5m', limit=35)
            candles_3m = fetch_binance_klines(symbol, '3m', limit=35)

            if (
                not candles_1d
                or not candles_4h
                or not candles_1h
                or not candles_15m
                or not candles_5m
                or not candles_3m
            ):
                continue

            calculated_levels = find_key_levels(
                candles_1d, candles_4h, candles_1h, candles_3m
            )

            curr_close = candles_15m[-1]['close']
            curr_high = candles_15m[-1]['high']
            curr_low = candles_15m[-1]['low']

            is_near, matched_level = is_price_near_any_level(
                curr_close,
                curr_high,
                curr_low,
                calculated_levels,
                max_distance_pct=2.0,
            )

            if not is_near:
                print(
                    f'[{now_str}] [{symbol}] Scan voltooid -> NO-GO / Prijs > 2.0% van'
                    ' S/R levels.',
                    flush=True,
                )
                time.sleep(0.2)
                continue

            # 🛡️ PYTHON SCALP FILTER (BESPAART API TOKENS ALS SCALPS UIT STAAN)
            is_scalp_level = matched_level and ('M3 Micro' in matched_level['name'] or matched_level.get('importance') == 'LOW Scalp')
            if not scalp_alerts_enabled and is_scalp_level:
                print(
                    f'[{now_str}] [{symbol}] SKIPPED -> Scalp level gedetecteerd, maar Scalp Alerts staan UIT (/scalp_off). API Call bespaard!',
                    flush=True,
                )
                time.sleep(0.2)
                continue

            # Python Hard Pre-Check voor TP1 ruimte
            direction = 'LONG' if curr_close >= matched_level['price'] else 'SHORT'
            nearest_tp = get_nearest_target(curr_close, calculated_levels, direction)
            reward_pct = abs(nearest_tp - curr_close) / curr_close * 100

            if reward_pct < 0.40:
                print(
                    f'[{now_str}] [{symbol}] SKIPPED -> TP1 te dichtbij'
                    f' ({reward_pct:.2f}% < 0.40%). R:R < 1.20R gegarandeerd.',
                    flush=True,
                )
                time.sleep(0.2)
                continue

            print(
                f'[{now_str}] 🎯 [{symbol}] NABIJ S/R LEVEL ({matched_level["name"]})'
                ' -> Vertex AI Inschakelen...',
                flush=True,
            )

            last_closed_candle_time = candles_15m[-2]['timestamp']

            analysis = evaluate_market_with_gemini(
                symbol,
                candles_1d,
                candles_4h,
                candles_1h,
                candles_15m,
                candles_5m,
                candles_3m,
                btc_context,
                calculated_levels,
            )

            is_go_alert = analysis and (
                '🚨 **GO**' in analysis or 'GO / NO-GO VERDICT: **GO**' in analysis
            )
            alert_key = (
                f'{symbol}_{last_closed_candle_time}'
                if not is_go_alert
                else f'{symbol}_{last_closed_candle_time}_GO'
            )

            if analysis and (
                'PRE-TRADE ALERT' in analysis
                or '🚨 **GO**' in analysis
                or 'WATCHLIST' in analysis
                or 'GO / NO-GO VERDICT' in analysis
            ):
                if alert_key in last_alerted_candles:
                    print(
                        f'[{symbol}] Reeds gemeld binnen de afgelopen 15 minuten.',
                        flush=True,
                    )
                    continue

                print(
                    f'[{now_str}] 🚨 ALERT GEGENEREERD EN VERSTUURD VOOR {symbol}!',
                    flush=True,
                )
                send_telegram_message(analysis)
                last_alerted_candles[alert_key] = time.time()
            else:
                print(
                    f'[{now_str}] [{symbol}] Scan voltooid -> NO-GO / Geen valide S/R'
                    ' setup.',
                    flush=True,
                )

            time.sleep(0.5)
        except Exception as e:
            print(f'Error bij verwerken {symbol}: {e}', flush=True)


if __name__ == '__main__':
    startup_msg = (
        '🤖 **MyCryptoAgent Master Service IS LIVE ON VERTEX AI!**\n\n'
        '**Geïntegreerd Quantitative System Instructions:**\n'
        '1. ⚠️ **Pre-Trade Alert:** Prijs binnen <= 2.0% van 1D/4H/1H Key Level\n'
        '2. 👁️ **Watchlist:** 15m Full Body Close (Wick <= 30%) op Key Level\n'
        '3. 🚨 **GO Execution:** M3/M5 Reversal + EV_adj > +0.30R & Score >= 65%\n\n'
        '• **Interactive Scalp Toggle:** Gebruik `/scalp_off` en `/scalp_on` in Telegram om Scalp AI-calls live te pauzeren en tokens te besparen.\n'
        '• **System Status Check:** Stuur `/status` in Telegram voor live schakelaar-status.'
    )
    send_telegram_message(startup_msg)

    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f'Loop error: {e}', flush=True)

        time.sleep(60)
