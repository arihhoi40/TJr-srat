import MetaTrader5 as mt5
import pandas as pd
import time

# ================== SETTINGS ==================
SYMBOL = "XAUUSDm"
TF = mt5.TIMEFRAME_M1

RISK_PERCENT = 0.7
RR = 0.8
MAX_SPREAD = 60
COOLDOWN = 20
MAGIC = 55999

last_trade_time = 0

# ================== INIT ==================
if not mt5.initialize():
    raise RuntimeError("MT5 init failed")

# ================== DATA ==================
def get_df(tf, bars=100):
    rates = mt5.copy_rates_from_pos(SYMBOL, tf, 0, bars)
    df = pd.DataFrame(rates)
    return df

# ================== SPREAD ==================
def spread_ok():
    tick = mt5.symbol_info_tick(SYMBOL)
    return (tick.ask - tick.bid) <= MAX_SPREAD * mt5.symbol_info(SYMBOL).point

# ================== MICRO STRUCTURE ==================
def bias(df):
    if df.close.iloc[-1] > df.open.iloc[-1]:
        return "BUY"
    if df.close.iloc[-1] < df.open.iloc[-1]:
        return "SELL"
    return None

# ================== DISPLACEMENT ==================
def displacement(df):
    body = abs(df.close.iloc[-1] - df.open.iloc[-1])
    avg = abs(df.close - df.open).rolling(10).mean().iloc[-1]
    return body > avg * 1.1

# ================== LOT ==================
def lot_size(sl_dist):
    acc = mt5.account_info()
    risk = acc.balance * (RISK_PERCENT / 100)
    tick_val = mt5.symbol_info(SYMBOL).trade_tick_value
    return round(risk / (sl_dist * tick_val), 2)

# ================== EXECUTION ==================
def place_trade(direction):
    tick = mt5.symbol_info_tick(SYMBOL)
    entry = tick.ask if direction == "BUY" else tick.bid

    df = get_df(TF)
    sl = df.low.iloc[-2] if direction == "BUY" else df.high.iloc[-2]

    risk = abs(entry - sl)
    if risk == 0:
        return

    tp = entry + risk * RR if direction == "BUY" else entry - risk * RR
    vol = lot_size(risk)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": vol,
        "type": mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": entry,
        "sl": sl,
        "tp": tp,
        "magic": MAGIC,
        "type_filling": mt5.ORDER_FILLING_IOC,
        "type_time": mt5.ORDER_TIME_GTC
    }

    mt5.order_send(request)

# ================== FAST BE ==================
def manage_be():
    positions = mt5.positions_get()
    if not positions:
        return

    tick = mt5.symbol_info_tick(SYMBOL)

    for p in positions:
        if p.magic != MAGIC:
            continue

        r = abs(p.price_open - p.sl)

        if p.type == mt5.ORDER_TYPE_BUY and tick.bid >= p.price_open + r * 0.3:
            mt5.order_send({
                "action": mt5.TRADE_ACTION_SLTP,
                "position": p.ticket,
                "sl": p.price_open
            })

        if p.type == mt5.ORDER_TYPE_SELL and tick.ask <= p.price_open - r * 0.3:
            mt5.order_send({
                "action": mt5.TRADE_ACTION_SLTP,
                "position": p.ticket,
                "sl": p.price_open
            })

# ================== MAIN LOOP ==================
print("AGGRESSIVE XAUUSD SCALPER RUNNING")

while True:
    manage_be()

    now = time.time()
    positions = mt5.positions_get()
    positions = [p for p in positions if p.magic == MAGIC]

    if positions:
        time.sleep(3)
        continue

    if now - last_trade_time < COOLDOWN:
        time.sleep(1)
        continue

    if not spread_ok():
        time.sleep(1)
        continue

    df = get_df(TF)

    direction = bias(df)
    if not direction:
        time.sleep(1)
        continue

    if not displacement(df):
        time.sleep(1)
        continue

    place_trade(direction)
    last_trade_time = time.time()

    time.sleep(2)
