"""Control logic for the station power grid simulator.

Deliberately free of any Streamlit / pandas / altair import so that it can be
unit tested on its own and driven from anything. ``app.py`` owns presentation;
this module owns state and safety.

Safety model
------------
The bus must never *reach* 49.50 Hz or 50.50 Hz, so the interlock is
**predictive**: before applying a rate, :meth:`PowerGridSimulator.tick` asks
whether the next step would breach a limit and, if so, moves CB101 first and
applies the new state's rate instead. A purely reactive interlock would have to
record a breaching value before responding to it.

The property this guarantees is inductive -- "safe state in, safe state out" --
not "always safe". Because ``(OFF, OPEN)`` is 0.00 Hz/s, the interlock can
*halt* an under-frequency decay but cannot reverse one; recovery needs
generation. See ``tests/test_simulator.py`` for both cases.

It also **latches**: once a limit trips, CB101 stays forced in its safety
position -- and manual commands stay refused -- until frequency fully
recovers to ``NOMINAL_FREQ``, not just back inside the safe band. See
``self._latch`` and ``_required_cb_state``.
"""

# Generator states
ON = "ON"
OFF = "OFF"

# Circuit breaker states
OPEN = "OPEN"
CLOSED = "CLOSED"

NOMINAL_FREQ = 50.00
UNDER_FREQ = 49.50
OVER_FREQ = 50.50

DEFAULT_DURATION = 60

# Frequency rate of change per second, keyed by (G101, CB101).
RATES = {
    (ON, CLOSED): -0.05,
    (ON, OPEN): +0.12,
    (OFF, CLOSED): -0.15,
    (OFF, OPEN): 0.00,
}


class PowerGridSimulator:
    """Bus frequency model for generator G101 and circuit breaker CB101.

    Advances in discrete 1-second ticks. The operator may flip G101 freely and
    CB101 whenever the interlock allows it; :meth:`tick` is the only method
    that moves time forward.
    """

    def __init__(self, duration=DEFAULT_DURATION):
        self.duration = duration
        self.reset()

    # -- lifecycle --------------------------------------------------------

    def reset(self):
        """Return to the documented startup state: 50.00 Hz, G101 ON, CB101 CLOSED."""
        self.t = 0
        self.frequency = NOMINAL_FREQ
        self.g101 = ON
        # Must precede set_cb101() below: _required_cb_state() reads this, and
        # it would not exist yet on the very first construction otherwise.
        self._latch = None
        self.running = False
        self.history = []
        # Routed through the setter so it stays the single writer of cb101.
        self.set_cb101(CLOSED)
        self._record()

    def start(self):
        if not self.is_finished:
            self.running = True

    def stop(self):
        self.running = False

    @property
    def is_finished(self):
        """Derived, never stored -- the duration can be edited mid-run."""
        return self.t >= self.duration

    # -- operator controls ------------------------------------------------

    def set_g101(self, on):
        """Manual generator toggle. Never blocked: G101 has no interlock."""
        self.g101 = ON if on else OFF

    def set_cb101(self, state, *, override_interlock=False):
        """The only writer of ``cb101``.

        Returns ``False`` and changes nothing when the interlock refuses the
        command. The automatic interlock reuses this method with
        ``override_interlock=True`` rather than assigning ``cb101`` itself.
        """
        if not override_interlock and self._would_be_reverted(state):
            return False
        self.cb101 = state
        return True

    # -- interlock --------------------------------------------------------

    def _hard_limit_state(self, frequency):
        """The spec-literal threshold check at ``frequency``, ignoring the latch."""
        if frequency <= UNDER_FREQ:
            return OPEN
        if frequency >= OVER_FREQ:
            return CLOSED
        return None

    def _required_cb_state(self, frequency):
        """CB101 state the interlock requires at ``frequency``, or None if free.

        Once a hard limit trips, ``self._latch`` remembers it, so the
        requirement persists until frequency fully recovers to
        ``NOMINAL_FREQ`` -- not just back inside the safe band -- and the
        interlock (and the button) cannot chatter right at the edge of a
        limit. Only reads ``self._latch``, never writes it, so this stays a
        pure query safe to call from `_would_be_reverted` for UI checks and
        fuzz probing.
        """
        hard = self._hard_limit_state(frequency)
        if hard is not None:
            return hard
        if self._latch == OPEN and frequency < NOMINAL_FREQ:
            return OPEN
        if self._latch == CLOSED and frequency > NOMINAL_FREQ:
            return CLOSED
        return None

    def _would_be_reverted(self, cb_state):
        """True if putting CB101 in ``cb_state``(OPEN/CLOSED) would breach a limit next tick."""
        required = self._required_cb_state(self._next_frequency(cb_state))
        return required is not None and required != cb_state

    def _enforce_interlock(self, frequency):
        """Force CB101 to the required state and advance the hysteresis latch.

        The only place ``self._latch`` is written. Safe to do here because
        ``tick()`` only ever calls this with a real frequency -- the current
        one, or the one about to be applied -- never a hypothetical probe
        like `_would_be_reverted` uses. That distinction is what keeps
        `_required_cb_state` itself pure.
        """
        required = self._required_cb_state(frequency)
        self._latch = required
        if required is not None and required != self.cb101:
            self.set_cb101(required, override_interlock=True)

    def is_frequency_unsafe(self):
        """The spec-literal check: is the bus outside the safe band right now?

        Latch-independent by design, unlike `_required_cb_state`. Always False
        in normal operation, because the predictive interlock acts a tick
        earlier. Kept because it is the stated rule, and because it is the
        recovery trigger for an externally injected unsafe state.
        """
        return self._hard_limit_state(self.frequency) is not None

    def is_cb101_locked(self):
        """Whether the UI should disable the CB101 button.

        Uses the same predicate as :meth:`set_cb101`, so a button that looks
        clickable is one the simulator will actually accept.
        """
        opposite = OPEN if self.cb101 == CLOSED else CLOSED
        return self._would_be_reverted(opposite) or self.is_frequency_unsafe()

    # -- simulation -------------------------------------------------------

    def _rate(self, cb_state=None):
        return RATES[(self.g101, self.cb101 if cb_state is None else cb_state)]

    def _next_frequency(self, cb_state=None):
        """Single source of truth for the value :meth:`tick` is about to store.

        Rounds here so the interlock checks the same number that gets stored;
        every rate is 2 decimals, and a test asserts frequency stays on that
        grid.
        """
        return round(self.frequency + self._rate(cb_state), 2)

    def _record(self):
        self.history.append(
            {
                "t": self.t,
                "frequency": self.frequency,
                "g101": self.g101,
                "cb101": self.cb101,
            }
        )

    def tick(self):
        """Advance one second: interlock, then rate, then record."""
        if not self.running or self.is_finished:
            return

        # Reactive: the spec's literal "if frequency reaches <= 49.50" rule.
        # Unreachable in normal operation because the predictive pass below
        # gets there a tick earlier -- it only fires for an injected state.
        self._enforce_interlock(self.frequency)

        # Predictive: do not step *into* a breach. _next_frequency() must stay
        # a call here, not a local hoisted above the reactive pass, because
        # that pass may have changed CB101 and therefore the rate.
        self._enforce_interlock(self._next_frequency())

        self.frequency = self._next_frequency()
        self.t += 1
        self._record()

        if self.is_finished:
            self.running = False
