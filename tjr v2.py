import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, time, timezone

import time as sleep
import logging

# ================== SETTINGS ==================
SYMBOL = "XAUUSDm"
CORRELATED_SYMBOL = "XAUUSDm"  # For SMT divergence
HTF = mt5.TIMEFRAME_H4  # Macro bias (TJR: 4H for structure)
ITF = mt5.TIMEFRAME_H1  # Intermediate for key levels (TJR: 1H sessions)
LTF = mt5.TIMEFRAME_M5  # Execution
RISK_PERCENT = 1.0
MIN_RR = 2.0  # Minimum, but dynamic preferred
MAGIC = 55101
BARS = 500  # More data for accuracy

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# ================== MT5 INIT ==================
if not mt5.initialize():
    raise RuntimeError("MT5 failed to initialize")


# ================== UTILS ==================
def get_df(symbol, timeframe, bars=BARS):
    try:
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
        if rates is None or len(rates) < bars:
            raise ValueError(f"Insufficient data for {symbol} on {timeframe}")
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df
    except Exception as e:
        logging.error(f"Data fetch error: {e}")
        return None


def in_killzone():
    now = datetime.now(timezone.utc).time()
    return (
        time(8, 0) <= now <= time(11, 0) or
        time(13, 30) <= now <= time(16, 30)
    )


# ================== KEY LEVELS (1H Sessions) ==================
def get_key_levels(df_itf):
    # Previous session highs/lows as liquidity pools
    df_itf['session'] = df_itf['time'].dt.floor('D')  # Group by day
    sessions = df_itf.groupby('session').agg({'high': 'max', 'low': 'min'})
    prev_high = sessions['high'].iloc[-2] if len(sessions) > 1 else df_itf['high'].max()
    prev_low = sessions['low'].iloc[-2] if len(sessions) > 1 else df_itf['low'].min()
    return prev_high, prev_low


# ================== STRUCTURE (BOS/CHOCH on 4H) ==================
def market_structure(df):
    # Pivot-based: Find recent swing highs/lows
    pivots_high = (df['high'].shift(1) < df['high']) & (df['high'].shift(-1) < df['high'])
    pivots_low = (df['low'].shift(1) > df['low']) & (df['low'].shift(-1) > df['low'])
    recent_highs = df['high'][pivots_high].tail(2)
    recent_lows = df['low'][pivots_low].tail(2)

    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        if recent_highs.iloc[-1] > recent_highs.iloc[-2] and recent_lows.iloc[-1] > recent_lows.iloc[-2]:
            return "BULLISH_BOS"
        if recent_lows.iloc[-1] < recent_lows.iloc[-2] and recent_highs.iloc[-1] < recent_highs.iloc[-2]:
            return "BEARISH_BOS"
        if recent_highs.iloc[-1] < recent_highs.iloc[-2] and recent_lows.iloc[-1] > recent_lows.iloc[-2]:
            return "BEARISH_CHOCH"
        if recent_lows.iloc[-1] > recent_lows.iloc[-2] and recent_highs.iloc[-1] > recent_highs.iloc[-2]:
            return "BULLISH_CHOCH"
    return None


# ================== SMT DIVERGENCE ==================
def smt_divergence(ltf_main, ltf_corr, bias):
    main_disp = displacement(ltf_main)
    corr_disp = displacement(ltf_corr)
    # Divergence: Main displaces in bias direction, corr doesn't
    if bias.startswith("BULLISH"):
        return main_disp and ltf_main['close'].iloc[-1] > ltf_main['open'].iloc[-1] and not corr_disp
    if bias.startswith("BEARISH"):
        return main_disp and ltf_main['close'].iloc[-1] < ltf_main['open'].iloc[-1] and not corr_disp
    return False


# ================== LIQUIDITY SWEEP ==================
def liquidity_sweep(df, bias, key_high, key_low):
    curr = df.iloc[-1]
    if bias.startswith("BULLISH") and curr['low'] < key_low:
        return True  # Swept low liquidity
    if bias.startswith("BEARISH") and curr['high'] > key_high:
        return True  # Swept high liquidity
    return False


# ================== DISPLACEMENT ==================
def displacement(df):
    bodies = abs(df['close'] - df['open'])
    avg_body = bodies.rolling(20).mean().iloc[-1]
    return bodies.iloc[-1] > avg_body * 1.5


# ================== FVG ==================
def fair_value_gap(df, bias):
    c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    if bias.startswith("BULLISH") and c3['low'] > c1['high']:
        return (c1['high'], c3['low'])
    if bias.startswith("BEARISH") and c3['high'] < c1['low']:
        return (c3['high'], c1['low'])
    return None


# ================== ORDER BLOCK ==================
def order_block(df, bias):
    # Last strong reversal candle as OB
    bodies = abs(df['close'] - df['open'])
    strong = bodies > bodies.rolling(10).mean() * 1.5
    if bias.startswith("BULLISH"):
        bull_ob = df[(strong) & (df['close'] > df['open'])].tail(1)
        if not bull_ob.empty:
            return (bull_ob['low'].iloc[0], bull_ob['high'].iloc[0])  # Discount OB
    if bias.startswith("BEARISH"):
        bear_ob = df[(strong) & (df['close'] < df['open'])].tail(1)
        if not bear_ob.empty:
            return (bear_ob['low'].iloc[0], bear_ob['high'].iloc[0])  # Premium OB
    return None


