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
# 1. MINI FLASK WEBSERVER (Render Keep-Alive)
# ==========================================
app = Flask(__name__)

# GLOBALE SCHAKELAAR VOOR SCALP ALERTS (Standaard: True)
scalp_alerts_enabled = True


@app.route('/')
def health_check():
    status = 'AAN' if scalp_alerts_enabled else 'UIT'
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


# ==========================================
# 3. TELEGRAM COMMAND HANDLER
# ==========================================
def listen_telegram_commands():
    """Luistert op de achtergrond naar Telegram commando's (/scalp_off, /scalp_on, /status)"""
    global scalp_alerts_enabled
    if not TELEGRAM_BOT_TOKEN:
        return

    last_update_id = 0
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates'

    while True:
        try:
            params = {'timeout': 20, 'offset': last_update_id + 1}
            response = requests.get(url, params=params, timeout=25)
            data = response.json()

            if 'result' in data:
                for update in data['result']:
                    last_update_id = update['update_id']
                    message = update.get('message', {})
                    text = message.get('text', '').strip()

                    if text == '/scalp_off':
                        scalp_alerts_enabled = False
                        send_telegram_message(
                            '🔴 **SCALP ALERTS UITGESCHAKELD**\n\nM1 fetches en M3'
                            ' micro-pivots gepauzeerd om tokens te besparen.'
                        )
                        print('Telegram Command: Scalp Alerts DISABLED', flush=True)

                    elif text == '/scalp_on':
                        scalp_alerts_enabled = True
                        send_telegram_message(
                            '🟢 **SCALP ALERTS GEACTIVERD**\n\nMicro M1/M3 analyses weer'
                            ' actief via Vertex AI.'
                        )
                        print('Telegram Command: Scalp Alerts ENABLED', flush=True)

                    elif text == '/status':
                        status_str = (
                            '🟢 ACTIEF' if scalp_alerts_enabled else '🔴 UITGESCHAKELD'
                        )
                        send_telegram_message(
                            f'🤖 **MYCRYPTOAGENT SYSTEM STATUS**\n\n• Scalp Mode: {status_str}\n• High-Freq Engine: ACTIVE (60s loop)'
                        )

        except Exception as e:
            print(f'Telegram listener error: {e}', flush=True)
            time.sleep(5)


threading.Thread(target=listen_telegram_commands, daemon=True).start()


# ==========================================
# 4. HELPER FUNCTIES & SMART CONFLUENCE S/R ENGINE
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


def find_key_levels(
    candles_1d, candles_4h, candles_1h, candles_3m=None, include_scalp=True
):
    levels = []

    def add_level(name, price, importance):
        for lvl in levels:
            if abs(lvl['price'] - price) / price < 0.002:
                if 'CRITICAL' in lvl['importance'] or 'HIGH' in lvl['importance']:
                    lvl['name'] += f' + Confluence ({name})'
                    return
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

    if include_scalp and candles_3m and len(candles_3m) >= 5:
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


