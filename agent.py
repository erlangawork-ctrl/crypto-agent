import os
import time
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
import ccxt
import pandas as pd
from google import genai
from system_prompt import SYSTEM_INSTRUCTIONS

# ==========================================
# CONFIGURATIE & ENVIRONMENT VARIABLES
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram_message(message):
    """Verstuurt een bericht via de Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram niet geconfigureerd in Environment Variables.")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        print(f"Telegram status code: {response.status_code}")
    except Exception as e:
        print(f"Fout bij versturen Telegram bericht: {e}")

# Dummy HTTP Server voor Render Health Check
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Crypto Agent Live & Scanning 24/7!")

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"Web service dummy poort actief op poort {port}")
    server.serve_forever()

def run_trading_agent():
    print("==================================================")
    print(" AI Trading Agent gestart op Render.com (24/7)    ")
    print(" Strategy: EV_adj, Scale-Out (50/30/20), No-Blind-Limit")
    print(" Features: Multi-timeframe (15m/5m) + Early Warnings")
    print("==================================================\n")
    
    if not GEMINI_API_KEY:
        raise ValueError("CRITICAL: Geen GEMINI_API_KEY gevonden!")

    # Welkomstbericht op Telegram bij opstarten
    send_telegram_message("🚀 Crypto Agent Online! Multi-timeframe scanner met Early Warnings is actief op Render.")

    client = genai.Client(api_key=GEMINI_API_KEY)
    exchange = ccxt.binance()
    
    symbols = ["BTC/USDT", "ETH/USDT"]
    
    while True:
        try:
            for symbol in symbols:
                current_time = time.strftime('%Y-%m-%d %H:%M:%S')
                print(f"[{current_time}] Scannen van {symbol} (15m + 5m) via Binance...")
                
                # 1. Haal 15m data op voor de macro/structureele context & Full Body Close
                bars_15m = exchange.fetch_ohlcv(symbol, timeframe="15m", limit=20)
                df_15m = pd.DataFrame(bars_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df_15m['timestamp'] = pd.to_datetime(df_15m['timestamp'], unit='ms')
                
                # 2. Haal 5m data op voor de M3/M5 Reversal Confirmation op de retest
                bars_5m = exchange.fetch_ohlcv(symbol, timeframe="5m", limit=12)
                df_5m = pd.DataFrame(bars_5m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df_5m['timestamp'] = pd.to_datetime(df_5m['timestamp'], unit='ms')
                
                data_json_15m = df_15m.to_json(orient="records")
                data_json_5m = df_5m.to_json(orient="records")
                
                prompt = f"""
                Hier is de live marktdata voor {symbol}:

                ### 15-MINUTEN MARKTDATA (Context & Full Body Close):
                {data_json_15m}

                ### 5-MINUTEN MARKTDATA (Micro Reversal & Retest Trigger):
                {data_json_5m}

                Evalueer of er op dit moment een geldige trade setup ontstaat of dat de koers een belangrijk S/R niveau nadert.

                COMMUNICATIE- EN NOTIFICATIE INSTRUCTIES:
                1. ALERT (Early Warning): Als de prijs een belangrijk HTF S/R-niveau tot op <=0.3% nadert, maar de M3/M5 reversal nog NIET is bevestigd, start je antwoord met 'ALERT' en geef een korte waarschuwing dat de zone wordt genaderd.
                2. WATCHLIST: Als er een 15m Full Body Close is geweest maar de M3/M5 retest nog gaande is, start je met 'WATCHLIST' en gebruik je de tabellen.
                3. GO: Als er een actieve setup is mét afgeronde M3/M5 reversal candle (Score >= 65%), start je met 'GO' en gebruik je het volledige Output Format uit Sectie 8.
                4. NO-GO / GEEN SETUP: Als er niks boeiends gebeurt (Score < 65%), geef dan een heel korte statusupdate van 1 regel zonder tabellen.
                """
                
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                    config={'system_instruction': SYSTEM_INSTRUCTIONS}
                )
                
                analysis_text = response.text
                print(f"\n--- ANALYSE RESULTAAT {symbol} ---")
                print(analysis_text)
                print("------------------------------------\n")
                
                # Stuur een melding naar Telegram bij ALERT, WATCHLIST of GO
                if any(tag in analysis_text for tag in ["GO", "WATCHLIST", "ALERT"]):
                    telegram_msg = f"📊 MARKET UPDATE: {symbol}\n\n{analysis_text}"
                    send_telegram_message(telegram_msg)
                
            print("Wachten op volgende scaninterval (5 minuten)...\n")
            time.sleep(300)
            
        except Exception as e:
            print(f"Fout tijdens scan loop: {e}")
            time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    run_trading_agent()
