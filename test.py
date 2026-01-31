import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta
import pytz

# ================= CONFIG =================
SYMBOL = "XAUUSDm"
HTF = mt5.TIMEFRAME_M15
LTF = mt5.TIMEFRAME_M5

RISK_PERCENT = 0.5
RR = 1.5
MAX_SPREAD = 30  # points
ATR_MULTIPLIER = 1.2

MAX_DAILY_LOSS = 2.0
MAX_CONSECUTIVE_LOSSES = 3
COOLDOWN_MINUTES = 15

LONDON_START = 7
NY_START = 13
SESSION_END = 20

TIMEZONE = pytz.timezone("Europe/London")

def connect():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
    if not mt5.symbol_select(SYMBOL, True):
        raise RuntimeError("Symbol not available")

def get_rates(tf, bars=200):
    rates = mt5.copy_rates_from_pos(SYMBOL, tf, 0, bars)
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = abs(df['high'] - df['close'].shift())
    low_close = abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def in_session():
    now = datetime.now(TIMEZONE).hour
    return (LONDON_START <= now <= SESSION_END)

def spread_ok():
    tick = mt5.symbol_info_tick(SYMBOL)
    spread = (tick.ask - tick.bid) / mt5.symbol_info(SYMBOL).point
    return spread <= MAX_SPREAD

def htf_trend():
    df = get_rates(HTF)
    df['ema50'] = ema(df['close'], 50)
    df['ema200'] = ema(df['close'], 200)
    if df['ema50'].iloc[-1] > df['ema200'].iloc[-1]:
        return "BUY"
    if df['ema50'].iloc[-1] < df['ema200'].iloc[-1]:
        return "SELL"
    return None

def ltf_entry(direction):
    df = get_rates(LTF)
    df['ema50'] = ema(df['close'], 50)
    df['ema200'] = ema(df['close'], 200)
    df['rsi'] = rsi(df['close'])
    df['atr'] = atr(df)

    last = df.iloc[-1]

    if last['atr'] < df['atr'].mean():
        return False

    if direction == "BUY":
        return (
            last['close'] > last['ema50'] and
            last['rsi'] > 55
        )

    if direction == "SELL":
        return (
            last['close'] < last['ema50'] and
            last['rsi'] < 45
        )

    return False

def lot_size(sl_points):
    acc = mt5.account_info()
    risk_amount = acc.balance * (RISK_PERCENT / 100)
    tick_value = mt5.symbol_info(SYMBOL).trade_tick_value
    return round(risk_amount / (sl_points * tick_value), 2)

def execute_trade(direction):
    tick = mt5.symbol_info_tick(SYMBOL)
    price = tick.ask if direction == "BUY" else tick.bid

    df = get_rates(LTF)
    atr_val = atr(df).iloc[-1]
    sl_dist = atr_val * ATR_MULTIPLIER
    tp_dist = sl_dist * RR

    sl = price - sl_dist if direction == "BUY" else price + sl_dist
    tp = price + tp_dist if direction == "BUY" else price - tp_dist

    volume = lot_size(sl_dist / mt5.symbol_info(SYMBOL).point)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 10,
        "magic": 777,
        "comment": "HTF-LTF Scalper",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK,
    }

    return mt5.order_send(request)

def run():
    connect()
    print("XAUUSD SCALPER RUNNING")

    while True:
        if not in_session() or not spread_ok():
            time.sleep(30)
            continue

        if mt5.positions_total() > 0:
            time.sleep(10)
            continue

        direction = htf_trend()
        if direction and ltf_entry(direction):
            execute_trade(direction)
            time.sleep(60)

        time.sleep(5)

run()
