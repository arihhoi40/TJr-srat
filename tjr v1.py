import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, time, timezone

import time as sleep

# ================== SETTINGS ==================
SYMBOL = "US30m"
HTF = mt5.TIMEFRAME_M15
LTF = mt5.TIMEFRAME_M5
RISK_PERCENT = 1.0
RR = 2.0
MAGIC = 55101

# ================== MT5 INIT ==================
if not mt5.initialize():
    raise RuntimeError("MT5 failed to initialize")

# ================== UTILS ==================
def get_df(symbol, timeframe, bars=200):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df

def in_killzone():
    now = datetime.now(timezone.utc).time()
    return (
        time(8, 0) <= now <= time(11, 0) or
        time(13, 30) <= now <= time(16, 30)
    )
# ================== STRUCTURE ==================
def market_structure(df):
    highs = df['high']
    lows = df['low']

    hh = highs.iloc[-3] > highs.iloc[-4]
    hl = lows.iloc[-3] > lows.iloc[-4]
    ll = lows.iloc[-3] < lows.iloc[-4]
    lh = highs.iloc[-3] < highs.iloc[-4]

    if hh and hl:
        return "BULLISH"
    if ll and lh:
        return "BEARISH"
    return None

# ================== LIQUIDITY ==================
def liquidity_sweep(df, bias):
    prev = df.iloc[-2]
    curr = df.iloc[-1]

    if bias == "BULLISH":
        return curr.low < prev.low
    if bias == "BEARISH":
        return curr.high > prev.high
    return False

# ================== DISPLACEMENT ==================
def displacement(df):
    bodies = abs(df['close'] - df['open'])
    avg = bodies.rolling(20).mean().iloc[-1]
    return bodies.iloc[-1] > avg * 1.5

# ================== FVG ==================
def fair_value_gap(df, bias):
    c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

    if bias == "BULLISH" and c3.low > c1.high:
        return (c1.high, c3.low)

    if bias == "BEARISH" and c3.high < c1.low:
        return (c3.high, c1.low)

    return None

# ================== RISK ==================
def lot_size(sl_pips):
    acc = mt5.account_info()
    risk_money = acc.balance * (RISK_PERCENT / 100)
    tick = mt5.symbol_info(SYMBOL).trade_tick_value
    return round(risk_money / (sl_pips * tick), 2)

# ================== EXECUTION ==================
def place_trade(direction, entry, sl):
    risk = abs(entry - sl)
    tp = entry + risk * RR if direction == "BUY" else entry - risk * RR
    volume = lot_size(risk)

    tick = mt5.symbol_info_tick(SYMBOL)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": tick.ask if direction == "BUY" else tick.bid,
        "sl": sl,
        "tp": tp,
        "magic": MAGIC,
        "type_filling": mt5.ORDER_FILLING_IOC
    }

    mt5.order_send(request)

# ================== BREAKEVEN ==================
def manage_be():
    positions = mt5.positions_get()
    if positions is None:
        return

    tick = mt5.symbol_info_tick(SYMBOL)

    for p in positions:
        if p.magic != MAGIC or p.sl == 0:
            continue

        if p.type == mt5.ORDER_TYPE_BUY:
            r = p.price_open - p.sl
            if tick.bid >= p.price_open + r:
                mt5.order_send({
                    "action": mt5.TRADE_ACTION_SLTP,
                    "position": p.ticket,
                    "sl": p.price_open,
                    "tp": p.tp
                })

        if p.type == mt5.ORDER_TYPE_SELL:
            r = p.sl - p.price_open
            if tick.ask <= p.price_open - r:
                mt5.order_send({
                    "action": mt5.TRADE_ACTION_SLTP,
                    "position": p.ticket,
                    "sl": p.price_open,
                    "tp": p.tp
                })

# ================== MAIN LOOP ==================
print("TJR BOOTCAMP BOT RUNNING")

while True:
    manage_be()

    positions = [p for p in mt5.positions_get() if p.magic == MAGIC]
    if positions:
        sleep.sleep(10)
        continue

    if not in_killzone():
        sleep.sleep(10)
        continue

    htf = get_df(SYMBOL, HTF)
    bias = market_structure(htf)
    if not bias:
        sleep.sleep(10)
        continue

    ltf = get_df(SYMBOL, LTF)
    if not liquidity_sweep(ltf, bias):
        sleep.sleep(10)
        continue

    if not displacement(ltf):
        sleep.sleep(10)
        continue

    fvg = fair_value_gap(ltf, bias)
    if not fvg:
        sleep.sleep(10)
        continue

    entry = sum(fvg) / 2
    sl = ltf.low.iloc[-2] if bias == "BULLISH" else ltf.high.iloc[-2]

    place_trade("BUY" if bias == "BULLISH" else "SELL", entry, sl)

    sleep.sleep(30)
