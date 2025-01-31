import MetaTrader5 as mt5
import logging
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import os

# Load credentials securely from environment variables
MT5_LOGIN = int(os.getenv("MT5_LOGIN", "208254608"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "Mybestfriend@1")
MT5_SERVER = os.getenv("MT5_SERVER", "Exness-MT5Trial9")

# Configure logging with more detailed format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("trading_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("OswaldFxBotV2.0")

class TradingBot:
    def __init__(self, symbols=None, risk_percentage=1, max_positions=3):
        self.symbols = symbols if symbols else ["EURUSDm"]
        self.risk_percentage = risk_percentage
        self.max_positions = max_positions
        self.timeframes = {
            'fast': mt5.TIMEFRAME_M5,
            'medium': mt5.TIMEFRAME_M15,
            'slow': mt5.TIMEFRAME_H1,
            'long': mt5.TIMEFRAME_H4  # Added longer timeframe
        }

    def initialize(self):
        mt5.shutdown()
        if not mt5.initialize():
            logger.error(f"Failed to initialize MT5: {mt5.last_error()}")
            return False
        if not mt5.login(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
            logger.error(f"Failed to login: {mt5.last_error()}")
            return False
        logger.info("Successfully connected to MT5")
        return True

    def get_market_data(self, symbol, timeframe, periods=500):
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, periods)
        if rates is None:
            return None
        df = pd.DataFrame(rates)
        df['sma20'] = df['close'].rolling(window=20).mean()
        df['sma50'] = df['close'].rolling(window=50).mean()
        df['sma200'] = df['close'].rolling(window=200).mean()
        df['rsi'] = self.calculate_rsi(df['close'])
        df['atr'] = self.calculate_atr(df)
        df['obv'] = self.calculate_obv(df)
        return df

    def calculate_rsi(self, close_prices, period=14):
        delta = close_prices.diff()
        gain = pd.Series(np.where(delta > 0, delta, 0), index=close_prices.index).rolling(window=period).mean()
        loss = pd.Series(np.where(delta < 0, -delta, 0), index=close_prices.index).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    def calculate_atr(self, df, period=14):
        tr = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift()), abs(df['low'] - df['close'].shift())))
        return tr.rolling(window=period).mean()

    def calculate_obv(self, df):
        obv = (np.sign(df['close'].diff()) * df['tick_volume']).cumsum()
        return obv

    def check_signals(self):
        signals = {}
        for symbol in self.symbols:
            df = self.get_market_data(symbol, self.timeframes['fast'])
            if df is None:
                continue
            last = df.iloc[-1]
            if last['sma20'] > last['sma50'] > last['sma200'] and last['rsi'] < 70:
                signals[symbol] = mt5.ORDER_TYPE_BUY
            elif last['sma20'] < last['sma50'] < last['sma200'] and last['rsi'] > 30:
                signals[symbol] = mt5.ORDER_TYPE_SELL
        return signals

    def place_trade(self, symbol, order_type):
        price = mt5.symbol_info_tick(symbol).ask if order_type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(symbol).bid
        sl, tp = self.calculate_sl_tp(order_type, price)
        lot_size = 0.1  # Default lot size
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot_size,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 10,
            "magic": 234000,
            "comment": "OswaldFxBotV2.0",
            "type_filling": mt5.ORDER_FILLING_IOC,
            "type_time": mt5.ORDER_TIME_GTC,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Trade failed for {symbol}: {result.comment}")
            return False
        logger.info(f"Trade placed successfully: {symbol} {order_type}")
        return True

    def run(self):
        if not self.initialize():
            return
        last_candle_time = None
        while True:
            current_time = datetime.now()
            if last_candle_time is None or current_time.minute % 5 == 0 and current_time.minute != last_candle_time:
                last_candle_time = current_time.minute
                signals = self.check_signals()
                for symbol, signal in signals.items():
                    self.place_trade(symbol, signal)
            time.sleep(1)

if __name__ == "__main__":
    bot = TradingBot(symbols=["EURUSDm", "GBPUSDm", "USDJPYm"], risk_percentage=1, max_positions=3)
    bot.run()
