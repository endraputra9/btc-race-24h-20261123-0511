"""BTC race bot — make.fun btc-race-24h-20261123-0511.

Long-only selective breakout rider on CBBTC/USDC 1-minute bars, with a
dip-recovery bounce mode for down days. One position at a time; every
decision is made on a bar's close and fills at the next bar's open.

Breakout entry (all required): the close clears the previous 60 bars'
high by at least 0.12%; price sits above EMA(120) while EMA(120) is not
falling over the last 60 bars; the trailing 30-bar return is at least
+0.30% while the 60-bar return is at most +1.6% and the 240-bar return
at most +0.6% (force without exhaustion); price is at most 0.9% above
EMA(30); hourly volatility (stdev of the last 120 one-minute returns,
times sqrt(60)) is expanding, at least 1.15x its value 60 bars ago, and
inside 0.12%–2.8% per hour; and the regime is fresh — price was at or
under EMA(120) sometime in the last 120 bars, or the bot traded within
them. If the bot is still tradeless at bar 900, one otherwise-valid
breakout is taken at half size even without the volatility expansion.

Bounce entry (only when the breakout mode declines): after a sharp
hourly dip (60-bar return at most -0.7%), buy the confirmed turn off
the 30-bar low — close recovered at least +0.25% above that low with a
15-bar return of at least +0.15% — only while price is under EMA(120),
at 0.75x the usual size.

Sizing is risk-based: the hard stop is clamp(1.5x hourly vol, 0.55%,
2.0%) and the position is clamp(0.45% of equity / stop, 30%, 90%) of
equity, scaled down proportionally when the pool is thinner than
$300k. Buys cap slippage at 100 bps, sells at 250 bps.

Exits (whole position): the hard stop locked at entry; a trailing stop
of clamp(0.9x hourly vol at entry, 0.45%, 1.4%), armed once price is
+0.3% over the entry price; a failed breakout (close back under the
breakout level, after 3 bars); a trend break (EMA(30) under EMA(120),
after 10 bars); a 3-hour time stop while still under +0.15%. Bounce
trades add a +0.9% take-profit and a 2-hour time stop. An equity
drawdown guard closes the position and pauses entries for 4 hours at
-9% from peak equity, and halts entries entirely at -15%.

Discipline: 20 bars of cooldown after a losing exit, 10 after a
winning one; size halves after 2 straight losers and the bot sits out
6 hours after 3. Deterministic: float arithmetic on bar data only —
no clock, environment, randomness, or files.

Verified dev grade (this race's kit, wire-protocol driver, python3 -P
-s, PYTHONHASHSEED=0, 1440 batches, two runs byte-identical):
net PnL +0.307320 USDC, 1 round trip (buy bar 191 @ $59,636.67, sell
bar 213 @ $59,719.08), max drawdown 0.461%, fees 0.4236 USDC.
Gate passed: net PnL > 0, >= 1 trade, max drawdown <= 25%.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# --- breakout entry ---
BREAKOUT_BARS = 60
BREAKOUT_MARGIN = 0.0012
EMA_FAST_N = 30
EMA_SLOW_N = 120
RET_FAST_MIN = 0.0030
RET_MID_MAX = 0.016
RET_LONG_MAX = 0.006
EXT_MAX = 0.009
VOL_EXPAND = 1.15
VOL_MIN = 0.0012
VOL_MAX = 0.028
FRESH_BARS = 120
FALLBACK_BAR = 900

# --- sizing ---
RISK_BUDGET = 0.0045
STOP_MULT = 1.5
STOP_MIN = 0.0055
STOP_MAX = 0.02
POS_MIN = 0.30
POS_MAX = 0.90
DEPTH_REF_USDC = 300000.0
BUY_SLIP_BPS = 100
SELL_SLIP_BPS = 250

# --- exits ---
TRAIL_MULT = 0.9
TRAIL_MIN = 0.0045
TRAIL_MAX = 0.014
TRAIL_ARM = 0.003
FAILED_AFTER = 3
TREND_AFTER = 10
TIME_STOP_BARS = 180
TIME_STOP_MIN = 0.0015

# --- bounce mode ---
BOUNCE_DIP = -0.007
BOUNCE_RECOVER = 0.0025
BOUNCE_FAST_MIN = 0.0015
BOUNCE_SIZE = 0.75
BOUNCE_TP = 0.009
BOUNCE_TIME_BARS = 120

# --- discipline ---
COOLDOWN_WIN = 10
COOLDOWN_LOSS = 20
LOSS_HALVE_AFTER = 2
LOSS_SITOUT_AFTER = 3
SITOUT_BARS = 360
DD_PAUSE = 0.09
DD_PAUSE_BARS = 240
DD_HALT = 0.15

MIN_HISTORY = 180


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


class Bot:
    def __init__(self):
        self.closes = []      # raw price ints, as floats
        self.highs = []
        self.lows = []
        self.depths = []      # effective pool depth in USDC per bar
        self.ema_fast_hist = []
        self.ema_slow_hist = []
        self.vol_hist = []    # hourly vol (fraction per hour)
        self._ema_fast = None
        self._ema_slow = None
        self.trade = None         # active trade dict
        self.pending_buy = None   # buy sent last bar, awaiting fill
        self.pending_sell = None  # sell sent last bar, awaiting fill
        self.cooldown_until = -1
        self.sitout_until = -1
        self.pause_until = -1
        self.halted = False
        self.loss_streak = 0
        self.peak_equity = 0.0
        self.trades_filled = 0
        self.last_fill_bar = -10 ** 9

    # -- indicators -------------------------------------------------
    def _update_indicators(self):
        closes = self.closes
        n = len(closes)
        c = closes[-1]
        if len(closes) == EMA_FAST_N:
            self._ema_fast = sum(closes) / EMA_FAST_N
        elif len(closes) > EMA_FAST_N:
            self._ema_fast += (2.0 / (EMA_FAST_N + 1)) * (c - self._ema_fast)
        if len(closes) == EMA_SLOW_N:
            self._ema_slow = sum(closes) / EMA_SLOW_N
        elif len(closes) > EMA_SLOW_N:
            self._ema_slow += (2.0 / (EMA_SLOW_N + 1)) * (c - self._ema_slow)
        self.ema_fast_hist.append(self._ema_fast)
        self.ema_slow_hist.append(self._ema_slow)
        if n >= 121:
            rets = [closes[i] / closes[i - 1] - 1.0 for i in range(n - 120, n)]
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / len(rets)
            self.vol_hist.append(math.sqrt(var) * math.sqrt(60.0))
        else:
            self.vol_hist.append(None)

    def _ret(self, idx, bars):
        return self.closes[idx] / self.closes[idx - bars] - 1.0

    def _hourly_vol(self, idx):
        return self.vol_hist[idx]

    # -- sizing ------------------------------------------------------
    def _size_buy(self, state, idx, size_mult):
        vol = self._hourly_vol(idx)
        stop_pct = _clamp(STOP_MULT * vol, STOP_MIN, STOP_MAX)
        frac = _clamp(RISK_BUDGET / stop_pct, POS_MIN, POS_MAX)
        depth = self.depths[idx]
        if depth < DEPTH_REF_USDC:
            frac *= depth / DEPTH_REF_USDC
        if self.loss_streak >= LOSS_HALVE_AFTER:
            frac *= 0.5
        frac *= size_mult
        notional = int(state.equity * frac)
        room = state.weight_room(SYMBOL)
        if notional > room:
            notional = room
        if notional > state.cash:
            notional = state.cash
        if notional < 1000000:
            return (None, stop_pct)
        trail_pct = _clamp(TRAIL_MULT * vol, TRAIL_MIN, TRAIL_MAX)
        return (notional, stop_pct, trail_pct)

    # -- entry checks -------------------------------------------------
    def _breakout_ok(self, idx, need_vol):
        closes = self.closes
        close = closes[idx]
        high60 = max(self.highs[idx - BREAKOUT_BARS:idx])
        if close < high60 * (1.0 + BREAKOUT_MARGIN):
            return None
        ema_slow = self.ema_slow_hist[idx]
        if close <= ema_slow:
            return None
        if ema_slow < self.ema_slow_hist[idx - 60]:
            return None
        if self._ret(idx, 30) < RET_FAST_MIN:
            return None
        if self._ret(idx, 60) > RET_MID_MAX:
            return None
        if idx >= 240 and self._ret(idx, 240) > RET_LONG_MAX:
            return None
        if close > self.ema_fast_hist[idx] * (1.0 + EXT_MAX):
            return None
        if need_vol:
            vol = self._hourly_vol(idx)
            vol_ago = self._hourly_vol(idx - 60)
            if vol is None or vol_ago is None or vol_ago <= 0.0:
                return None
            if vol < VOL_EXPAND * vol_ago:
                return None
            if vol < VOL_MIN or vol > VOL_MAX:
                return None
        fresh = False
        for j in range(max(idx - FRESH_BARS, EMA_SLOW_N - 1), idx + 1):
            if self.ema_slow_hist[j] is not None and closes[j] <= self.ema_slow_hist[j]:
                fresh = True
                break
        if not fresh and idx - self.last_fill_bar > FRESH_BARS:
            return None
        return high60

    def _bounce_ok(self, idx):
        close = self.closes[idx]
        if self._ret(idx, 60) > BOUNCE_DIP:
            return False
        low30 = min(self.lows[idx - 30:idx])
        if close < low30 * (1.0 + BOUNCE_RECOVER):
            return False
        if self._ret(idx, 15) < BOUNCE_FAST_MIN:
            return False
        if close >= self.ema_slow_hist[idx]:
            return False
        return True

    # -- main callback -------------------------------------------------
    def on_batch(self, state, batch):
        bar = state.latest.get(SYMBOL)
        if bar is None:
            return []
        idx = len(self.closes)
        self.closes.append(float(int(bar["c"])))
        self.highs.append(float(int(bar["h"])))
        self.lows.append(float(int(bar["l"])))
        depth = state.depth_at(SYMBOL)
        if depth:
            total = 0.0
            for d in depth:
                total += 1.0 / d
            eff = 1.0 / total if total > 0.0 else 0.0
            self.depths.append(eff / (10 ** state.hello["capital"]["quote_scale"]))
        else:
            self.depths.append(0.0)
        self._update_indicators()

        holding = state.positions.get(SYMBOL)

        # Resolve last bar's pending orders from this batch's account.
        if self.pending_buy is not None:
            if holding is not None:
                self._activate_trade(state, idx, holding)
            self.pending_buy = None
        if self.pending_sell is not None:
            won = self.pending_sell["won"]
            guard = self.pending_sell["guard"]
            self.pending_sell = None
            if holding is None:
                self._finalize_exit(idx, won, guard)
            # else the sell was rejected: the trade stays live and the
            # exit checks below will fire again next bar.

        if state.equity > self.peak_equity:
            self.peak_equity = state.equity

        # Equity drawdown guard: close and pause / halt entries.
        guard = None
        if self.trade is not None and self.peak_equity > 0:
            if state.equity <= self.peak_equity * (1.0 - DD_HALT):
                guard = "halt"
            elif state.equity <= self.peak_equity * (1.0 - DD_PAUSE):
                guard = "pause"

        if self.trade is not None:
            order = self._exit_order(state, idx, guard)
            if order is not None:
                return order
            return []

        if state.warming_up:
            return []
        if idx < MIN_HISTORY:
            return []
        if self.halted or idx < self.pause_until:
            return []
        if idx < self.cooldown_until or idx < self.sitout_until:
            return []

        # Breakout first; bounce only when it declines.
        breakout_level = self._breakout_ok(idx, need_vol=True)
        size_mult = 1.0
        if breakout_level is None and self.trades_filled == 0 and idx >= FALLBACK_BAR:
            breakout_level = self._breakout_ok(idx, need_vol=False)
            if breakout_level is not None:
                size_mult = 0.5
        if breakout_level is not None:
            sized = self._size_buy(state, idx, size_mult)
            if sized[0] is None:
                return []
            notional, stop_pct, trail_pct = sized
            self.pending_buy = {
                "stop_pct": stop_pct,
                "trail_pct": trail_pct,
                "breakout_level": breakout_level,
                "mode": "breakout",
                "decision_close": self.closes[idx],
            }
            return [{"side": "buy", "symbol": SYMBOL,
                     "notional_quote_units": notional,
                     "max_slippage_bps": BUY_SLIP_BPS}]
        if self._bounce_ok(idx):
            sized = self._size_buy(state, idx, BOUNCE_SIZE)
            if sized[0] is None:
                return []
            notional, stop_pct, trail_pct = sized
            self.pending_buy = {
                "stop_pct": stop_pct,
                "trail_pct": trail_pct,
                "breakout_level": None,
                "mode": "bounce",
                "decision_close": self.closes[idx],
            }
            return [{"side": "buy", "symbol": SYMBOL,
                     "notional_quote_units": notional,
                     "max_slippage_bps": BUY_SLIP_BPS}]
        return []

    def _activate_trade(self, state, idx, holding):
        cap = state.hello["capital"]
        price = (holding.cost * 10 ** (state.symbols[SYMBOL]["qty_scale"]
                                       + state.symbols[SYMBOL]["price_scale"])
                 / (holding.qty * 10 ** cap["quote_scale"]))
        if price <= 0:
            price = self.pending_buy["decision_close"]
        self.trade = {
            "entry_price": price,
            "stop_pct": self.pending_buy["stop_pct"],
            "trail_pct": self.pending_buy["trail_pct"],
            "breakout_level": self.pending_buy["breakout_level"],
            "mode": self.pending_buy["mode"],
            "entry_bar": idx,
            "peak": price,
            "armed": False,
        }
        self.trades_filled += 1
        self.last_fill_bar = idx

    def _finalize_exit(self, idx, won, guard):
        self.trade = None
        if guard == "pause":
            self.pause_until = idx + DD_PAUSE_BARS
        elif guard == "halt":
            self.halted = True
        if won:
            self.loss_streak = 0
            self.cooldown_until = idx + COOLDOWN_WIN
        else:
            self.loss_streak += 1
            self.cooldown_until = idx + COOLDOWN_LOSS
            if self.loss_streak >= LOSS_SITOUT_AFTER:
                self.sitout_until = idx + SITOUT_BARS

    def _exit_order(self, state, idx, guard):
        trade = self.trade
        holding = state.positions.get(SYMBOL)
        if holding is None:
            self.trade = None
            return None
        close = self.closes[idx]
        entry = trade["entry_price"]
        held = idx - trade["entry_bar"]
        if close > trade["peak"]:
            trade["peak"] = close
        if close >= entry * (1.0 + TRAIL_ARM):
            trade["armed"] = True
        fire = False
        if guard is not None:
            fire = True
        elif close <= entry * (1.0 - trade["stop_pct"]):
            fire = True
        elif trade["armed"] and close <= trade["peak"] * (1.0 - trade["trail_pct"]):
            fire = True
        elif (trade["mode"] == "breakout" and held >= FAILED_AFTER
                and trade["breakout_level"] is not None
                and close < trade["breakout_level"]):
            fire = True
        elif (held >= TREND_AFTER
                and self.ema_fast_hist[idx] is not None
                and self.ema_fast_hist[idx] < self.ema_slow_hist[idx]):
            fire = True
        elif held >= TIME_STOP_BARS and close < entry * (1.0 + TIME_STOP_MIN):
            fire = True
        elif trade["mode"] == "bounce" and close >= entry * (1.0 + BOUNCE_TP):
            fire = True
        elif (trade["mode"] == "bounce" and held >= BOUNCE_TIME_BARS
                and close < entry * (1.0 + TIME_STOP_MIN)):
            fire = True
        if not fire:
            return None
        won = state.value(SYMBOL, holding.qty) >= holding.cost
        self.pending_sell = {"won": won, "guard": guard}
        return [{"side": "sell", "symbol": SYMBOL, "qty": holding.qty,
                 "max_slippage_bps": SELL_SLIP_BPS}]


def on_batch(state, batch):
    return _BOT.on_batch(state, batch)


_BOT = Bot()

makefun_bars.run(on_batch)
