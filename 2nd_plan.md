# CB101 Hysteresis Lock + New Defaults — Implementation Plan

## Context

The simulator currently unlocks the CB101 button (and `set_cb101`) as soon as flipping it
would *not immediately* be reverted next tick — which can be as early as 0.01 Hz inside the
49.50 / 50.50 limits (e.g. 49.53 Hz). The user wants the interlock itself to be stickier: once
it trips, CB101 must stay forced in its safety position — and the button/setter must stay
locked — until frequency fully recovers to **nominal (50.00 Hz)**, not just back inside the
safe band. This is a deliberate, explicit deviation from `AI_prompt.md` (which is left
untouched per standing instructions); the behavior change lives in code only.

Two other explicit asks bundled into this change:
- Default `g101` becomes **ON** (was OFF). Default `cb101` stays **CLOSED** (no change).
- The lock predicate, the actual auto-forced CB101 state, and the button's `disabled` flag
  must all agree — this is the same "single predicate" lesson already baked into
  [simulator.py](src/power_grid_simulator/simulator.py) (`_would_be_reverted` /
  `is_cb101_locked` / `set_cb101` all funnel through one check). The fix extends that one
  predicate rather than adding a second one, to avoid reintroducing the two-predicate bug
  that was fixed earlier (11.9% silently-reverted commands).

## Design: a hysteresis latch on `_required_cb_state`

Today `_required_cb_state(frequency)` is a pure threshold check (`<= 49.50` → OPEN,
`>= 50.50` → CLOSED, else `None`). It needs a memory: once a hard limit trips, keep requiring
that state until frequency crosses back through `NOMINAL_FREQ` (50.00) in the recovering
direction — classic bang-bang-with-hysteresis.

Add one new instance attribute, `self._latch` (`OPEN`, `CLOSED`, or `None`), and split the pure
threshold check out (so `is_frequency_unsafe()` can stay literal — see below):

```python
def _hard_limit_state(self, frequency):
    """The spec-literal threshold check, ignoring hysteresis."""
    if frequency <= UNDER_FREQ:
        return OPEN
    if frequency >= OVER_FREQ:
        return CLOSED
    return None

def _required_cb_state(self, frequency):
    """CB101 state the hysteresis interlock requires at `frequency`, or None if free.

    Once a hard limit trips, the requirement persists until frequency fully
    recovers to NOMINAL_FREQ -- not just back inside the safe band -- so the
    interlock (and the button) cannot chatter right at the edge of a limit.
    """
    hard = self._hard_limit_state(frequency)
    if hard is not None:
        return hard
    if self._latch == OPEN and frequency < NOMINAL_FREQ:
        return OPEN
    if self._latch == CLOSED and frequency > NOMINAL_FREQ:
        return CLOSED
    return None

def is_frequency_unsafe(self):
    """Spec-literal: is the bus outside the safe band right now? Latch-independent."""
    return self._hard_limit_state(self.frequency) is not None
```

`_required_cb_state` stays **pure** (reads `self._latch`, never writes it), so it remains safe
to call from `_would_be_reverted` / `is_cb101_locked` for UI queries and fuzz probing without
side effects. The latch is advanced exactly once per real tick, at the end, using the actual
post-step frequency:

```python
def tick(self):
    if not self.running or self.is_finished:
        return
    self._enforce_interlock(self.frequency)
    self._enforce_interlock(self._next_frequency())
    self.frequency = self._next_frequency()
    self._latch = self._required_cb_state(self.frequency)   # advance hysteresis
    self.t += 1
    self._record()
```

Because `_enforce_interlock` and `_would_be_reverted` both already route through
`_required_cb_state`, this single change makes three things agree automatically:
1. the CB101 state the simulator actually forces (`_enforce_interlock`),
2. what `set_cb101` will refuse (`_would_be_reverted`), and
3. what the button shows as disabled (`is_cb101_locked`).

