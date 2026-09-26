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
    """Checkt of de prijs (Close, High of Low Wick) binnen 0.5% van een HTF S/R niveau staat."""
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

            if dist_close <= 0.5 or dist_high <= 0.5 or dist_low <= 0.5:
                return True, lvl
    return False, None


def cleanup_expired_alerts():
    current_time = time.time()
    expired_keys = [
        key
        for key, timestamp in last_alerted_candles.items()
        if current_time - timestamp > 3600
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
# 5. AI QUANT EVALUATIE ENGINE (COMPACT & FLASH ONLY - MAX BESPARING)
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

    # COMPACT DATA PAYLOAD (80% MINDER TOKENS PER CALL - BESPAART ENORM VEEL GELD)
    m1_prompt_block = (
        "- M1 Candles (Laatste 5): " + json.dumps(candles_1m[-5:]) + "\n"
        if candles_1m
        else "- M1 Candles: NIET ACTIEF (Scalp Mode is UIT)\n"
    )

    prompt = (
        "SYSTEM INSTRUCTIONS: QUANTITATIVE CRYPTO TRADING CO-PILOT (" + str(symbol) + ")\n"
        "1. ROL: Kwantitatieve Analyst Co-Pilot. Adviseer op basis van +EV, EV_adj, R:R en strikt risicobeheer.\n\n"
        "CONTEXT BTCUSDT: Price = $" + str(btc_context['close']) + ", Trend = " + str(btc_context['trend']) + "\n"
        "HARD KEY LEVELS VOOR " + str(symbol) + ": " + json.dumps(calculated_levels) + "\n"
        "HARD TP1 TARGETS: LONG = $" + str(nearest_long_tp) + " | SHORT = $" + str(nearest_short_tp) + "\n\n"
        "RECENTE MARKT DATA (" + str(symbol) + "):\n"
        "- 15m Candles (Laatste 5): " + json.dumps(candles_15m[-5:]) + "\n"
        "- M5 Candles (Laatste 5): " + json.dumps(candles_5m[-5:]) + "\n"
        "- M3 Candles (Laatste 5): " + json.dumps(candles_3m[-5:]) + "\n"
        + m1_prompt_block +
        "\nEXECUTION REGELS:\n"
        "1. SCALP RECLAIM: M3 Close + M1/M3 Reversal is VOLDOENDE voor GO (15m close OPTIONEEL).\n"
        "2. DAY SWEEP / SWING: 15m Full Body Close VERPLICHT.\n"
        "3. MINIMUM SL AFSTAND: Minimaal " + str(min_sl_pct) + "% op deze asset.\n"
        "4. GO EISEN: Score >= 65%, EV_adj > +0.30R, R:R naar TP1 >= 1.2R.\n\n"
        "OUTPUT FORMAT BIJ NO-GO:\n"
        "**GO / NO-GO VERDICT:** **[NO-GO]** *(Rating: B | Score: X% | EV_adj: -X.XX R)*\n"
        "Korte Analyse: (1 zin waarom NO-GO).\n\n"
        "OUTPUT FORMAT BIJ PRE-TRADE ALERT:\n"
        "**PRE-TRADE ALERT (KLAARZITTEN)** - " + str(symbol) + "\n"
        "- **Afstand tot S/R Level:** ~X.XX% (Actuele koers: $" + str(curr_price) + ")\n"
        "- **Verwachte S/R Zone:** $XX.XX (1D/4H/1H Level)\n"
        "- **Actie:** Open M3/M5 chart en wacht op reversal.\n\n"
        "OUTPUT FORMAT BIJ WATCHLIST OF GO:\n"
        "**GO / NO-GO VERDICT:** **[GO | WATCHLIST]** *(Rating: [A+ | A] | Score: X% | MAX EV_adj: +X.XX R)*\n"
        "**ALPHA TRADE ANALYSIS (" + str(symbol) + "):**\n"
        "- **BTC Context:** BTC Price = $" + str(btc_context['close']) + "\n\n"
        "**EXECUTION SUMMARY (" + str(symbol) + " - [Long / Short]):**\n"
        "- **Entry Price:** **$XX.XX**\n"
        "- **Stop Loss (SL):** **$XX.XX**\n"
        "- **TP1 Level:** **$XX.XX**\n"
        "- **Max Adjusted EV (EV_adj):** **+X.XX R**\n"
    )

    # STRIKT FLASH ONLY (VOORKOMT DUISTERE PRO-KOSTEN)
    models_to_try = [
        'gemini-2.5-flash',
        'gemini-1.5-flash',
    ]

    for model_name in models_to_try:
        try:
            response = ai_client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={'tools': []},
            )
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            print(f'Vertex Error ({model_name}) voor {symbol}: {e}', flush=True)
            continue
    return None


