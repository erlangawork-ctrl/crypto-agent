import os
import time
import requests
import json
import google.generativeai as genai

# ==========================================
# 1. CONFIGURATIE & ENVIRONMENT VARIABLES
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Stel Gemini API in
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash')
else:
    print("WARNING: GEMINI_API_KEY niet gevonden in Environment Variables!")

# Geüpdatete lijst met 9 coins (High Liquidity + AI + DeFi)
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

# Geheugen om dubbele Telegram meldingen te voorkomen per candle
last_alerted_candles = {}

# ==========================================
# 2. HELPER FUNCTIES (BINANCE & TELEGRAM)
# ==========================================
def send_telegram_message(text):
    """Verstuurt geformatteerde berichten naar Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram tokens ontbreken. Bericht niet verzonden.")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Fout bij verzenden Telegram bericht: {e}")

def fetch_binance_klines(symbol, interval, limit=50):
    """Haalt candle data op van de Binance Public API."""
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        
        candles = []
        for c in data:
            candles.append({
                "timestamp": c[0],
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5])
            })
        return candles
    except Exception as e:
        print(f"Fout bij ophalen klines voor {symbol} ({interval}): {e}")
        return []

# ==========================================
# 3. AI QUANT EVALUATIE ENGINE (GEMINI)
# ==========================================
def evaluate_market_with_gemini(symbol, candles_15m, candles_5m, btc_context):
    """
    Voedt de marktdata aan Gemini 2.5 Flash en evalueert de setup volgens 
    de Quantitative Co-Pilot Master Instructions.
    """
    prompt = f"""
    Je bent een kwantitatieve Trading Analyst Co-Pilot gespecialiseerd in Crypto.
    Analyseer de volgende live marktdata voor {symbol} en geef een strikt wiskundig oordeel.

    CONTEXT BTCUSDT (Voor Trend Alignment & Altcoin Correlatie):
    - BTC 15m Laatste Close: {btc_context['close']}
    - BTC 15m Trend/Richting: {btc_context['trend']}

    TARGET ASSET DATA ({symbol}):
    - Laatste 5x 15m Candles: {json.dumps(candles_15m[-5:])}
    - Laatste 5x 5m Candles: {json.dumps(candles_5m[-5:])}

    STRIKTE EVALUATIE REGELS:
    1. Trend Alignment (35% Gewicht): Als {symbol} != 'BTCUSDT' en {symbol} vertoont een bullish setup TERWIJL BTC bearish is, verlaag de Trend Alignment score direct naar max 33.3% (Counter-Trend Penalty).
    2. 15m Full Body Close Rule: Upper/lower wick mag niet groter zijn dan 30% van het totale bereik van de 15m breakout candle. Zo niet -> No-Go / Watchlist.
    3. Reversal Confirmation (M3/M5): Zoek naar Engulfing, Pinbar (wick >= 66%), of MSS op volume >= 1.5x gemiddelde.
    4. Snelheid/False Breakout Penalty (-30%): Bij snelle stijging/daling <48u na breakdown zonder accumulatie.
    5. Bereken Setup Score (%), Win Rate (P), Gewogen R:R, EV = (P * R) - ((1-P) * 1) en Adjusted EV (EV_adj = T * EV).
    6. Als Setup Score < 65% of EV < +0.30R -> VERDICT IS AUTOMATISCH 'NO-GO' (B-Setup = 0.0% Risico).

    GEEF JE ANTWOORD UITSLUITEND IN HET VOLGENDE TELEGRAM FORMAT:

    🚨 **[GO | WATCHLIST | NO-GO]** - {symbol}
    **Rating:** [A+ | A | B] | **Score:** X% | **EV:** +X.XX R

    - **Playbook:** [Swing Breakout | Day Sweep | Scalp Reclaim]
    - **Richting:** [Long / Short]
    - **Entry:** $XX.XX
    - **Stop Loss:** $XX.XX
    - **TP1 (50%):** $XX.XX | **TP2 (30%):** $XX.XX | **Runner (20%):** $XX.XX
    - **Gewogen R:R:** X.XX R | **Fill Chance (T):** X%
    - **Adjusted EV (EV_adj):** +X.XX R

    **Korte Analyse:** (Max 2 zinnen met de exacte reden voor de score en BTC-correlatie).
    """

    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Fout tijdens Gemini API call voor {symbol}: {e}")
        return None

# ==========================================
# 4. MAIN TRADING SCANNER LOOP
# ==========================================
def run_scanner():
    print("🔍 Markt-scan gestart voor alle 9 symbolen...")
    
    # 1. Haal eerst BTCUSDT data op voor de globale correlatie-context
    btc_15m = fetch_binance_klines("BTCUSDT", "15m", limit=10)
    if not btc_15m:
        print("Kan BTCUSDT data niet ophalen. Scan afgebroken.")
        return

    btc_last_close = btc_15m[-1]["close"]
    btc_prev_close = btc_15m[-2]["close"]
    btc_trend = "BULLISH" if btc_last_close >= btc_prev_close else "BEARISH"
    
    btc_context = {
        "close": btc_last_close,
        "trend": btc_trend
    }

    # 2. Scan elk symbool in de SYMBOLS lijst
    for symbol in SYMBOLS:
        try:
            candles_15m = fetch_binance_klines(symbol, "15m", limit=20)
            candles_5m = fetch_binance_klines(symbol, "5m", limit=20)
            
            if not candles_15m or not candles_5m:
                continue

            last_candle_time = candles_15m[-1]["timestamp"]
            
            # Voorkom dubbele meldingen op dezelfde candle
            if last_alerted_candles.get(symbol) == last_candle_time:
                continue

            # Voer AI evaluatie uit
            analysis = evaluate_market_with_gemini(symbol, candles_15m, candles_5m, btc_context)
            
            if analysis:
                print(f"\n--- EVALUATIE {symbol} ---")
                print(analysis)
                
                # Stuur alleen een Telegram melding bij een GO of een valide WATCHLIST signaal
                if "🚨 **GO**" in analysis or "🚨 **WATCHLIST**" in analysis:
                    send_telegram_message(analysis)
                    last_alerted_candles[symbol] = last_candle_time
            
            # Korte pauze om API rate limits netjes te respecteren
            time.sleep(2)

        except Exception as e:
            print(f"Fout tijdens verwerken van {symbol}: {e}")

# ==========================================
# 5. SERVER STARTUP & LOOP EXECUTION
# ==========================================
if __name__ == "__main__":
    print("🚀 MyCryptoAgent Worker is succesvol gestart op Render!")
    
    # Welkomstbericht bij opstarten/deploy
    startup_msg = (
        "🤖 **MyCryptoAgent is LIVE & Online!**\n\n"
        "**Actieve Monitoring (9 Symbolen):**\n"
        "• BTC, ETH, ADA, AAVE, TAO, UNI, LINK, AVAX, SOL\n\n"
        "**Systeem Status:**\n"
        "• Scan Interval: Elke 5 minuten\n"
        "• AI Model: Gemini 2.5 Flash\n"
        "• Protocols: No-Blind-Limit, M3/M5 Reversal, BTC Alignment Penalty\n"
        "• Audio-Routing: Samsung Lock Screen Push OK"
    )
    send_telegram_message(startup_msg)

    # Infinite loop die elke 5 minuten (300 seconden) draait
    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f"Onverwachte fout in de hoofd-loop: {e}")
        
        print("😴 Wachten op de volgende 5-minuten cyclus...")
        time.sleep(300)
