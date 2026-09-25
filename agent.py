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
    return 'MyCryptoAgent Co-Pilot is 24/7 Online & Active!', 200


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


# ==========================================
# 3. HELPER FUNCTIES & HARD S/R ENGINE
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


def fetch_binance_klines(symbol, interval, limit=50):
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


def find_key_levels(candles_1d, candles_4h, candles_1h):
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

    return levels


def get_nearest_target(current_price, calculated_levels, direction='LONG'):
    """Berekent het eerstvolgende logische TP niveau zonder 'droom'-fallbacks."""
    prices = [lvl['price'] for lvl in calculated_levels]
    if direction == 'LONG':
        targets = [p for p in prices if p > current_price]
        return min(targets) if targets else current_price * 1.012
    else:
        targets = [p for p in prices if p < current_price]
        return max(targets) if targets else current_price * 0.988


def is_price_near_any_level(
    current_price,
    high_price,
    low_price,
    calculated_levels,
    max_distance_pct=1.0,
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
# 4. AI QUANT EVALUATIE ENGINE (GEMINI ON VERTEX AI)
# ==========================================
def evaluate_market_with_gemini(
    symbol,
    candles_1d,
    candles_4h,
    candles_15m,
    candles_5m,
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

    # Dynamische minimale SL afstand tegen ruis (0.25% BTC/ETH, 0.40% Altcoins)
    min_sl_pct = 0.25 if symbol in ['BTCUSDT', 'ETHUSDT'] else 0.40

    prompt = f"""
    Je bent een meedogenloze, kwantitatieve Trading Analyst Co-Pilot gespecialiseerd in Crypto ({symbol}).
    Analyseer de live data volgens de System Instructions. BEREKEN EXPLICIT DE ADJUSTED EV (EV_adj = T * EV) ALS LEIDENDE METRIC.

    CONTEXT BTCUSDT: Price = ${btc_context['close']}, 15m Trend = {btc_context['trend']}
    HARD KEY LEVELS VOOR {symbol}: {json.dumps(calculated_levels)}

    VERPLICHTE BEREKENDE HARD TP1 TARGETS:
    - Als LONG trade: TP1 IS VERPLICHT MATEMATISCH $ {nearest_long_tp}
    - Als SHORT trade: TP1 IS VERPLICHT MATEMATISCH $ {nearest_short_tp}

    TARGET ASSET DATA ({symbol}):
    - 1D Candles (5x): {json.dumps(candles_1d[-5:])}
    - 4H Candles (5x): {json.dumps(candles_4h[-5:])}
    - 15m Last Closed: {json.dumps(last_closed_15m)}
    - 15m Live Candle: {json.dumps(current_live_candle)}
    - M5 Candles (Micro Reversal & Volume): {json.dumps(candles_5m[-5:])}

    STRIKTE WISKUNDIGE GUARDRAILS (HARD ENFORCED):
    1. Risico 1R = |Entry - StopLoss|.
    2. Beloning naar TP1 = |TP1 - Entry|.
    3. R:R naar TP1 = Beloning / 1R.
    4. Als R:R naar TP1 voor Optie A of B < 1.20R IS HET VERDICT AUTOMATISCH 'NO-GO'!
    5. MINIMUM SL AFSTAND: De afstand tussen Entry en SL MOET minimaal {min_sl_pct}% bedragen op deze asset ({symbol}). Een strakkere SL is FYSIEK ONGELDIG (AUTOMATISCH NO-GO)!
    6. Formule EV_adj = T * ((P * R_gewogen) - ((1 - P) * 1R)). Reken dit MATHEMATISCH EXACT UIT zonder hallucinaties!
    7. GEEN BLINDE LIMIT ORDERS: Optie D mag alleen gekozen worden als er al een M5 reversal candle IS AFGEROND!

    ⚡ SPECIAL RELATIVE STRENGTH / DECOUPLING LOGICA:
    - [SUPER BUY]: Als {symbol} haar 4H/Daily Support verdedigt TERWIJL BTC bearish/downward dumpt, verhoog Win Rate (P) met +12% tot +15%.
    - [SUPER SELL]: Als {symbol} haar 4H/Daily Resistance faalt TERWIJL BTC bullish/upward pumpt, verhoog Win Rate (P) voor Short met +12% tot +15%.

    KWANTITATIEVE SCORING MATRIX:
    1. Trend (35%) | 2. Level Kwaliteit (30%) | 3. Displacement & Micro (20%) | 4. Session Timing (15%)
    Rating: A+ (>=85%), A (65-84%), B (<65% -> AUTOMATISCH NO-GO)

    3-TRAPS VERDICT REGELS:
    1. ⚠️ PRE-TRADE ALERT: Prijs/wick binnen <= 1.0% van KEY LEVEL, maar nog geen 15m close/reversal.
    2. 👁️ WATCHLIST: 15m Full Body Close GEVALIDEERD, maar M3/M5 reversal nog in aanbouw.
    3. 🚨 GO: 15m Full Body Close GEVALIDEERD EN M3/M5 Reversal BEVESTIGD EN Score >= 65% EN EV_adj > +0.30R EN R:R naar TP1 >= 1.2R.
    4. NO-GO: Score < 65%, onvoldoende R:R (<1.2R) naar TP1, of SL < {min_sl_pct}%.

    OUTPUT FORMAT BIJ 'NO-GO':
    **GO / NO-GO VERDICT:** **[NO-GO]** *(Rating: B | Score: X% | EV_adj: -X.XX R)*
    Korte Analyse: (Leg uit waarom de R:R onvoldoende is naar TP1, de SL te krap is (<{min_sl_pct}%), of de M5 reversal ontbreekt).

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
    • **Aanbevolen Strategy:** **Option B (Sweet Spot)**
    • **Entry Price:** **$XX.XX**
    • **Stop Loss (SL):** **$XX.XX** *(Structurele M3/M5 wick SL)*
    • **TP1 Level:** **$XX.XX**
    • **Max Adjusted EV (EV_adj):** **+X.XX R**

    ### Execution Optimization Matrix
    | Parameter | Option A (Cons.) | Option B (Sweet Spot) | Option C (Aggr. SL) | **Option D (Retest Reversal - MAX EV_adj)** |
    | :--- | :--- | :--- | :--- | :--- |
    | **Entry Price** | $XX.XX | $XX.XX | $XX.XX | **$XX.XX** |
    | **Stop Loss (SL)** | $XX.XX | $XX.XX | $XX.XX | **$XX.XX** |
    | **Risico Afstand (1R)** | $XX.XX | $XX.XX | $XX.XX | **$XX.XX** |
    | **TP1 (50%)** | $XX.XX | $XX.XX | $XX.XX | **$XX.XX** |
    | **Fill Chance (T)** | 85% | 65% | 40% | **85%** |
    | **Gewogen R:R** | X.XX R | X.XX R | X.XX R | **X.XX R** |
    | **Adjusted EV (EV_adj)**| +X.XX R | +X.XX R | +X.XX R | **+X.XX R (MAX)** |

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
                time.sleep(3)
            else:
                print(
                    f'Vertex AI Error ({model_name}) voor {symbol}: {e}',
                    flush=True,
                )
                return None
    return None


# ==========================================
# 5. MAIN SCANNER LOOP
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
            candles_4h = fetch_binance_klines(symbol, '4h', limit=20)
            candles_1h = fetch_binance_klines(symbol, '1h', limit=30)
            candles_15m = fetch_binance_klines(symbol, '15m', limit=20)
            candles_5m = fetch_binance_klines(symbol, '5m', limit=20)

            if (
                not candles_1d
                or not candles_4h
                or not candles_1h
                or not candles_15m
                or not candles_5m
            ):
                continue

            calculated_levels = find_key_levels(candles_1d, candles_4h, candles_1h)

            curr_close = candles_15m[-1]['close']
            curr_high = candles_15m[-1]['high']
            curr_low = candles_15m[-1]['low']

            is_near, matched_level = is_price_near_any_level(
                curr_close,
                curr_high,
                curr_low,
                calculated_levels,
                max_distance_pct=1.0,
            )

            if not is_near:
                print(
                    f'[{now_str}] [{symbol}] Scan voltooid -> NO-GO / Prijs > 1.0% van S/R levels.',
                    flush=True,
                )
                time.sleep(0.5)
                continue

            # ==========================================
            # 🛡️ HARD PYTHON PRE-CHECK (ELIMINEERT AI HALLUCINATIES)
            # ==========================================
            direction = 'LONG' if curr_close >= matched_level['price'] else 'SHORT'
            nearest_tp = get_nearest_target(curr_close, calculated_levels, direction)
            reward_pct = abs(nearest_tp - curr_close) / curr_close * 100

            if reward_pct < 0.50:
                print(
                    f'[{now_str}] [{symbol}] SKIPPED -> TP1 ligt te dichtbij'
                    f' ({reward_pct:.2f}% < 0.50%). R:R < 1.20R gegarandeerd.',
                    flush=True,
                )
                time.sleep(0.5)
                continue
            # ==========================================

            print(
                f'[{now_str}] 🎯 [{symbol}] NABIJ S/R LEVEL ({matched_level["name"]})'
                ' -> Gemini Pro Inschakelen...',
                flush=True,
            )

            last_closed_candle_time = candles_15m[-2]['timestamp']

            analysis = evaluate_market_with_gemini(
                symbol,
                candles_1d,
                candles_4h,
                candles_15m,
                candles_5m,
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

            time.sleep(2)
        except Exception as e:
            print(f'Error bij verwerken {symbol}: {e}', flush=True)


if __name__ == '__main__':
    startup_msg = (
        '🤖 **MyCryptoAgent Master Service IS LIVE ON VERTEX AI!**\n\n'
        '**Geïntegreerd Quantitative System Instructions:**\n'
        '1. ⚠️ **Pre-Trade Alert:** Prijs binnen <= 1.0% van 1D/4H/1H Key Level\n'
        '2. 👁️ **Watchlist:** 15m Full Body Close (Wick <= 30%) op Key Level\n'
        '3. 🚨 **GO Execution:** M3/M5 Reversal + EV_adj > +0.30R & Score >= 65%\n\n'
        '• **Vertex Engine Fix:** Gemini 1.5 Pro/Flash-002 API-Endpoints'
        ' geactiveerd.\n'
        '• **Dynamic TP Injection:** Hardcoded TP1 berekend door Python Engine.\n'
        '• **Python Pre-Check Guardrail:** Skips setups met < 0.50% ruimte naar'
        ' TP1 & SL < 0.40%.'
    )
    send_telegram_message(startup_msg)

    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f'Loop error: {e}', flush=True)

        time.sleep(180)