def is_price_near_any_htf_level(
    current_price, high_price, low_price, calculated_levels
):
    """Checkt of de prijs (Close, High of Low Wick) binnen 2.0% van een HTF S/R niveau staat."""
    for lvl in calculated_levels:
        if lvl.get('importance') in [
            'CRITICAL HTF',
            'HIGH HTF',
            'MEDIUM Intraday',
        ]:
            target_price = lvl['price']
            dist_close = abs(current_price - target_price) / target_price * 100
            dist_high = abs(high_price - target_price) / target_price * 100
            dist_low = abs(low_price - target_price) / target_price * 100

            if dist_close <= 2.0 or dist_high <= 2.0 or dist_low <= 2.0:
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
    candles_1m,
    btc_context,
    calculated_levels,
):
    if not ai_client:
        return None

    current_live_candle = candles_15m[-1]
    curr_price = current_live_candle['close']

    nearest_long_tp = get_nearest_target(curr_price, calculated_levels, 'LONG')
    nearest_short_tp = get_nearest_target(curr_price, calculated_levels, 'SHORT')
    min_sl_pct = 0.25 if symbol in ['BTCUSDT', 'ETHUSDT'] else 0.40

    m1_prompt_block = (
        "- M1 Candles (Ultra-Micro Structuur & Volume Spikes - Laatste 20): " + json.dumps(candles_1m[-20:]) + "\n"
        if candles_1m
        else "- M1 Candles: NIET ACTIEF (Scalp Mode is UIT)\n"
    )

    prompt = (
        "SYSTEM INSTRUCTIONS: QUANTITATIVE CRYPTO TRADING CO-PILOT (" + str(symbol) + ")\n"
        "1. ROL & HOOFDDOEL: Je bent een meedogenloze, kwantitatieve Trading Analyst Co-Pilot gespecialiseerd in Crypto. Je adviseert op basis van pure statistische Expected Value (+EV), Adjusted Expected Value (EV_adj = T * EV), waarschijnlijkheidsverdelingen, Order Fill Chance (T) en strikt risicobeheer. Er is geen ruimte voor emotionele ruis of vage voorspellingen.\n\n"
        "CONTEXT BTCUSDT: Price = $" + str(btc_context['close']) + ", 15m Trend = " + str(btc_context['trend']) + "\n"
        "HARD KEY LEVELS VOOR " + str(symbol) + ": " + json.dumps(calculated_levels) + "\n\n"
        "VERPLICHTE BEREKENDE HARD TP1 TARGETS:\n"
        "- Als LONG trade: TP1 IS VERPLICHT MATEMATISCH $ " + str(nearest_long_tp) + "\n"
        "- Als SHORT trade: TP1 IS VERPLICHT MATEMATISCH $ " + str(nearest_short_tp) + "\n\n"
        "TARGET ASSET HISTORIE DATA (" + str(symbol) + "):\n"
        "- 1D Candles (Laatste 10): " + json.dumps(candles_1d[-10:]) + "\n"
        "- 4H Candles (Laatste 15): " + json.dumps(candles_4h[-15:]) + "\n"
        "- 1H Candles (Laatste 20): " + json.dumps(candles_1h[-20:]) + "\n"
        "- 15m Candles (Laatste 20): " + json.dumps(candles_15m[-20:]) + "\n"
        "- M5 Candles (Laatste 20): " + json.dumps(candles_5m[-20:]) + "\n"
        "- M3 Candles (Micro Reversal & Volume - Laatste 20): " + json.dumps(candles_3m[-20:]) + "\n"
        + m1_prompt_block + "\n"
        "PLAYBOOK SPECIFIEKE SL / TP EXECUTION REGELS:\n"
        "1. ALS PLAYBOOK = [SCALP RECLAIM] (M3/M5 Micro Reclaim):\n"
        "   - Stop Loss (SL): Strak onder/boven M3/M5 wick (Minimaal " + str(min_sl_pct) + "%).\n"
        "   - TP1 Level (70% SCALE-OUT): Eerstvolgende M15 of 1H Micro-level.\n"
        "   - R:R Target: TP1 vanaf 1.2R tot 2.0R is voldoende voor een GO.\n"
        "   - ENTRY TRIGGER: Voor Scalp Reclaims is een 15m close OPTIONEEL. Een M3 Close met een bevestigde M1/M3 Reversal Candle (volume >= 1.5x SMA 9) is VOLDOENDE voor een GO!\n"
        "2. ALS PLAYBOOK = [DAY SWEEP] (15m/1H Sweep van PDH/PDL/Swings):\n"
        "   - Stop Loss (SL): Onder/boven 15m/1H sweep wick high/low.\n"
        "   - TP1 Level (50% SCALE-OUT): Eerstvolgende 1H/4H Key Level.\n"
        "   - R:R Target: TP1 MOET minimaal >= 1.5R tot 3.0R bieden.\n"
        "   - ENTRY TRIGGER: 15m Full Body Close (wick <= 30%) VERPLICHT.\n"
        "3. ALS PLAYBOOK = [SWING BREAKOUT] (4H/Daily Retest):\n"
        "   - Stop Loss (SL): Ruim onder/boven 4H/Daily swing structuur zone.\n"
        "   - TP1 Level (30% SCALE-OUT): Major Daily/Weekly Resistance/Support.\n"
        "   - R:R Target: TP1 MOET minimaal >= 2.0R bieden.\n"
        "   - ENTRY TRIGGER: 15m/1H Full Body Close VERPLICHT.\n\n"
        "STRIKTE WISKUNDIGE GUARDRAILS (HARD ENFORCED):\n"
        "1. Risico 1R = |Entry - StopLoss|.\n"
        "2. Beloning naar TP1 = |TP1 - Entry|.\n"
        "3. R:R naar TP1 = Beloning / 1R.\n"
        "4. Als R:R naar TP1 voor Optie A of B < 1.20R IS HET VERDICT AUTOMATISCH 'NO-GO'!\n"
        "5. MINIMUM SL AFSTAND: De afstand tussen Entry en SL MOET minimaal " + str(min_sl_pct) + "% bedragen op deze asset.\n"
        "6. Formule EV_adj = T * ((P * R_gewogen) - ((1 - P) * 1R)). Reken dit MATHEMATISCH EXACT UIT!\n"
        "7. GEEN BLINDE LIMIT ORDERS: Optie D mag alleen gekozen worden als er al een M3/M5 reversal candle IS AFGEROND!\n\n"
        "KWANTITATIEVE SCORING MATRIX:\n"
        "1. Trend (35%) | 2. Level Kwaliteit (30%) | 3. Displacement & Micro (20%) | 4. Session Timing (15%)\n"
        "Rating: A+ (>=85%), A (65-84%), B (<65% -> AUTOMATISCH NO-GO)\n\n"
        "3-TRAPS VERDICT REGELS:\n"
        "1. PRE-TRADE ALERT: Prijs/wick binnen <= 2.0% van KEY LEVEL, maar nog geen reversal.\n"
        "2. WATCHLIST: Key Level geraakt, 15m close (Day Sweep/Swing) of M3 Opbouw (Scalp) gevalideerd, maar M3/M5 reversal nog in aanbouw.\n"
        "3. GO: (a) Voor Day Sweep/Swing: 15m Full Body Close + M3/M5 Reversal BEVESTIGD. (b) Voor Scalp Reclaim: M3 Close + M1/M3 Reversal BEVESTIGD. Beiden eisen Score >= 65%, EV_adj > +0.30R en R:R naar TP1 >= 1.2R.\n"
        "4. NO-GO: Score < 65%, onvoldoende R:R (<1.2R) naar TP1, of SL < " + str(min_sl_pct) + "%.\n\n"
        "OUTPUT FORMAT BIJ 'NO-GO':\n"
        "**GO / NO-GO VERDICT:** **[NO-GO]** *(Rating: B | Score: X% | EV_adj: -X.XX R)*\n"
        "Korte Analyse: (Leg uit waarom de R:R onvoldoende is naar TP1, de SL te krap is (<" + str(min_sl_pct) + "%), of de M3/M5 reversal ontbreekt).\n\n"
        "OUTPUT FORMAT BIJ 'PRE-TRADE ALERT':\n"
        "**PRE-TRADE ALERT (KLAARZITTEN)** - " + str(symbol) + "\n"
        "- **Afstand tot S/R Level:** ~X.XX% (Actuele koers: $" + str(current_live_candle['close']) + " vs Key Level: $XX.XX)\n"
        "- **Verwachte S/R Zone:** $XX.XX -$XX.XX (1D / 4H / 1H Level)\n"
        "- **Verwachte Playbook:** [Swing Breakout | Day Sweep | Scalp Reclaim]\n"
        "- **Verwachte Richting:** [Long / Short]\n"
        "- **Actie:** Open je chart op M3/M5. Wacht op M3/M5 reversal.\n\n"
        "OUTPUT FORMAT BIJ 'WATCHLIST' OF 'GO':\n"
        "**GO / NO-GO VERDICT:** **[GO | WATCHLIST]** *(Rating: [A+ | A] | Score: X% | MAX EV_adj: +X.XX R)*\n\n"
        "**ALPHA TRADE ANALYSIS (" + str(symbol) + "):**\n"
        "- **BTC Context:** BTC Price = $" + str(btc_context['close']) + " (" + str(btc_context['trend']) + ")\n"
        "- **Relative Strength/Weakness:** [Beschrijf of " + str(symbol) + " zwakker/sterker is dan BTC en waarom dit extra EV geeft].\n\n"
        "**EXECUTION SUMMARY (" + str(symbol) + " - [Long / Short]):**\n"
        "- **Playbook Type & Profile:** [Swing Breakout | Day Sweep | Scalp Reclaim]\n"
        "- **Huidige Prijs:** $" + str(current_live_candle['close']) + "\n"
        "- **Aanbevolen Strategy:** **Option B (Sweet Spot)**\n"
        "- **Entry Price:** **$XX.XX**\n"
        "- **Stop Loss (SL):** **$XX.XX** *(Structurele M3/M5 wick SL)*\n"
        "- **TP1 Level:** **$XX.XX** *(Scale-out: 70% Scalp | 50% Day Sweep | 30% Swing)*\n"
        "- **TP2 Level:** **$XX.XX**\n"
        "- **Runner:** **$XX.XX**\n"
        "- **Max Adjusted EV (EV_adj):** **+X.XX R**\n\n"
        "### Execution Optimization Matrix\n"
        "| Parameter | Option A (Cons.) | Option B (Sweet Spot) | Option C (Aggr. SL) | Option D (Retest Reversal - MAX EV_adj) |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        "| Entry Price | $XX.XX \vert{}$XX.XX | $XX.XX \vert{}$XX.XX |\n"
        "| Stop Loss (SL) | $XX.XX \vert{}$XX.XX | $XX.XX \vert{}$XX.XX |\n"
        "| Risico Afstand (1R) | $XX.XX \vert{}$XX.XX | $XX.XX \vert{}$XX.XX |\n"
        "| TP1 Level | $XX.XX \vert{}$XX.XX | $XX.XX \vert{}$XX.XX |\n"
        "| Fill Chance (T) | 85% | 65% | 40% | 85% |\n"
        "| Gewogen R:R | X.XX R | X.XX R | X.XX R | X.XX R |\n"
        "| Adjusted EV (EV_adj) | +X.XX R | +X.XX R | +X.XX R | +X.XX R (MAX) |\n\n"
        "**Korte Analyse:** (Max 2 zinnen met exacte reden, Daily/4H/1H niveau, BTC-correlatie en eventuele Relative Strength/Weakness Bonus)."
    )

    models_to_try = [
        'gemini-1.5-pro-002',
        'gemini-1.5-flash-002',
        'publishers/google/models/gemini-1.5-pro',
    ]

    for model_name in models_to_try:
        try:
            response = ai_client.models.generate_content(
                model=model_name, contents=prompt
            )
            return response.text.strip()
        except Exception:
            continue
    return None


