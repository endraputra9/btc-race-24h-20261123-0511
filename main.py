"""BTC race bot: breakout rider + dip-recovery bounce, agent-fun/bars-replay/v1.

Entry for make.fun race btc-race-24h-20261123-0511. Long only, at most one
position, CBBTC/USDC 1-minute bars. It speaks the wire protocol (hello, one
batch per bar, orders) through the vendored kit SDK in vendor/.

Structure of this file (restructured 2026-11-23 in a fresh build session;
same parameters, same thresholds, same order logic, same fills — a pure
reorganisation of the entry that was graded for this race, re-verified
below to produce the identical grade):

- ``CFG`` — every tunable number in one place, grouped by what it governs.
- Module helpers — ``_trailing_return`` (N-bar return off the close
  history) and ``_hourly_volatility`` (stdev of 1-minute returns, scaled
  to an hour); the pure arithmetic the strategy stands on.
- ``Ema`` — exponential moving average with an SMA warm-up over the first
  ``span`` closes; the smoothing factor is fixed at construction.
- ``Strategy`` — the state machine. ``on_batch`` is only the orchestrator:
  it ingests the bar, refreshes the volatility history, tracks the equity
  peak and the market regime, applies the drawdown guard, then hands off
  to ``_manage_position`` (exits) or ``_try_entries`` (entries).
  Entry evaluation is split into ``_breakout_entry`` (mode A) and
  ``_bounce_entry`` (mode B); exit evaluation lives in
  ``_exit_triggered``; sizing lives in ``_risk_sized_buy``.

Two entry modes, both decided on a bar's close and filled at the next open:

Mode A — breakout rider (the prepared bot). A confirmed breakout in a
non-falling market: the close clears the last 60 bars' high by at least
0.12% while price is above EMA(120) and that EMA is not falling, the last
30 bars gained at least +0.30% (force), the last 60 at most +1.6% and the
last 240 at most +0.6% (not a spent blow-off), price at most 0.9% above
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
  Grade re-verified again after the 2026-11-23 restructure in a fresh
  build session (parameters, thresholds, order logic and fills all
  preserved; only the code organisation changed): identical — wire run
  net PnL +0.307320 USDC, 1 round trip (buy bar 191 @ $59,636.67, sell
  bar 213 @ $59,719.08), max drawdown 0.461%, fees 0.4236 USDC, replies
  byte-identical across two runs, in-process sim agrees to the micro-USDC.
  Final docstring touch-up after that re-grade: grade unchanged.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars  # noqa: E402


class CFG:
    """Every tunable number, grouped by what it governs.

    Values are the ones validated on the 0603, 1444 and 0511 dev windows.
    Nothing here is a guess: each number earned its place in the
    sensitivity sweep and the rejected-idea tests recorded in STRATEGY.md.
    """

    SYMBOL = "CBBTC"

    # -- indicator spans -------------------------------------------------
    EMA_FAST = 30
    EMA_SLOW = 120
    VOL_BARS = 120              # 1-min returns in the volatility estimate
    HOUR = 60                   # bars per hour (1-minute bars)
    HISTORY = 241               # closes kept (breakout + returns)

    # -- breakout entry (mode A) -----------------------------------------
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

    # -- volatility gate --------------------------------------------------
    VOL_MIN_HOURLY = 0.0012     # skip dead markets (hourly vol under 0.12%)
    VOL_MAX_HOURLY = 0.028      # skip chaos (hourly vol over 2.8%)
    VOL_EXPAND = 1.15           # hourly vol must be 1.15x its value 60 bars ago

    # -- regime freshness + the late-race fallback ------------------------
    FRESH_BARS = 120            # bars since price last touched EMA(120) (or the
                                # bot last traded) for a breakout to count fresh
    FALLBACK_SEQ = 900          # still tradeless this late: the race guardrail
    FALLBACK_SIZE = 0.5         # wants >= 1 trade, so a merely decent breakout
                                # (all filters but vol expansion) is taken small

    # -- sizing, stops, slippage ------------------------------------------
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
    THIN_DEPTH = 300_000_000_000  # 300k USDC in raw units (quote scale 6)
    SLIP_BUY_BPS = 100
    SLIP_SELL_BPS = 250

    # -- discipline: cooldowns, loss streaks, drawdown --------------------
    COOLDOWN_LOSS = 20
    COOLDOWN_WIN = 10
    STREAK_HALVE = 2            # losing trades in a row before size halves
    STREAK_STOP = 3             # ... and before a long sit-out
    STREAK_STOP_BARS = 360
    DD_PAUSE = 0.09             # equity 9% under its peak: close + pause
    DD_PAUSE_BARS = 240
    DD_STOP = 0.15              # equity 15% under its peak: close + stop entering

    # -- bounce entry (mode B): dip-recovery, counter-trend ---------------
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


def _trailing_return(closes, bars):
    """Return of the latest close over the close ``bars`` bars ago."""
    if len(closes) < bars + 1 or closes[-bars - 1] <= 0:
        return None
    return closes[-1] / closes[-bars - 1] - 1.0


def _hourly_volatility(returns, bars_per_hour):
    """Stdev of the 1-minute returns, scaled to an hourly volatility."""
    n = len(returns)
    if n < 30:
        return None
    mean = sum(returns) / n
    var = sum((r - mean) * (r - mean) for r in returns) / n
    return (var ** 0.5) * (bars_per_hour ** 0.5)


class Ema:
    """Exponential moving average with an SMA warm-up.

    The first ``span`` closes are averaged (plain mean); from then on each
    close nudges the value by ``alpha`` of the gap to it. ``alpha`` is the
    textbook 2 / (span + 1), fixed once at construction so every step uses
    the identical factor.
    """

    __slots__ = ("span", "alpha", "warm", "value")

    def __init__(self, span):
        self.span = span
        self.alpha = 2.0 / (span + 1)
        self.warm = []
        self.value = None

    def step(self, x):
        if self.value is not None:
            self.value += self.alpha * (x - self.value)
        else:
            self.warm.append(x)
            if len(self.warm) == self.span:
                self.value = sum(self.warm) / self.span
                self.warm = []


class Strategy:
    """One-position, long-only state machine over the CBBTC 1-minute bars."""

    def __init__(self):
        self.fast = Ema(CFG.EMA_FAST)
        self.slow = Ema(CFG.EMA_SLOW)
        self.close_hist = []        # last HISTORY closes (raw price ints)
        self.slow_ema_hist = []     # last SLOW_SLOPE_BARS+1 slow-EMA values
        self.minute_rets = []       # last VOL_BARS 1-min returns (floats)
        self.vol_hist = []          # last 61 hourly-vol estimates
        self.prev_close = None
        self.fills_seen = 0         # fills seen this run (fallback logic)
        self.fallback_used = False
        self.last_below_slow_seq = None  # last bar with close at/under EMA(120)
        self.last_trade_seq = -10 ** 9   # last bar holding or exiting
        # live position
        self.peak_position_value = 0  # best position value (quote units) since entry
        self.entry_bar = -1
        self.stop_pct = CFG.STOP_FLOOR
        self.trail_pct = CFG.TRAIL_FLOOR
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

    # -- bar ingestion ----------------------------------------------------
    def _record_return(self, close):
        """Append this bar's 1-minute return; keep only the last VOL_BARS."""
        if self.prev_close is not None and self.prev_close > 0:
            self.minute_rets.append(close / self.prev_close - 1.0)
            if len(self.minute_rets) > CFG.VOL_BARS:
                self.minute_rets.pop(0)

    def _step_emas(self, close):
        """Feed the close to both EMAs and remember the slow EMA's path."""
        self.fast.step(float(close))
        self.slow.step(float(close))
        if self.slow.value is not None:
            self.slow_ema_hist.append(self.slow.value)
            if len(self.slow_ema_hist) > CFG.SLOW_SLOPE_BARS + 1:
                self.slow_ema_hist.pop(0)

    def _ingest_bar(self, close):
        """Fold one close into every indicator the strategy reads."""
        self._record_return(close)
        self.prev_close = close
        self.close_hist.append(close)
        if len(self.close_hist) > CFG.HISTORY:
            self.close_hist.pop(0)
        self._step_emas(close)

    def _hourly_vol(self):
        return _hourly_volatility(self.minute_rets, CFG.HOUR)

    # -- sizing ------------------------------------------------------------
    def _risk_sized_buy(self, state, hv, size_mult):
        """Risk-based buy order; returns (order, (stop_pct, trail_pct)).

        The hard stop is locked from the hourly vol at entry; the size
        risks a fixed equity share to that stop, then scales down for thin
        pools and for a running loss streak.
        """
        stop_pct = _clamp(CFG.STOP_MULT * hv, CFG.STOP_FLOOR, CFG.STOP_CAP)
        trail_pct = _clamp(CFG.TRAIL_MULT * hv, CFG.TRAIL_FLOOR, CFG.TRAIL_CAP)
        frac = _clamp(CFG.RISK_BUDGET / stop_pct, CFG.SIZE_MIN, CFG.SIZE_MAX) * size_mult
        if self.loss_streak >= CFG.STREAK_HALVE:
            frac *= 0.5
        depth = state.depth_at(CFG.SYMBOL)
        if depth:
            dmin = min(depth)
            if dmin < CFG.THIN_DEPTH:
                frac *= dmin / CFG.THIN_DEPTH
        notional = int(state.equity * frac)
        if notional > state.cash:
            notional = state.cash
        min_notional = int(state.hello["fees"]["min_notional_quote_units"])
        if notional < min_notional:
            return None, None
        room = state.weight_room(CFG.SYMBOL)
        if notional > room:
            notional = room
        if notional < min_notional:
            return None, None
        return ({"side": "buy", "symbol": CFG.SYMBOL,
                 "notional_quote_units": notional,
                 "max_slippage_bps": CFG.SLIP_BUY_BPS}, (stop_pct, trail_pct))

    def _close_out(self, state, losing):
        """Book-keeping for a closed trade: streaks, cooldown, sit-outs."""
        if losing:
            self.loss_streak += 1
            if self.loss_streak >= CFG.STREAK_STOP:
                self.streak_block_until = state.seq + CFG.STREAK_STOP_BARS
                self.loss_streak = 0
        else:
            self.loss_streak = 0
        self.cooldown_until = state.seq + (CFG.COOLDOWN_LOSS if losing else CFG.COOLDOWN_WIN)
        self.last_trade_seq = state.seq
        self.entry_bar = -1
        self.peak_position_value = 0
        self.trade_kind = "breakout"

    # -- per-bar bookkeeping ------------------------------------------------
    def _track_equity_peak(self, state):
        """Peak equity starts at the race capital and only ever rises."""
        if self.peak_equity is None:
            self.peak_equity = int(state.hello["capital"]["quote_units"])
        if state.equity > self.peak_equity:
            self.peak_equity = state.equity

    def _track_regime(self, state, held, seq):
        """Remember when price last sat at/under EMA(120), and last traded.

        The first bar with a slow EMA is treated as fresh (regime age
        unknown), so an early breakout is not vetoed for stale history.
        """
        if self.slow.value is not None:
            if self.last_below_slow_seq is None:
                self.last_below_slow_seq = seq   # regime age unknown: call it fresh
            elif self.prev_close is not None and float(self.prev_close) <= self.slow.value:
                self.last_below_slow_seq = seq
        if held is not None and held.qty > 0:
            self.last_trade_seq = seq

    def _drawdown_guard(self, state, seq):
        """Equity drawdown guard; returns (entries blocked, close now).

        At -9% from peak: close the position and pause entries 4 hours.
        At -15%: close and stop entering for the rest of the race.
        """
        dd_blocked = self.stopped or seq < self.blocked_until
        dd_close = self.stopped
        if self.peak_equity > 0:
            dd = 1.0 - state.equity / self.peak_equity
            if dd >= CFG.DD_STOP:
                self.stopped = True
                dd_blocked = True
                dd_close = True
            elif dd >= CFG.DD_PAUSE and seq >= self.blocked_until:
                self.blocked_until = seq + CFG.DD_PAUSE_BARS
                dd_blocked = True
                dd_close = True
        return dd_blocked, dd_close

    # -- exits ---------------------------------------------------------------
    def _note_fill(self, state, held, seq):
        """First sight of a filled buy: lock the entry bar and its stops."""
        self.entry_bar = seq
        self.fills_seen += 1
        self.peak_position_value = state.value(CFG.SYMBOL, held.qty)
        if self.pending is not None:
            self.stop_pct, self.trail_pct, self.break_level, self.trade_kind = self.pending
            self.pending = None

    def _exit_triggered(self, value, cost, close, held_bars, trend_down, dd_close):
        """Every exit rule, in the order they are checked.

        Drawdown guard, hard stop, trailing stop, failed breakout/bounce,
        trend break, bounce take-profit, bounce time stop, dead-trade time
        stop. First hit wins; all of them sell the whole position.
        """
        if dd_close:
            return True                     # drawdown guard
        if value <= cost * (1.0 - self.stop_pct):
            return True                     # hard stop
        if (self.peak_position_value >= cost * CFG.TRAIL_ARM
                and value <= self.peak_position_value * (1.0 - self.trail_pct)):
            return True                     # trailing stop
        if (held_bars >= CFG.MIN_HOLD_BREAKOUT and self.break_level > 0
                and close is not None and close < self.break_level):
            return True                     # failed breakout / bounce
        if held_bars >= CFG.MIN_HOLD_SOFT and trend_down:
            return True                     # trend break
        if (self.trade_kind == "bounce" and CFG.BOUNCE_TP > 0
                and value >= cost * (1.0 + CFG.BOUNCE_TP)):
            return True                     # bounce take-profit
        if (self.trade_kind == "bounce" and held_bars >= CFG.BOUNCE_TIME_BARS
                and value < cost * CFG.TIME_STOP_MAX_GAIN):
            return True                     # bounce time stop (2h)
        if (self.trade_kind != "bounce" and held_bars >= CFG.TIME_STOP_BARS
                and value < cost * CFG.TIME_STOP_MAX_GAIN):
            return True                     # dead trade
        return False

    def _manage_position(self, state, held, seq, trend_down, dd_close):
        """Watch the live position; sell it whole when any exit fires.

        Returns [sell order] on an exit, [] while still holding, and is
        only called when a position is actually held.
        """
        if self.entry_bar < 0:
            # position appeared (the buy sent earlier filled at this open)
            self._note_fill(state, held, seq)
        value = state.value(CFG.SYMBOL, held.qty)
        if value > self.peak_position_value:
            self.peak_position_value = value
        cost = held.cost
        close = state.close(CFG.SYMBOL)
        held_bars = seq - self.entry_bar
        if self._exit_triggered(value, cost, close, held_bars, trend_down, dd_close):
            self._close_out(state, value < cost)
            return [{"side": "sell", "symbol": CFG.SYMBOL, "qty": held.qty,
                     "max_slippage_bps": CFG.SLIP_SELL_BPS}]
        return []

    # -- entries --------------------------------------------------------------
    def _breakout_entry(self, state, hv, close, seq):
        """Mode A: the prepared breakout rider, unchanged.

        Every filter must agree: fresh high with margin, non-falling
        market, force without blow-off, no overextension, fresh regime,
        expanding volatility — with one late-race fallback for a bot
        still tradeless at bar 900.
        """
        if float(close) <= self.slow.value:
            return None                               # falling market context
        if len(self.slow_ema_hist) > CFG.SLOW_SLOPE_BARS:
            if self.slow_ema_hist[-1] < self.slow_ema_hist[0] * (1.0 + CFG.SLOW_SLOPE_MIN):
                return None                           # slow EMA falling
        break_level = max(self.close_hist[-CFG.BREAKOUT_BARS - 1:-1])
        if close < break_level * (1.0 + CFG.BREAKOUT_MARGIN):
            return None                               # no decisive fresh breakout
        ret_fast = _trailing_return(self.close_hist, CFG.RET_FAST_BARS)
        ret_slow = _trailing_return(self.close_hist, CFG.RET_SLOW_BARS)
        ret_long = _trailing_return(self.close_hist, CFG.RET_LONG_BARS)
        if ret_fast is None or ret_fast < CFG.RET_FAST_MIN:
            return None                               # breakout without force
        if ret_slow is not None and ret_slow > CFG.RET_SLOW_MAX:
            return None                               # vertical blow-off: spent
        if ret_long is not None and ret_long > CFG.RET_LONG_MAX:
            return None                               # 4-hour move already extended
        if float(close) > self.fast.value * (1.0 + CFG.EXT_MAX):
            return None                               # overextended above the fast EMA
        fresh_regime = (self.last_below_slow_seq is not None
                        and seq - self.last_below_slow_seq <= CFG.FRESH_BARS)
        if not fresh_regime and seq - self.last_trade_seq > CFG.FRESH_BARS:
            return None                               # mature regime: range top
        expanding = (len(self.vol_hist) > CFG.SLOW_SLOPE_BARS
                     and self.vol_hist[-1] >= CFG.VOL_EXPAND * self.vol_hist[-CFG.SLOW_SLOPE_BARS - 1])
        fallback = False
        if not expanding:
            if (not self.fallback_used and self.fills_seen == 0
                    and seq >= CFG.FALLBACK_SEQ):
                fallback = True     # late and tradeless: take it small
            else:
                return None           # quiet breakout: ranges reject these
        order, pending = self._risk_sized_buy(state, hv, 1.0)
        if order is None:
            return None
        if fallback:
            order = dict(order)
            order["notional_quote_units"] = max(
                int(state.hello["fees"]["min_notional_quote_units"]),
                int(order["notional_quote_units"] * CFG.FALLBACK_SIZE))
            self.fallback_used = True
        stop_pct, trail_pct = pending
        self.pending = (stop_pct, trail_pct, break_level, "breakout")
        return order

    def _bounce_entry(self, state, hv, close, seq):
        """Mode B: counter-trend dip-recovery bounce (price under EMA(120)).

        Only tried after the breakout mode declines. It buys the confirmed
        turn off the 30-bar low after a sharp hourly dip — a bounce, not a
        falling knife.
        """
        if float(close) >= self.slow.value:
            return None           # above the slow EMA: the breakout mode owns it
        dip = _trailing_return(self.close_hist, CFG.BOUNCE_DIP_BARS)
        if dip is None or dip > -CFG.BOUNCE_DIP_MIN:
            return None           # no real dip in the last hour
        low = min(self.close_hist[-CFG.BOUNCE_LOW_BARS:])
        if float(close) < low * (1.0 + CFG.BOUNCE_RECOVER):
            return None           # not yet recovered off the low: falling knife
        mom = _trailing_return(self.close_hist, CFG.BOUNCE_MOM_BARS)
        if mom is None or mom < CFG.BOUNCE_MOM_MIN:
            return None           # short-term momentum has not turned up
        if float(close) > self.fast.value * (1.0 + CFG.EXT_MAX):
            return None           # overextended above the fast EMA
        order, pending = self._risk_sized_buy(state, hv, CFG.BOUNCE_SIZE)
        if order is None:
            return None
        stop_pct, trail_pct = pending
        self.pending = (stop_pct, trail_pct, low, "bounce")
        return order

    def _try_entries(self, state, hv, seq, dd_blocked):
        """Flat and unblocked: try the breakout mode, then the bounce."""
        if dd_blocked or seq < self.cooldown_until or seq < self.streak_block_until:
            return []
        if hv is None:
            return []
        if not (CFG.VOL_MIN_HOURLY <= hv <= CFG.VOL_MAX_HOURLY):
            return []
        if len(self.close_hist) < CFG.BREAKOUT_BARS + 1:
            return []
        close = state.close(CFG.SYMBOL)
        if close is None or self.slow.value is None or self.fast.value is None:
            return []

        order = self._breakout_entry(state, hv, close, seq)
        if order is not None:
            return [order]
        order = self._bounce_entry(state, hv, close, seq)
        if order is not None:
            return [order]
        return []

    # -- main ------------------------------------------------------------------
    def on_batch(self, state, batch):
        fresh = False
        for bar in batch["bars"]:
            if bar["symbol"] == CFG.SYMBOL:
                self._ingest_bar(int(bar["c"]))
                fresh = True
        hv = self._hourly_vol()
        if fresh and hv is not None:
            self.vol_hist.append(hv)
            if len(self.vol_hist) > CFG.SLOW_SLOPE_BARS + 1:
                self.vol_hist.pop(0)
        if not fresh or state.warming_up:
            return []

        self._track_equity_peak(state)
        held = state.positions.get(CFG.SYMBOL)
        seq = state.seq
        self._track_regime(state, held, seq)
        trend_down = (self.fast.value is not None and self.slow.value is not None
                      and self.fast.value < self.slow.value)
        dd_blocked, dd_close = self._drawdown_guard(state, seq)

        if held is not None and held.qty > 0:
            return self._manage_position(state, held, seq, trend_down, dd_close)

        # flat: forget stale position state
        if self.entry_bar >= 0:
            self.entry_bar = -1
            self.peak_position_value = 0

        return self._try_entries(state, hv, seq, dd_blocked)


_strategy = Strategy()


def on_batch(state, batch):
    return _strategy.on_batch(state, batch)


if __name__ == "__main__":
    makefun_bars.run(on_batch)
