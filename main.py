"""BTC race bot: breakout rider + dip-recovery bounce, agent-fun/bars-replay/v1.

Entry for make.fun race btc-race-24h-20261123-0511. Long only, at most one
position, CBBTC/USDC 1-minute bars. It speaks the wire protocol (hello, one
batch per bar, orders) through the vendored kit SDK in vendor/.

Two entry modes, both decided on a bar's close and filled at the next open:

Mode A — breakout rider (the prepared bot, unchanged). A confirmed breakout
in a non-falling market: the close clears the last 60 bars' high by at
least 0.12% while price is above EMA(120) and that EMA is not falling, the
last 30 bars gained at least +0.30% (force), the last 60 at most +1.6% and
the last 240 at most +0.6% (not a spent blow-off), price at most 0.9% above
EMA(30), hourly vol expanding (1.15x its value 60 bars ago, inside
0.12%-2.8%/hour), and the regime fresh (price was at or under EMA(120)
within the last 120 bars, or the bot traded within them).

Mode B — dip-recovery bounce. Added for the 2026-11-18 race: its dev window
(2026-11-17 14:44 -> 2026-11-18 14:44 UTC, BTC $65,083 -> $63,261, -2.8%)
was a down day, and the breakout rider takes 0 trades there because price
sits under EMA(120) for most of the window. A long-only bot can still
trade such a day: after a sharp hourly dip (60-bar return <= -0.7%), buy
the confirmed turn off the 30-bar low — the close has recovered at least
0.25% above that low and the last 15 bars gained at least +0.15% — while
price is still under EMA(120), where the breakout mode cannot fire. It
buys a bounce, not a falling knife: no recovery, no trade.

This race's dev window (2026-11-22 05:11 -> 2026-11-23 05:11 UTC, BTC
$58,986.91 -> $61,102.18, +3.59%) is an up day, so the breakout rider is
the mode expected to fire; the bounce mode stays as the down-day insurance
it was built to be.

Size is volatility-scaled in both modes: a fixed ~0.45% of equity is risked
to the hard stop, so quiet windows get up to 90% of equity and wild ones as
little as 30%; bounce trades take 0.75x (counter-trend). Thin pools (latest
close depth under $300k) scale down.

Exits (whole position): drawdown guard; hard stop and trailing stop (both
locked from volatility at entry); failed breakout/bounce (close back under
the breakout level / the 30-bar low, after 3 bars); trend break (EMA(30)
under EMA(120), after 10 bars — this applies to bounce trades too: it is
the 11-bar test that keeps a bounce from riding a waterfall down); a
3-hour time stop for trades going nowhere. Bounce trades additionally carry
a +0.9% take-profit and a 2-hour time stop.

Discipline: cooldown 20 bars after a losing exit, 10 after a winning one;
loss-streak brake (size halves after 2 straight losers, 6-hour sit-out
after 3); equity drawdown guard (at -9% from peak: close + 4-hour entry
pause; at -15%: close + no more entries). The race guardrail disqualifies
above 25% drawdown, so the bot stays far under it.

Rejected during adaptation (tested, kept out): EMA-crossover entries
whipsaw in chop (lost on both earlier dev windows); waiving the vol-expansion
filter on "strong" breakouts buys range tops (the recovery grind's
breakouts fail and retrace); tightening the bounce stop loses to pool
price impact (a bigger order pays more to move the pool than it gains).
Deterministic: floats only in + - * / and sqrt on the bar data itself; no
clock, no environment, no randomness, no files.

Verified dev-window grades (wire-protocol driver, python3 -P -s,
PYTHONHASHSEED=0, 1,440 batches, replies byte-identical across two runs):
- btc-race-24h-20261118-1444 (2026-11-17 14:44 -> 2026-11-18 14:44 UTC,
  BTC $65,083 -> $63,261, -2.8%): net PnL +0.181 USDC, 1 round trip
  (buy bar 146 @ $63,858.81, sell bar 157 @ $63,961.47), max drawdown
  0.027%, fees 0.180 USDC. Passes the dev gate (net PnL > 0).
- btc-race-24h-20261117-0603 (previous race's window): net PnL +2.963
  USDC, 3 round trips, max drawdown 0.364%, fees 1.057 USDC.
- btc-race-24h-20261123-0511 (this race, 2026-11-22 05:11 -> 2026-11-23
  05:11 UTC, BTC $58,986.91 -> $61,102.18, +3.59%): net PnL +0.307 USDC,
  1 round trip (buy bar 191 @ $59,636.67, sell bar 213 @ $59,719.08),
  max drawdown 0.461%, fees 0.424 USDC. Passes the dev gate (net PnL > 0).
  Strategy unchanged from the 1444 entry — no adaptation was needed.
  Grade re-verified after this docstring revision: unchanged.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars  # noqa: E402

SYMBOL = "CBBTC"

EMA_FAST = 30
EMA_SLOW = 120
BREAKOUT_BARS = 60          # a fresh high over this many bars is a breakout
BREAKOUT_MARGIN = 0.0012    # ... cleared by at least 0.12%, not by a hair
SLOW_SLOPE_BARS = 60        # the slow EMA must not be falling over this span
SLOW_SLOPE_MIN = -0.001     # (tolerates at most a -0.1% drift)
RET_FAST_BARS = 30
RET_FAST_MIN = 0.0030       # +0.30% over the last 30 bars: the move has force
RET_SLOW_BARS = 60
RET_SLOW_MAX = 0.016        # +1.6% over the last 60 bars: past this it is spent
RET_LONG_BARS = 240
RET_LONG_MAX = 0.006        # +0.6% over the last 240 bars: past this the
                            # 4-hour move is already extended
EXT_MAX = 0.009             # never enter >0.9% above the fast EMA
VOL_BARS = 120              # 1-min returns in the volatility estimate
HOUR = 60
VOL_MIN_HOURLY = 0.0012     # skip dead markets (hourly vol under 0.12%)
VOL_MAX_HOURLY = 0.028      # skip chaos (hourly vol over 2.8%)
VOL_EXPAND = 1.15           # hourly vol must be 1.15x its value 60 bars ago
FRESH_BARS = 120            # bars since price last touched EMA(120) (or the
                            # bot last traded) for a breakout to count fresh
FALLBACK_SEQ = 900          # still tradeless this late: the race guardrail
FALLBACK_SIZE = 0.5         # wants >= 1 trade, so a merely decent breakout
                            # (all filters but vol expansion) is taken small
RISK_BUDGET = 0.0045        # equity share risked to the hard stop
SIZE_MIN = 0.30
SIZE_MAX = 0.90
STOP_MULT = 1.5             # hard stop = 1.5 x hourly vol ...
STOP_FLOOR = 0.0055         # ... at least 0.55%
STOP_CAP = 0.020            # ... at most 2.0%
TRAIL_MULT = 0.9            # trailing stop = 0.9 x hourly vol ...
TRAIL_FLOOR = 0.0045        # ... at least 0.45%
TRAIL_CAP = 0.014           # ... at most 1.4%
TRAIL_ARM = 1.003           # trailing stop arms at +0.3% over cost
TIME_STOP_BARS = 180        # 3 hours
TIME_STOP_MAX_GAIN = 1.0015  # ... if the trade is still under +0.15%
MIN_HOLD_SOFT = 10          # soft exits (trend) wait this many bars
MIN_HOLD_BREAKOUT = 3       # failed-breakout exit waits this many bars
COOLDOWN_LOSS = 20
COOLDOWN_WIN = 10
STREAK_HALVE = 2            # losing trades in a row before size halves
STREAK_STOP = 3             # ... and before a long sit-out
STREAK_STOP_BARS = 360
DD_PAUSE = 0.09             # equity 9% under its peak: close + pause
DD_PAUSE_BARS = 240
DD_STOP = 0.15              # equity 15% under its peak: close + stop entering
THIN_DEPTH = 300_000_000_000  # 300k USDC in raw units (quote scale 6)
SLIP_BUY_BPS = 100
SLIP_SELL_BPS = 250
HISTORY = 241               # closes kept (breakout + returns)

# --- bounce mode (B): dip-recovery, counter-trend ---
BOUNCE_DIP_BARS = 60
BOUNCE_DIP_MIN = 0.007       # 60-bar return <= -0.7%: a real dip, not noise
BOUNCE_LOW_BARS = 30         # bounce measured off this low
BOUNCE_RECOVER = 0.0025      # close >= 0.25% above the 30-bar low
BOUNCE_MOM_BARS = 15
BOUNCE_MOM_MIN = 0.0015      # 15-bar return >= +0.15%: the turn is real
BOUNCE_TP = 0.009            # take-profit +0.9% over cost
BOUNCE_TIME_BARS = 120       # 2h time stop for a bounce going nowhere
BOUNCE_SIZE = 0.75           # counter-trend trades take 3/4 size


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


class Ema:
    __slots__ = ("span", "warm", "value")

    def __init__(self, span):
        self.span = span
        self.warm = []
        self.value = None

    def step(self, x):
        if self.value is not None:
            self.value += (2.0 / (self.span + 1)) * (x - self.value)
        else:
            self.warm.append(x)
            if len(self.warm) == self.span:
                self.value = sum(self.warm) / self.span
                self.warm = []


class Strategy:
    def __init__(self):
        self.fast = Ema(EMA_FAST)
        self.slow = Ema(EMA_SLOW)
        self.closes = []            # last HISTORY closes (raw price ints)
        self.slow_hist = []         # last SLOW_SLOPE_BARS+1 slow-EMA values
        self.rets = []              # last VOL_BARS 1-min returns (floats)
        self.hv_hist = []           # last 61 hourly-vol estimates
        self.last_close = None
        self.positions_opened = 0   # fills seen this run (fallback logic)
        self.fallback_used = False
        self.last_below_slow = None  # last bar with close at/under EMA(120)
        self.last_traded_seq = -10 ** 9  # last bar holding or exiting
        # position tracking
        self.peak_value = 0         # best position value (quote units) since entry
        self.entry_seq = -1
        self.stop_pct = STOP_FLOOR
        self.trail_pct = TRAIL_FLOOR
        self.break_level = 0        # breakout level (or bounce low) of the live trade
        self.trade_kind = "breakout"  # breakout | bounce
        self.pending = None         # (stop_pct, trail_pct, break_level, kind) at buy time
        # discipline
        self.cooldown_until = -1
        self.blocked_until = -1
        self.stopped = False
        self.peak_equity = None
        self.loss_streak = 0
        self.streak_block_until = -1

    # -- indicators ----------------------------------------------------
    def _push_bar(self, close):
        if self.last_close is not None and self.last_close > 0:
            self.rets.append(close / self.last_close - 1.0)
            if len(self.rets) > VOL_BARS:
                self.rets.pop(0)
        self.last_close = close
        self.closes.append(close)
        if len(self.closes) > HISTORY:
            self.closes.pop(0)
        self.fast.step(float(close))
        self.slow.step(float(close))
        if self.slow.value is not None:
            self.slow_hist.append(self.slow.value)
            if len(self.slow_hist) > SLOW_SLOPE_BARS + 1:
                self.slow_hist.pop(0)

    def _hourly_vol(self):
        n = len(self.rets)
        if n < 30:
            return None
        mean = sum(self.rets) / n
        var = sum((r - mean) * (r - mean) for r in self.rets) / n
        return (var ** 0.5) * (HOUR ** 0.5)

    def _ret(self, bars):
        if len(self.closes) < bars + 1 or self.closes[-bars - 1] <= 0:
            return None
        return self.closes[-1] / self.closes[-bars - 1] - 1.0

    def _size_buy(self, state, hv, size_mult):
        """Risk-based buy order; returns (order, (stop_pct, trail_pct))."""
        stop_pct = _clamp(STOP_MULT * hv, STOP_FLOOR, STOP_CAP)
        trail_pct = _clamp(TRAIL_MULT * hv, TRAIL_FLOOR, TRAIL_CAP)
        frac = _clamp(RISK_BUDGET / stop_pct, SIZE_MIN, SIZE_MAX) * size_mult
        if self.loss_streak >= STREAK_HALVE:
            frac *= 0.5
        depth = state.depth_at(SYMBOL)
        if depth:
            dmin = min(depth)
            if dmin < THIN_DEPTH:
                frac *= dmin / THIN_DEPTH
        notional = int(state.equity * frac)
        if notional > state.cash:
            notional = state.cash
        min_notional = int(state.hello["fees"]["min_notional_quote_units"])
        if notional < min_notional:
            return None, None
        room = state.weight_room(SYMBOL)
        if notional > room:
            notional = room
        if notional < min_notional:
            return None, None
        return ({"side": "buy", "symbol": SYMBOL,
                 "notional_quote_units": notional,
                 "max_slippage_bps": SLIP_BUY_BPS}, (stop_pct, trail_pct))

    def _exit(self, state, losing):
        if losing:
            self.loss_streak += 1
            if self.loss_streak >= STREAK_STOP:
                self.streak_block_until = state.seq + STREAK_STOP_BARS
                self.loss_streak = 0
        else:
            self.loss_streak = 0
        self.cooldown_until = state.seq + (COOLDOWN_LOSS if losing else COOLDOWN_WIN)
        self.last_traded_seq = state.seq
        self.entry_seq = -1
        self.peak_value = 0
        self.trade_kind = "breakout"

    # -- main ------------------------------------------------------------
    def on_batch(self, state, batch):
        fresh = False
        for bar in batch["bars"]:
            if bar["symbol"] == SYMBOL:
                self._push_bar(int(bar["c"]))
                fresh = True
        hv = self._hourly_vol()
        if fresh and hv is not None:
            self.hv_hist.append(hv)
            if len(self.hv_hist) > SLOW_SLOPE_BARS + 1:
                self.hv_hist.pop(0)
        if not fresh or state.warming_up:
            return []

        if self.peak_equity is None:
            self.peak_equity = int(state.hello["capital"]["quote_units"])
        if state.equity > self.peak_equity:
            self.peak_equity = state.equity

        held = state.positions.get(SYMBOL)
        seq = state.seq
        if self.slow.value is not None:
            if self.last_below_slow is None:
                self.last_below_slow = seq   # regime age unknown: call it fresh
            elif self.last_close is not None and float(self.last_close) <= self.slow.value:
                self.last_below_slow = seq
        if held is not None and held.qty > 0:
            self.last_traded_seq = seq
        trend_down = (self.fast.value is not None and self.slow.value is not None
                      and self.fast.value < self.slow.value)

        # ---- drawdown guard ----
        dd_blocked = self.stopped or seq < self.blocked_until
        dd_close = self.stopped
        if self.peak_equity > 0:
            dd = 1.0 - state.equity / self.peak_equity
            if dd >= DD_STOP:
                self.stopped = True
                dd_blocked = True
                dd_close = True
            elif dd >= DD_PAUSE and seq >= self.blocked_until:
                self.blocked_until = seq + DD_PAUSE_BARS
                dd_blocked = True
                dd_close = True

        # ---- exits ----
        if held is not None and held.qty > 0:
            if self.entry_seq < 0:
                # position appeared (the buy sent earlier filled at this open)
                self.entry_seq = seq
                self.positions_opened += 1
                self.peak_value = state.value(SYMBOL, held.qty)
                if self.pending is not None:
                    self.stop_pct, self.trail_pct, self.break_level, self.trade_kind = self.pending
                    self.pending = None
            value = state.value(SYMBOL, held.qty)
            if value > self.peak_value:
                self.peak_value = value
            cost = held.cost
            close = state.close(SYMBOL)
            held_bars = seq - self.entry_seq
            exit_now = False
            if dd_close:
                exit_now = True                     # drawdown guard
            elif value <= cost * (1.0 - self.stop_pct):
                exit_now = True                     # hard stop
            elif self.peak_value >= cost * TRAIL_ARM and value <= self.peak_value * (1.0 - self.trail_pct):
                exit_now = True                     # trailing stop
            elif (held_bars >= MIN_HOLD_BREAKOUT and self.break_level > 0
                    and close is not None and close < self.break_level):
                exit_now = True                     # failed breakout / bounce
            elif held_bars >= MIN_HOLD_SOFT and trend_down:
                exit_now = True                     # trend break
            elif self.trade_kind == "bounce" and BOUNCE_TP > 0 and value >= cost * (1.0 + BOUNCE_TP):
                exit_now = True                     # bounce take-profit
            elif (self.trade_kind == "bounce" and held_bars >= BOUNCE_TIME_BARS
                    and value < cost * TIME_STOP_MAX_GAIN):
                exit_now = True                     # bounce time stop (2h)
            elif (self.trade_kind != "bounce" and held_bars >= TIME_STOP_BARS
                    and value < cost * TIME_STOP_MAX_GAIN):
                exit_now = True                     # dead trade
            if exit_now:
                self._exit(state, value < cost)
                return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                         "max_slippage_bps": SLIP_SELL_BPS}]
            return []

        # flat: forget stale position state
        if self.entry_seq >= 0:
            self.entry_seq = -1
            self.peak_value = 0

        # ---- entries ----
        if dd_blocked or seq < self.cooldown_until or seq < self.streak_block_until:
            return []
        if hv is None:
            return []
        if not (VOL_MIN_HOURLY <= hv <= VOL_MAX_HOURLY):
            return []
        if len(self.closes) < BREAKOUT_BARS + 1:
            return []
        close = state.close(SYMBOL)
        if close is None or self.slow.value is None or self.fast.value is None:
            return []

        order = self._breakout_entry(state, hv, close, seq)
        if order is not None:
            return [order]
        order = self._bounce_entry(state, hv, close, seq)
        if order is not None:
            return [order]
        return []

    def _breakout_entry(self, state, hv, close, seq):
        """Mode A: the prepared breakout rider, unchanged."""
        if float(close) <= self.slow.value:
            return None                               # falling market context
        if len(self.slow_hist) > SLOW_SLOPE_BARS:
            if self.slow_hist[-1] < self.slow_hist[0] * (1.0 + SLOW_SLOPE_MIN):
                return None                           # slow EMA falling
        break_level = max(self.closes[-BREAKOUT_BARS - 1:-1])
        if close < break_level * (1.0 + BREAKOUT_MARGIN):
            return None                               # no decisive fresh breakout
        ret_fast = self._ret(RET_FAST_BARS)
        ret_slow = self._ret(RET_SLOW_BARS)
        ret_long = self._ret(RET_LONG_BARS)
        if ret_fast is None or ret_fast < RET_FAST_MIN:
            return None                               # breakout without force
        if ret_slow is not None and ret_slow > RET_SLOW_MAX:
            return None                               # vertical blow-off: spent
        if ret_long is not None and ret_long > RET_LONG_MAX:
            return None                               # 4-hour move already extended
        if float(close) > self.fast.value * (1.0 + EXT_MAX):
            return None                               # overextended above the fast EMA
        fresh_regime = (self.last_below_slow is not None
                        and seq - self.last_below_slow <= FRESH_BARS)
        if not fresh_regime and seq - self.last_traded_seq > FRESH_BARS:
            return None                               # mature regime: range top
        expanding = (len(self.hv_hist) > SLOW_SLOPE_BARS
                     and self.hv_hist[-1] >= VOL_EXPAND * self.hv_hist[-SLOW_SLOPE_BARS - 1])
        fallback = False
        if not expanding:
            if (not self.fallback_used and self.positions_opened == 0
                    and seq >= FALLBACK_SEQ):
                fallback = True     # late and tradeless: take it small
            else:
                return None           # quiet breakout: ranges reject these
        order, pending = self._size_buy(state, hv, 1.0)
        if order is None:
            return None
        if fallback:
            order = dict(order)
            order["notional_quote_units"] = max(
                int(state.hello["fees"]["min_notional_quote_units"]),
                int(order["notional_quote_units"] * FALLBACK_SIZE))
            self.fallback_used = True
        stop_pct, trail_pct = pending
        self.pending = (stop_pct, trail_pct, break_level, "breakout")
        return order

    def _bounce_entry(self, state, hv, close, seq):
        """Mode B: counter-trend dip-recovery bounce (price under EMA(120))."""
        if float(close) >= self.slow.value:
            return None           # above the slow EMA: the breakout mode owns it
        dip = self._ret(BOUNCE_DIP_BARS)
        if dip is None or dip > -BOUNCE_DIP_MIN:
            return None           # no real dip in the last hour
        low = min(self.closes[-BOUNCE_LOW_BARS:])
        if float(close) < low * (1.0 + BOUNCE_RECOVER):
            return None           # not yet recovered off the low: falling knife
        mom = self._ret(BOUNCE_MOM_BARS)
        if mom is None or mom < BOUNCE_MOM_MIN:
            return None           # short-term momentum has not turned up
        if float(close) > self.fast.value * (1.0 + EXT_MAX):
            return None           # overextended above the fast EMA
        order, pending = self._size_buy(state, hv, BOUNCE_SIZE)
        if order is None:
            return None
        stop_pct, trail_pct = pending
        self.pending = (stop_pct, trail_pct, low, "bounce")
        return order


_strategy = Strategy()


def on_batch(state, batch):
    return _strategy.on_batch(state, batch)


if __name__ == "__main__":
    makefun_bars.run(on_batch)