# ==========================================
# 6. MAIN SCANNER LOOP (HIGH FREQUENCY & FASE-SPECIFIEKE COOLDOWN)
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
            candles_15m = fetch_binance_klines(symbol, '15m', limit=15)
            candles_5m = fetch_binance_klines(symbol, '5m', limit=15)
            candles_3m = fetch_binance_klines(symbol, '3m', limit=15)

            candles_1m = (
                fetch_binance_klines(symbol, '1m', limit=10)
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

            # 🎯 1. PRE-TRADE HTF CHECK (Binnen <= 0.5% van HTF Level)
            is_near_htf, matched_level = is_price_near_any_htf_level(
                curr_close, curr_high, curr_low, calculated_levels
            )

            if not is_near_htf:
                print(
                    f'[{now_str}] [{symbol}] Geen HTF S/R nabij (> 0.5%). Scan'
                    ' afgerond.',
                    flush=True,
                )
                time.sleep(0.2)
                continue

            # 🛡️ DYNAMISCHE MICRO-FILTER
            is_pure_scalp = matched_level.get('importance') == 'LOW Scalp'
            direction = 'LONG' if curr_close >= matched_level['price'] else 'SHORT'
            nearest_tp = get_nearest_target(curr_close, calculated_levels, direction)
            reward_pct = abs(nearest_tp - curr_close) / curr_close * 100

            if is_pure_scalp and reward_pct < 0.20:
                print(
                    f'[{now_str}] [{symbol}] SKIPPED -> Pure micro-scalp TP1 te'
                    f' dichtbij ({reward_pct:.2f}% < 0.20%).',
                    flush=True,
                )
                time.sleep(0.2)
                continue

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

            if analysis:
                first_line = analysis.split('\n')[0] if analysis else 'EMPTY'
                print(
                    f'[{now_str}] 🤖 [VERTEX RESPONSE {symbol}]: {first_line}',
                    flush=True,
                )

                # 🔑 FASE-SPECIFIEKE SLEUTEL LOGICA (Borging van Pre-Trade -> Watchlist -> GO Flow)
                phase_suffix = "_NO_GO"
                if 'PRE-TRADE' in analysis or 'PRE-TRADE ALERT' in analysis:
                    phase_suffix = "_PRE"
                elif 'WATCHLIST' in analysis:
                    phase_suffix = "_WATCHLIST"
                elif '🚨 **GO**' in analysis or 'GO / NO-GO VERDICT: **GO**' in analysis:
                    phase_suffix = "_GO"

                alert_key = f"{symbol}_{last_closed_candle_time}{phase_suffix}"

                if alert_key in last_alerted_candles:
                    print(
                        f'[{symbol}] Fase {phase_suffix} reeds gemeld voor deze 15m candle. Overgeslagen.',
                        flush=True,
                    )
                    continue

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
                        f'[{now_str}] 🚨 ALERT VERSTUURD VOOR {symbol} ({phase_suffix}) NAAR TELEGRAM!',
                        flush=True,
                    )
                else:
                    last_alerted_candles[alert_key] = time.time()
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
        '1. ⚠️ **Pre-Trade Alert:** Prijs binnen <= 0.5% van HTF Key Level\n'
        '2. 👁️ **Watchlist:** 15m Full Body Close op Key Level\n'
        '3. 🚨 **GO Execution:** M3/M5 Reversal + EV_adj > +0.30R & Score >= 65%\n\n'
        '🛡️ **Cost Guardrail:** Compact Payload + Flash-Only actief (Gegarandeerd < €5/maand).'
    )
    send_telegram_message(startup_msg)

    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f'Loop error: {e}', flush=True)
        time.sleep(60)