# ================== RETRACE CHECK ==================
def in_retrace(df, zone, bias):
    curr_price = df['close'].iloc[-1]
    low_zone, high_zone = min(zone), max(zone)
    if bias.startswith("BULLISH"):
        return low_zone <= curr_price <= high_zone  # Retraced into discount
    if bias.startswith("BEARISH"):
        return low_zone <= curr_price <= high_zone  # Retraced into premium
    return False


# ================== RISK ==================
def lot_size(sl_pips):
    try:
        acc = mt5.account_info()
        if acc is None:
            raise ValueError("Account info unavailable")
        risk_money = acc.balance * (RISK_PERCENT / 100)
        tick = mt5.symbol_info(SYMBOL).trade_tick_value
        return round(risk_money / (sl_pips * tick), 2)
    except Exception as e:
        logging.error(f"Lot size error: {e}")
        return 0.0


# ================== EXECUTION ==================
def place_trade(direction, entry, sl, tp):
    try:
        risk = abs(entry - sl)
        if risk == 0 or tp is None:
            return
        volume = lot_size(risk)
        if volume <= 0:
            return
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            raise ValueError("Tick info unavailable")
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
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logging.error(f"Order failed: {result.comment}")
    except Exception as e:
        logging.error(f"Trade placement error: {e}")


# ================== BREAKEVEN ==================
def manage_be():
    try:
        positions = mt5.positions_get(symbol=SYMBOL)
        if positions is None:
            return
        tick = mt5.symbol_info_tick(SYMBOL)
        for p in positions:
            if p.magic != MAGIC or p.sl == 0:
                continue
            if p.type == mt5.POSITION_TYPE_BUY:
                r = p.price_open - p.sl
                if tick.bid >= p.price_open + r:
                    mt5.position_modify(p.ticket, sl=p.price_open, tp=p.tp)
            if p.type == mt5.POSITION_TYPE_SELL:
                r = p.sl - p.price_open
                if tick.ask <= p.price_open - r:
                    mt5.position_modify(p.ticket, sl=p.price_open, tp=p.tp)
    except Exception as e:
        logging.error(f"BE management error: {e}")


# ================== MAIN LOOP ==================
logging.info("100% TJR BOOTCAMP BOT RUNNING")

while True:
    manage_be()

    positions = mt5.positions_get(symbol=SYMBOL)
    if positions and any(p.magic == MAGIC for p in positions):
        sleep.sleep(60)  # Wait longer if open
        continue

    if not in_killzone():
        sleep.sleep(60)
        continue

    htf = get_df(SYMBOL, HTF)
    if htf is None:
        sleep.sleep(60)
        continue
    bias = market_structure(htf)
    if not bias:
        sleep.sleep(60)
        continue

    itf = get_df(SYMBOL, ITF)
    if itf is None:
        sleep.sleep(60)
        continue
    key_high, key_low = get_key_levels(itf)
    curr_price = itf['close'].iloc[-1]
    range_size = key_high - key_low
    near_key_level = abs(curr_price - key_high) < range_size * 0.03 or abs(curr_price - key_low) < range_size * 0.03
    if not near_key_level:
        sleep.sleep(60)
        continue

    ltf_main = get_df(SYMBOL, LTF)
    ltf_corr = get_df(CORRELATED_SYMBOL, LTF)
    if ltf_main is None or ltf_corr is None:
        sleep.sleep(60)
        continue

    if not smt_divergence(ltf_main, ltf_corr, bias):
        sleep.sleep(60)
        continue

    if not liquidity_sweep(ltf_main, bias, key_high, key_low):
        sleep.sleep(60)
        continue

    if not displacement(ltf_main):
        sleep.sleep(60)
        continue

    fvg = fair_value_gap(ltf_main, bias)
    ob = order_block(ltf_main, bias)
    if not fvg or not ob:
        sleep.sleep(60)
        continue

    # Use OB as primary zone if available, else FVG
    entry_zone = ob if ob else fvg
    if not in_retrace(ltf_main, entry_zone, bias):
        sleep.sleep(60)
        continue

    entry = (min(entry_zone) + max(entry_zone)) / 2
    sl = min(entry_zone) if bias.startswith("BULLISH") else max(entry_zone)  # Invalidation at OB/FVG edge
    risk = abs(entry - sl)
    tp = key_high if bias.startswith("BULLISH") else key_low  # Next liquidity
    tp_dist = abs(entry - tp)
    if tp_dist / risk < MIN_RR:
        logging.info("Skipped: RR too low")
        sleep.sleep(60)
        continue

    place_trade("BUY" if bias.startswith("BULLISH") else "SELL", entry, sl, tp)

    sleep.sleep(300)  # Wait for next potential bar