`reset()` needs `self._latch = None` set **before** the `self.set_cb101(CLOSED)` call inside
it (that call reads `self._latch`, which doesn't exist yet on first construction otherwise),
and `self.g101 = OFF` becomes `self.g101 = ON`:

```python
def reset(self):
    self.t = 0
    self.frequency = NOMINAL_FREQ
    self.g101 = ON                 # changed default
    self._latch = None             # new — must precede set_cb101 below
    self.running = False
    self.history = []
    self.set_cb101(CLOSED)
    self._record()
```

No changes needed to `set_cb101`, `_enforce_interlock`, `_would_be_reverted`, or
`is_cb101_locked` — they inherit the new behavior for free by calling `_required_cb_state`.

## tests/test_simulator.py (TDD: update/add these first, then make simulator.py pass them)

**Default-value tests** — flip expected `g101` from OFF to ON:
- `test_initial_state`, `test_reset_records_the_t0_row`, `test_reset_after_ticks_restores_initial_state`

**Tests whose numeric traces assumed the old default-OFF decline** — add explicit `g101=OFF`
to their `running_sim(...)` calls so the intended scenario (fast decline, hits the latch at
tick 4) still holds regardless of the new default:
`test_cb101_does_not_flip_before_a_predicted_breach`,
`test_flip_happens_one_tick_early_and_applies_the_new_rate`,
`test_golden_trace_with_g101_off` (name already promises OFF — make it explicit),
`test_under_frequency_latch_is_stable`, `test_set_g101_is_never_blocked`,
`test_set_cb101_rejected_when_it_would_be_reverted`,
`test_interlock_override_beats_a_refused_command`,
`test_auto_interlock_reuses_set_cb101`,
`test_interlock_cannot_recover_under_frequency_without_generation` (its whole premise is "no
generation" — must pin `g101=OFF` explicitly now that it's no longer the default).

**New hysteresis tests** (the actual point of this change):
- Under-frequency: drive to the latch with `g101=OFF`, then `set_g101(True)` to allow recovery
  (`(ON, OPEN)` = +0.12/s). Assert `set_cb101(CLOSED)` returns `False` and `is_cb101_locked()`
  is `True` on every tick while frequency is mid-band but `< 50.00` (e.g. at 49.79 Hz — well
  inside 49.50–50.50, which the *old* predicate would have unlocked). Assert the lock releases
  and `set_cb101(CLOSED)` succeeds only once frequency reaches `>= 50.00`.
- Over-frequency: inject `frequency=50.55, g101=ON, cb101=OPEN` (as the existing
  `test_injected_over_frequency_recovers` does) to trip the CLOSED latch, then keep ticking
  (`(ON, CLOSED)` = -0.05/s declining). Assert `set_cb101(OPEN)` stays rejected while
  frequency is mid-band but `> 50.00`, and releases once frequency reaches `<= 50.00`.

**Recompute, don't guess**: `test_steady_state_cycle_with_g101_on`'s hand-derived period/band
(51 ticks, [49.53, 50.49]) is invalidated by the wider hysteresis swing (release now happens at
nominal, not at the band edge). After implementing, run the simulator to capture the actual
cycle and hardcode the verified numbers — do not hand-guess replacement floats.

**Should need no changes** (verify, don't blindly trust): `test_rate_table_*`,
`test_frequency_never_reaches_the_limits` (explicit states already), `test_set_cb101_allowed_*`,
`test_is_cb101_locked_is_false_mid_band`, `test_is_frequency_unsafe_is_false_in_normal_operation`
(now literal-only, so still true), `test_cb101_has_a_single_assignment_site`, the rate-table
soundness/fuzz tests, and the lifecycle tests.

## app.py

1. **Sync the G101 toggle to the new default.** `get_simulator()` currently only seeds `sim`
   and `last_tick`; it must also seed the keyed toggle on first creation:
   `st.session_state[G101_KEY] = sim.g101 == ON`. Otherwise the toggle widget defaults to
   `False` on first render while `sim.g101` is `ON`, and the UI disagrees with the model.
2. **`on_reset()`** currently hardcodes `st.session_state[G101_KEY] = False` — change to sync
   from the actual post-reset state, `st.session_state[G101_KEY] = st.session_state.sim.g101 == ON`,
   so it can't drift from `reset()`'s default again.
3. **Interlock banner copy** (the `if locked:` block) — update both messages to name the
   nominal-recovery condition instead of just the crossed limit, e.g. "held OPEN until the bus
   recovers to 50.00 Hz" / "held CLOSED until the bus falls back to 50.00 Hz", so the operator
   understands why the button stays disabled well inside the safe band.
4. **Bottom caption** — update the sentence describing the predictive interlock; it currently
   says the button unlocks "whenever the change would be reverted on the next tick" without
   mentioning the nominal-recovery hysteresis now in play.
5. **Chart (recommended, small):** add a third dotted reference line + label at `NOMINAL_FREQ`
   in `frequency_chart`, colored **green** (distinct from the red limit lines) since 50.00 Hz is
   now an operationally meaningful recovery target, not just the starting value.

## Verification

```powershell
uv run pytest tests/ -v
uv run streamlit run src/power_grid_simulator/app.py
```

1. Tests green first (CLAUDE.md TDD rule) — including the two new hysteresis tests and the
   recomputed steady-state cycle.
2. Manual check: Start with defaults (G101 **ON**, CB101 CLOSED). Frequency declines at
   0.05 Hz/s; when it nears 49.50 CB101 auto-opens. Confirm the button stays greyed out and the
   banner stays up all the way through the climb back — including once frequency is visibly
   back inside the safe band (e.g. 49.7–49.9 Hz) — releasing only at 50.00 Hz. Confirm the
   symmetric behavior on the over-frequency side once frequency climbs past 50.50.

# Implementation Output Summary
simulator.py: added a `self._latch` hysteresis flag. `_required_cb_state()` now persists the required CB101 state until frequency fully recovers to `NOMINAL_FREQ` (50.00 Hz), not just back inside the 49.50–50.50 band. The latch is written only inside `_enforce_interlock()` (called exclusively with real, never-hypothetical frequencies during `tick()`), so `_required_cb_state()` stays pure and safe for `set_cb101`'s gate, `is_cb101_locked()`, and fuzz probing to share. `is_frequency_unsafe()` was split off into a separate latch-independent `_hard_limit_state()` check so it keeps its original spec-literal meaning. Default `g101` is now `ON` (already in place from your earlier edits); `reset()` initializes the latch before the first `set_cb101` call.

tests/test_simulator.py: updated default-value assertions, pinned `g101=OFF` explicitly on the tests whose numeric traces depended on the old default, and added two new tests proving the lock survives well into the mid-band (e.g. 49.79 Hz, 50.20 Hz) and releases only at nominal, in both directions.

app.py: seeded the `G101` toggle from the sim on first load, updated the interlock banner and the bottom caption to explain the latch-to-nominal behavior, and added a green dotted reference line at 50.00 Hz on the frequency chart marking the recovery target.

Verified via pytest (109/109 green) and Streamlit's `AppTest` harness — simulated an under-frequency trip, ticked through recovery, and confirmed the button stays disabled with the correct banner text all the way from 49.55 Hz through 49.91 Hz, releasing exactly at 50.03 Hz.