# ==========================================
# 6. MAIN SCANNER LOOP (HIGH FREQUENCY & GUARANTEED PRE-TRADE ALERTS)
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

            # DYNAMISCH M1 KLINES OPHALEN ENKEL ALS /scalp_on IS
            candles_1m = (
                fetch_binance_klines(symbol, '1m', limit=30)
                if scalp_alerts_enabled
                else []
            )

            if not (
                candles_1d
                and candles_4h
                and candles_1h
                and candles_15m
                and candles_5m
                and candles_3m
            ):
                continue

            # Slimme Level Detectie met Confluence samenvoeging
            calculated_levels = find_key_levels(
                candles_1d,
                candles_4h,
                candles_1h,
                candles_3m,
                include_scalp=scalp_alerts_enabled,
            )

            curr_close = candles_15m[-1]['close']
            curr_high = candles_15m[-1]['high']
            curr_low = candles_15m[-1]['low']

            # 🎯 1. PRE-TRADE HTF CHECK (Binnen 2.0% van 1D/4H/1H Level?)
            is_near_htf, matched_level = is_price_near_any_htf_level(
                curr_close, curr_high, curr_low, calculated_levels
            )

            if not is_near_htf:
                print(
                    f'[{now_str}] [{symbol}] Geen HTF S/R nabij (> 2.0%). Scan'
                    ' afgerond.',
                    flush=True,
                )
                time.sleep(0.2)
                continue

            # 🛡️ DYNAMISCHE MICRO-FILTER (Voorkomt dat Pre-Trade Alerts op HTF niveaus worden geblokkeerd)
            is_pure_scalp = matched_level.get('importance') == 'LOW Scalp'
            direction = 'LONG' if curr_close >= matched_level['price'] else 'SHORT'
            nearest_tp = get_nearest_target(curr_close, calculated_levels, direction)
            reward_pct = abs(nearest_tp - curr_close) / curr_close * 100

            # Alleen skippen als het een puur M3 scalp-niveau is zónder HTF-waarde én met te weinig ruimte (<0.20%)
            if is_pure_scalp and reward_pct < 0.20:
                print(
                    f'[{now_str}] [{symbol}] SKIPPED -> Pure micro-scalp TP1 te'
                    f' dichtbij ({reward_pct:.2f}% < 0.20%).',
                    flush=True,
                )
                time.sleep(0.2)
                continue

            print(
                f'[{now_str}] 🎯 [{symbol}] NABIJ HTF LEVEL ({matched_level["name"]})'
                ' -> Vertex AI Inschakelen...',
                flush=True,
            )

            last_closed_candle_time = candles_15m[-2]['timestamp']

            # Gemini AI Oproepen voor Pre-Trade Alert / Watchlist / GO Evaluatie
            analysis = evaluate_market_with_gemini(
                symbol,
                candles_1d,
                candles_4h,
                candles_1h,
                candles_15m,
                candles_5m,
                candles_3m,
                candles_1m,
                btc_context,
                calculated_levels,
            )

            # BUG CONSOLE DEBUG PRINT: Print altijd de eerste regel van Vertex AI op Render!
            if analysis:
                first_line = analysis.split('\n')[0] if analysis else 'EMPTY'
                print(
                    f'[{now_str}] 🤖 [VERTEX RESPONSE {symbol}]: {first_line}',
                    flush=True,
                )

            if analysis:
                is_go_alert = (
                    '🚨 **GO**' in analysis or 'GO / NO-GO VERDICT: **GO**' in analysis
                )
                alert_key = (
                    f'{symbol}_{last_closed_candle_time}'
                    if not is_go_alert
                    else f'{symbol}_{last_closed_candle_time}_GO'
                )

                # Cooldown check om dubbele meldingen binnen dezelfde 15m te voorkomen
                if alert_key in last_alerted_candles:
                    print(
                        f'[{symbol}] Reeds gemeld binnen deze 15 minuten.',
                        flush=True,
                    )
                    continue

                # SOEPELE FILTER: Pre-Trade, Watchlist & GO direct naar Telegram sturen!
                is_valid_alert = any(
                    keyword in analysis
                    for keyword in [
                        'PRE-TRADE',
                        'WATCHLIST',
                        '🚨 **GO**',
                        'PRE-TRADE ALERT',
                    ]
                )

                if is_valid_alert:
                    send_telegram_message(analysis)
                    last_alerted_candles[alert_key] = time.time()
                    print(
                        f'[{now_str}] 🚨 ALERT VERSTUURD VOOR {symbol} NAAR TELEGRAM!',
                        flush=True,
                    )
                else:
                    print(
                        f'[{now_str}] [{symbol}] AI Verdict is NO-GO of afwijkend'
                        ' format. Geen Telegram-bericht.',
                        flush=True,
                    )

            time.sleep(0.5)
        except Exception as e:
            print(f'Error bij verwerken {symbol}: {e}', flush=True)


if __name__ == '__main__':
    startup_msg = (
        '🤖 **MyCryptoAgent Master Service IS LIVE ON VERTEX AI!**\n\n'
        '**Geïntegreerd Quantitative System Instructions:**\n'
        '1. ⚠️ **Pre-Trade Alert:** Prijs binnen <= 2.0% van HTF Key Level\n'
        '2. 👁️ **Watchlist:** 15m Full Body Close op Key Level (Day Sweep) / M3'
        ' Opbouw (Scalp)\n'
        '3. 🚨 **GO Execution:** M3/M5 Reversal + EV_adj > +0.30R & Score >='
        ' 65%\n\n'
        '• **Interactive Scalp Toggle:** Gebruik `/scalp_off` en `/scalp_on` in'
        ' Telegram.\n'
        '• **System Status Check:** Stuur `/status` in Telegram.'
    )
    send_telegram_message(startup_msg)

    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f'Loop error: {e}', flush=True)
        time.sleep(60)
