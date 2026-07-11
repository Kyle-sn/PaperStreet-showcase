"""
indicators.py

Shared, bounded rolling-window state for strategies.

Every bar strategy needs the same thing: keep the last N closes and compute
simple statistics off them. Doing this with a plain list (as the early
strategies did) leaks memory in a long-running live loop because the list grows
without bound. `RollingWindow` is backed by a `collections.deque` with a fixed
`maxlen`, so memory is constant regardless of how long the strategy runs.
"""

from __future__ import annotations

from collections import deque


class RollingWindow:
    """
    Fixed-size rolling window of float values with O(1) append.

    Once `size` values have been seen the oldest is evicted on each append, so
    the window always holds at most `size` values. `ready` is True once it is
    full — strategies should suppress signals until then (warm-up).

    Parameters
    ----------
    size : int
        Number of most-recent values to retain.
    """

    def __init__(self, size: int):
        if size <= 0:
            raise ValueError(f"size must be positive, got {size}")
        self.size = size
        self._values: deque[float] = deque(maxlen=size)

    def append(self, value: float) -> None:
        self._values.append(value)

    @property
    def ready(self) -> bool:
        """True once the window holds a full `size` values."""
        return len(self._values) == self.size

    @property
    def last(self) -> float:
        return self._values[-1]

    def mean(self) -> float:
        return sum(self._values) / len(self._values)

    def std(self) -> float:
        """
        Population standard deviation of the current window.

        Population (divide by N) rather than sample (N-1) matches the original
        MeanReversionStrategy behavior. Returns 0.0 when all values are equal.
        """
        n = len(self._values)
        mean = sum(self._values) / n
        variance = sum((v - mean) ** 2 for v in self._values) / n
        return variance ** 0.5

    def __len__(self) -> int:
        return len(self._values)


class WilderRSI:
    """
    Streaming Wilder RSI with O(1) per-bar update and constant memory.

    `period=2` reproduces the Connors/Alvarez RSI(2) used by the SPY
    short-reversal research notebook, which computed it vectorized as:

        delta = close.diff()
        up, down = delta.clip(lower=0), -delta.clip(upper=0)
        roll_up   = up.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
        roll_down = down.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
        rsi = 100 - 100 / (1 + roll_up / roll_down)

    The streaming form here matches that exactly so a live/backtest strategy
    emits the same signal the research measured (the core parity guarantee):
    the first price difference *seeds* the smoothed averages (pandas
    `adjust=False`), each later bar recurses `avg = (1-alpha)*avg + alpha*x`
    with `alpha = 1/period`, and `ready` flips True only once `period`
    differences have been seen (pandas `min_periods=period`).

    Parameters
    ----------
    period : int
        RSI lookback. Must be positive.
    """

    def __init__(self, period: int):
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        self.period = period
        self._alpha = 1.0 / period
        self._prev: float | None = None
        self._avg_gain: float | None = None
        self._avg_loss: float | None = None
        self._diffs = 0  # count of price differences seen (== non-NaN ewm inputs)

    def update(self, value: float) -> None:
        if self._prev is None:
            self._prev = value
            return
        delta = value - self._prev
        self._prev = value
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0

        if self._avg_gain is None:
            # First difference seeds the recursion (pandas adjust=False).
            self._avg_gain, self._avg_loss = gain, loss
        else:
            self._avg_gain = (1 - self._alpha) * self._avg_gain + self._alpha * gain
            self._avg_loss = (1 - self._alpha) * self._avg_loss + self._alpha * loss
        self._diffs += 1

    @property
    def ready(self) -> bool:
        """True once `period` price differences have been observed."""
        return self._diffs >= self.period

    @property
    def value(self) -> float | None:
        """Current RSI in [0, 100], or None during warm-up. 100 when there is
        no downside (avg loss is zero), mirroring 100 - 100/(1+inf)."""
        if not self.ready:
            return None
        if self._avg_loss == 0:
            return 100.0
        rs = self._avg_gain / self._avg_loss
        return 100.0 - 100.0 / (1.0 + rs)
