import MetaTrader5 as mt5
import logging
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# Configure logging with more detailed format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("trading_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("OswaldFxBotV1.1")

class TradingBot:
    def __init__(self, symbols=None, risk_percentage=1, max_positions=3):
        self.symbols = symbols if symbols else ["EURUSDm"]
        self.risk_percentage = risk_percentage
        self.max_positions = max_positions
        self.timeframes = {
            'fast': mt5.TIMEFRAME_M5,
            'medium': mt5.TIMEFRAME_M15,
            'slow': mt5.TIMEFRAME_H1
        }

    def initialize(self, login=None, password=None, server=None):
        """Initialize MT5 connection with error handling"""
        # Shutdown any existing MT5 connections
        mt5.shutdown()

        # Initialize MT5
        if not mt5.initialize():
            logger.error(f"Failed to initialize MT5: {mt5.last_error()}")
            return False

        # Attempt login if credentials are provided
        if login and password and server:
            authorized = mt5.login(
                login=login,
                password=password,
                server=server
            )
            if not authorized:
                logger.error(f"Failed to login to MT5 account: {mt5.last_error()}")
                return False
        else:
            logger.warning("No login credentials provided. Attempting to use existing connection.")

        # Verify account connection
        account_info = mt5.account_info()
        if account_info is None:
            logger.error("Failed to connect to trading account")
            return False

        logger.info(f"Successfully initialized MT5. Account: {account_info.login}")
        return True

    def get_market_structure(self, symbol, timeframe, periods=500):
        """Analyze market structure using multiple indicators"""
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, periods)
        if rates is None:
            return None

        df = pd.DataFrame(rates)

        # Calculate multiple moving averages
        df['sma20'] = df['close'].rolling(window=20).mean()
        df['sma50'] = df['close'].rolling(window=50).mean()
        df['sma200'] = df['close'].rolling(window=200).mean()

        # Calculate RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # Calculate ATR for volatility
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(
                abs(df['high'] - df['close'].shift()),
                abs(df['low'] - df['close'].shift())
            )
        )
        df['atr'] = df['tr'].rolling(window=14).mean()

        return df

    def check_market_conditions(self):
        """Analyze market conditions across multiple timeframes for each symbol"""
        conditions = {}
        for symbol in self.symbols:
            conditions[symbol] = {}
            for tf_name, tf in self.timeframes.items():
                df = self.get_market_structure(symbol, tf)
                if df is None:
                    continue
                latest = df.iloc[-1]
                conditions[symbol][tf_name] = {
                    'trend': (
                        'uptrend' if latest['sma20'] > latest['sma50'] > latest['sma200']
                        else 'downtrend' if latest['sma20'] < latest['sma50'] < latest['sma200']
                        else 'sideways'
                    ),
                    'rsi': latest['rsi'],
                    'volatility': latest['atr']
                }
        return conditions

    def get_entry_signals(self, conditions):
        """Generate entry signals based on market conditions"""
        signals = {}
        for symbol, condition in conditions.items():
            # Check if trends align across timeframes for each symbol
            trend_alignment = all(
                cond['trend'] == 'uptrend' for cond in condition.values()
            ) or all(
                cond['trend'] == 'downtrend' for cond in condition.values()
            )

            if trend_alignment:
                # Check RSI conditions
                fast_rsi = condition['fast']['rsi']
                if condition['fast']['trend'] == 'uptrend':
                    if 30 < fast_rsi < 70:  # Not overbought
                        signals[symbol] = mt5.ORDER_TYPE_BUY
                else:
                    if 30 < fast_rsi < 70:  # Not oversold
                        signals[symbol] = mt5.ORDER_TYPE_SELL
        return signals

    def calculate_position_size(self, symbol, stop_loss_pips):
        """Calculate position size based on account risk percentage"""
        account_info = mt5.account_info()
        if not account_info:
            return 0.01  # Minimum lot size as fallback

        symbol_info = mt5.symbol_info(symbol)
        pip_value = symbol_info.trade_tick_value * (10 ** (symbol_info.digits - 4))
        risk_amount = account_info.balance * (self.risk_percentage / 100)
        position_size = risk_amount / (stop_loss_pips * pip_value)

        # Round to nearest valid lot size and respect limits
        position_size = round(position_size, 2)
        return max(min(position_size, symbol_info.volume_max), symbol_info.volume_min)

    def calculate_sl_tp(self, order_type, entry_price, atr):
        """Calculate dynamic SL/TP based on ATR"""
        sl_multiplier = 1.5
        tp_multiplier = 2.5

        if order_type == mt5.ORDER_TYPE_BUY:
            sl = entry_price - (atr * sl_multiplier)
            tp = entry_price + (atr * tp_multiplier)
        else:
            sl = entry_price + (atr * sl_multiplier)
            tp = entry_price - (atr * tp_multiplier)

        return sl, tp

    def place_trade(self, symbol, order_type, conditions):
        """Place trade with dynamic position sizing and SL/TP for each symbol"""
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            logger.error(f"Failed to get symbol info for {symbol}")
            return False

        # Calculate entry price
        if order_type == mt5.ORDER_TYPE_BUY:
            price = symbol_info.ask
        else:
            price = symbol_info.bid

        # Calculate dynamic SL/TP based on volatility
        sl, tp = self.calculate_sl_tp(
            order_type, 
            price, 
            conditions['fast']['volatility']
        )

        # Calculate position size based on risk
        sl_pips = abs(price - sl) / symbol_info.point
        position_size = self.calculate_position_size(symbol, sl_pips)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": position_size,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 10,
            "magic": 234000,
            "comment": "EnhancedFxBot",
            "type_filling": mt5.ORDER_FILLING_IOC,
            "type_time": mt5.ORDER_TIME_GTC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order failed for {symbol}: {result.comment}")
            return False

        logger.info(f"Order placed successfully for {symbol}: {order_type} {position_size} lots at {price}")
        return True

    def manage_positions(self):
        """Manage open positions"""
        positions = mt5.positions_get()
        if positions is None:
            return

        for position in positions:
            # Calculate current profit in pips
            current_price = mt5.symbol_info_tick(position.symbol).bid
            profit_pips = (current_price - position.price_open) / mt5.symbol_info(position.symbol).point

            # Trail stop loss if in profit
            if profit_pips > 50:  # Minimum pips in profit before trailing
                new_sl = position.price_open + (profit_pips * 0.5 * mt5.symbol_info(position.symbol).point)
                if new_sl > position.sl:  # Only move SL up for buy positions
                    self.modify_sl_tp(position.ticket, new_sl, position.tp)

    def modify_sl_tp(self, ticket, sl, tp):
        """Modify SL/TP for an open position"""
        request = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "symbol": self.symbol,
            "sl": sl,
            "tp": tp,
            "position": ticket
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Failed to modify position: {result.comment}")
        else:
            logger.info(f"Successfully modified position {ticket}")

    def run(self):
        """Main bot loop"""
        if not self.initialize():
            return

        while True:
            try:
                # Check current market conditions for all symbols
                conditions = self.check_market_conditions()
                if not conditions:
                    continue

                # Manage existing positions
                self.manage_positions()

                # Get entry signals for each symbol
                signals = self.get_entry_signals(conditions)
                for symbol, signal in signals.items():
                    self.place_trade(symbol, signal, conditions[symbol])

                time.sleep(5)  # Adjust checking frequency

            except Exception as e:
                logger.error(f"Error in bot loop: {e}")
                time.sleep(60)  # Wait before retrying

if __name__ == "__main__":
    # Your MT5 account credentials
    MT5_LOGIN = 208254608  # Replace with your account number
    MT5_PASSWORD = "Mybestfriend@1"  # Replace with your password
    MT5_SERVER = "Exness-MT5Trial9"  # Replace with your broker's server name

    # Initialize the bot with multiple symbols
    bot = TradingBot(symbols=["EURUSDm", "GBPUSDm", "USDJPYm"], risk_percentage=1, max_positions=3)

    # Initialize connection with credentials
    if bot.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        bot.run()
    else:
        logger.error("Failed to initialize the bot. Please check your credentials.")
