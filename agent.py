import os
import time
import ccxt
import pandas as pd
from google import genai
from system_prompt import SYSTEM_INSTRUCTIONS

# ==========================================
# CONFIGURATIE & API KEY (Via Environment Variable)
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def run_trading_agent():
    print("==================================================")
    print(" AI Trading Agent gestart op Render.com (24/7)    ")
    print(" Strategy: EV_adj, Scale-Out (50/30/20), No-Blind-Limit")
    print("==================================================\n")
    
    if not GEMINI_API_KEY:
        raise ValueError("CRITICAL: Geen GEMINI_API_KEY gevonden in environment variables!")

    # Maak verbinding met de Gemini API
    client = genai.Client(api_key=GEMINI_API_KEY)
    exchange = ccxt.binance()
    
    symbols = ["BTC/USDT", "ETH/USDT"]
    
    while True:
        try:
            for symbol in symbols:
                current_time = time.strftime('%Y-%m-%d %H:%M:%S')
                print(f"[{current_time}] Scannen van {symbol} via Binance...")
                
                # Haal de meest recente 20 M15 kaarsen op van Binance
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
                
                print(f"\n--- ANALYSE RESULTAAT {symbol} ---")
                print(response.text)
                print("------------------------------------\n")
                
            print("Wachten op volgende scaninterval (5 minuten)...\n")
            time.sleep(300)
            
        except Exception as e:
            print(f"Fout tijdens scan loop: {e}")
            print("Herstarten over 60 seconden...")
            time.sleep(60)

if __name__ == "__main__":
    run_trading_agent()