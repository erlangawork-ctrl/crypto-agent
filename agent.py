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
        print(f"Telegram status: {response.status_code}")
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
    print("==================================================\n")
    
    if not GEMINI_API_KEY:
        raise ValueError("CRITICAL: Geen GEMINI_API_KEY gevonden!")

    # Welkomstbericht op Telegram bij opstarten
    send_telegram_message("🚀 Crypto Agent Online! De 24/7 markt-scanner is succesvol opgestart op Render.")

    client = genai.Client(api_key=GEMINI_API_KEY)
    exchange = ccxt.binance()
    
    symbols = ["BTC/USDT", "ETH/USDT"]
    
    while True:
        try:
            for symbol in symbols:
                current_time = time.strftime('%Y-%m-%d %H:%M:%S')
                print(f"[{current_time}] Scannen van {symbol} via Binance...")
                
                bars = exchange.fetch_ohlcv(symbol, timeframe="15m", limit=20)
                df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                
                market_data_json = df.to_json(orient="records")
                
                prompt = f"""
                Hier is de meest recente 15m OHLCV marktdata voor {symbol}:
                {market_data_json}
                
                Evalueer of er op dit moment een geldige trade setup ontstaat op basis van onze Master Instructions.
                
                STRIKTE REGELS OM AF TE DWINGEN:
                1. Pas het Strikt No-Blind-Limit Protocol toe (vereist M3/M5 reversal bevestiging op de retest).
                2. Controleer de 15m Full Body Close Regel (upper/lower wick mag niet >30% zijn van de kaars).
                3. Pas de -30% False Breakout Penalty toe bij herstel <48u na HTF breakdown.
                4. Pas de US Open Filter toe (geen front-run limit orders op S/R tussen 15:15 en 16:30 CET).
                5. Pas de gewogen R:R formule toe met het Scale-Out Protocol (50% TP1, 30% TP2, 20% Runner).
                6. Bereken de Setup Score, EV en EV_adj (met Fill Chance T).
                
                COMMUNICATIE INSTRUCTIES:
                - Als er GÉÉN directe setup klaarstaat (Score < 65%), geef dan een hele korte statusupdate van 1-2 regels volgens Sectie 2.
                - Als er WÉL een actieve setup of retest is (Score >= 65%), gebruik dan ONVOORWAARDELIJK het volledige verplichte Output Format uit Sectie 8 (inclusief Execution Optimization Matrix).
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
                
                # Stuur een melding naar Telegram als er een GO of WATCHLIST status is getriggerd
                if "GO" in analysis_text or "WATCHLIST" in analysis_text:
                    telegram_msg = f"📊 TRADE SIGNAL: {symbol}\n\n{analysis_text}"
                    send_telegram_message(telegram_msg)
                
            print("Wachten op volgende scaninterval (5 minuten)...\n")
            time.sleep(300)
            
        except Exception as e:
            print(f"Fout tijdens scan loop: {e}")
            time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    run_trading_agent()
