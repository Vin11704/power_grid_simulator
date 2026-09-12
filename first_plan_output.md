# Power Grid Simulator — Implementation Plan

## Context

The repo is a bare `uv init --package` scaffold: the only Python file is `src/power_grid_simulator/__init__.py` with a `main()` print stub. There is no simulator, no Streamlit app, and no tests. pytest is not installed.

[AI\_prompt.md](AI_prompt.md) specifies a live Streamlit power grid simulator for an Arctic research station: one diesel generator (G101), one circuit breaker (CB101), and a frequency sensor (FS101) on a bus that must be held near 50.00 Hz. The operator flips G101 and CB101 while the simulation runs in 1-second ticks, and a safety interlock overrides the operator to keep bus frequency inside 49.50–50.50 Hz. The outcome is a working app plus a unit-tested, UI-independent control-logic module.

### Decisions made with the user

1. **Predictive interlock.** `tick()` checks whether the next step *would* breach a limit and flips CB101 *before* stepping, so frequency never even touches 49.50 / 50.50. This is the only reading that satisfies the spec's "must never reach this value" — a reactive interlock would record 49.40 before opening.
2. **No markdown is written.** `AI_prompt.md` says to leave all markdown untouched, so README.md (currently truncated mid-sentence at `- cont`) and the `AI_USAGE.md` deliverable named in [power\_grid\_simulator.txt](power_grid_simulator.txt) are **out of scope** — the user writes those. Flagged here so it is not mistaken for an oversight.
3. **Layout** keeps the existing `uv_build` src-layout: modules in the package, `tests/` at root.
4. **Button disable = "toggling would breach."** Because of decision 1, frequency stays strictly inside the band, so a literal "frequency outside safe range" check would be dead code and the button would never gray out. CB101's button is instead disabled when flipping it would breach a limit on the next tick.

Design was stress-tested against the installed Streamlit 1.63.0 / Altair 6.2.2 / pandas 3.0.5; the traces and API details below are machine-verified, not assumed.

## Files

| File | Action |
| --- | --- |
| [simulator.py](simulator.py) | new — `PowerGridSimulator`, no Streamlit/pandas import  |
| [app.py](app.py)             | new — Streamlit UI, Altair charts                       |
| [test\_simulator.py](test_simulator.py)         | new — control-logic unit + property tests               |
| [pyproject.toml](pyproject.toml)                      | edit — declare `altair`, `pandas`; add pytest dev group |

Leave the existing `main()` stub alone — `[project.scripts]` references it. Per [CLAUDE.md](CLAUDE.md), comment out rather than delete if anything must change.

---

## simulator.py

Pure Python, zero UI dependencies, so tests import it directly.

```python
UNDER_FREQ, OVER_FREQ, NOMINAL = 49.50, 50.50, 50.00

RATES = {                      # (G101, CB101) -> Hz per second
    (ON,  CLOSED): -0.05,
    (ON,  OPEN):   +0.12,
    (OFF, CLOSED): -0.15,
    (OFF, OPEN):    0.00,
}

```

State owned by the class: `t`, `frequency`, `g101`, `cb101`, `history`, `running`, `duration`. `__init__` **delegates to `reset()`** (DRY), and `reset()` records the **t=0 row** — otherwise the chart starts at 49.85 and the operator never sees the 50.00 starting point.

### One predicate, three consumers

The load-bearing correction from the design review: the interlock check and the button's disabled-state check must be *the same predicate*. If `set_cb101` gates on the *current* frequency, its guard can never fire (predictive interlock keeps the current frequency inside the band), so the setter would silently accept every command and the greyed-out button in the browser would become the only safety mechanism. Fuzzing that version showed **11.9% of accepted manual CB commands were reverted on the very next tick** — and because `_record()` runs after the interlock, the reverted state never even appears in the chart, so the button reads as broken.

```python
def _required_cb_state(self, frequency):
    if frequency <= UNDER_FREQ: return OPEN      # under-freq -> must OPEN
    if frequency >= OVER_FREQ:  return CLOSED    # over-freq  -> must CLOSE
    return None                                  # safe band  -> operator's choice

def _next_frequency(self, cb_state=None) -> float:
    """Single source of truth for the value tick() is about to store."""
    cb = self.cb101 if cb_state is None else cb_state
    return round(self.frequency + RATES[(self.g101, cb)], 2)

def _would_be_reverted(self, cb_state) -> bool:
    """True if putting CB101 in cb_state would make the next tick breach a limit."""
    required = self._required_cb_state(self._next_frequency(cb_state))
    return required is not None and required != cb_state

def set_cb101(self, state, *, override_interlock=False) -> bool:
    """The ONLY writer of self.cb101. Returns False if the interlock refused."""
    if not override_interlock and self._would_be_reverted(state):
        return False
    self.cb101 = state
    return True

def _enforce_interlock(self, frequency) -> None:      # DRY: reuses set_cb101
    required = self._required_cb_state(frequency)
    if required is not None and required != self.cb101:
        self.set_cb101(required, override_interlock=True)

```

`_next_frequency()` doing the rounding matters: the check and the stored value must be the same number. Testing the raw sum while storing the rounded sum is safe *today* only because every rate is exactly 2 decimals — an unenforced invariant. A test asserts `frequency == round(frequency, 2)` to keep it honest. Float drift itself is a non-issue (2.5e-12 after 2000 unrounded ticks vs. a 0.01 Hz margin), so integer centi-hertz or `Decimal` would be over-engineering.

### tick()

```python
def tick(self) -> None:
    if not self.running or self.is_finished:
        return
    self._enforce_interlock(self.frequency)          # spec-literal reactive branch
    self._enforce_interlock(self._next_frequency())  # predictive; re-reads rate after any flip
    self.frequency = self._next_frequency()
    self.t += 1
    self._record()

```

Two notes for whoever reads this next, both worth a code comment:

- The **reactive branch never fires in normal operation** (0 times in \~800k fuzzed ticks) because the predictive branch gets there first. Keep it: it is the spec's literal "if frequency reaches <= 49.50" rule and it is the only recovery path for an externally-injected unsafe state.
- `self._next_frequency()` must stay a *call* inside the predictive line. Hoisting it to a local above the reactive branch silently breaks the design, because the reactive flip changes the rate.

### Public surface

- `tick()`, `start()`, `stop()`, `reset()`
- `set_g101(on: bool)` — never blocked; `set_cb101(state, *, override_interlock=False) -> bool`
- `is_cb101_locked()` → `self._would_be_reverted(opposite) or self.is_frequency_unsafe()`; the UI queries this each rerun for `disabled=`
- `is_frequency_unsafe()` — the spec-literal check. Always False in normal operation; kept and tested so a reader looking for the literal rule finds it.
- `is_finished` as a **property** (`t >= duration`), not a stale flag
- `history` as `list[dict]` with keys `t, frequency, g101, cb101` — no pandas in `simulator.py`, so the tests need neither pandas nor altair

### Expected dynamics (assert these as golden traces)

- **G101 OFF from the start:** 50.00 → 49.85 → 49.70 → 49.55, then CB101 auto-OPENs and frequency **holds at 49.55 forever** (rate becomes 0.00). Never touches 49.50. ✅ correct, but a flat line with a dead button looks like a hung app — hence the banner requirement below.
- **G101 ON:** settles into an asymmetric sawtooth, **period 51 ticks, band [49.53, 50.49]** — \~7 s rising while OPEN per \~17 s decaying while CLOSED. Margin to the limits is only 0.03 Hz low / 0.01 Hz high, which is why the golden-trace tests are worth having.
- Fuzzing 800k ticks of random operator actions: min 49.51, max 50.49, zero breaches.

---

## app.py

One `PowerGridSimulator` in `st.session_state` (**not** `@st.cache_resource` — that is shared across browser sessions). The split below is forced by three verified Streamlit behaviours, not by style preference:

**Outside the fragment** — duration `number_input`, and Start / Pause / Reset:

`run_every` is evaluated **at decoration time, in the main script body**. If Start/Pause lived inside the fragment, clicking Pause would only rerun the fragment, the decorator line would never re-execute, and *the timer would keep firing while paused*.

```python
run_every = "1s" if (sim.running and not sim.is_finished) else None

@st.fragment(run_every=run_every)
def live_view():
    ...
live_view()          # ALWAYS call it — never conditionally skip (streamlit#9080)

```

**Inside the fragment** — G101 toggle, CB101 button, status banner, frequency metric, all three charts. Anything outside the fragment **does not refresh on timer reruns** ("the two `st.write` commands outside the fragment will not update the frontend"), so a CB101 button rendered outside would hold a stale `disabled=` flag for up to a full cycle.

**Wall-clock tick guard.** A full rerun re-executes the fragment body, so every Start/Pause/Reset click would otherwise inject an extra `tick()` — directly violating the spec's "button clicks should update the state immediately, independent of the tick timer". Also makes 1 tick ≈ 1 real second:

```python
if sim.running and not sim.is_finished:
    now = time.monotonic()
    if now - st.session_state.last_tick >= 0.95:
        st.session_state.last_tick = now
        sim.tick()

```

Remaining UI details:

- **G101**: `st.toggle` with an `on_change` callback → `set_g101(...)`. Nothing overrides G101, so a keyed widget is safe. Reset must clear it via `st.session_state.g101_toggle = False` **inside the on\_click callback** — callbacks run before widget instantiation; doing it in the script body raises `StreamlitWidgetAlreadyInstantiatedError`.
- **CB101**: a stateless `st.button` labeled "Open CB101" / "Close CB101" from the current state, `disabled=sim.is_cb101_locked()`, with `st.toast` on a `set_cb101` returning False. Deliberately *not* a keyed toggle — the interlock writes `cb101` behind the widget's back, and a keyed toggle cannot be synced from the sim after instantiation.
- Prefer `on_click=` / `on_change=` callbacks over `if st.button(...):` so script ordering between controls and charts stops mattering.
- **Status banner when locked**, naming the only escape — otherwise the 49.55 freeze is inexplicable: *"INTERLOCK — CB101 tripped OPEN at 49.55 Hz. No generation, no load: frequency is held. Turn G101 ON to recover."*
- **One `st.rerun()` on the finishing tick.** The run ends inside a timer rerun, so no full rerun happens, `run_every` is never recomputed, and the fragment would rerun every second forever.
- Duration input: default 60, `min_value=1`, **`max_value=3600`** (Altair raises `MaxRowsError` above 5000 rows, confirmed on this install), disabled while running.
- After finishing, `tick()` early-returns, so disable Start and caption "Reset to run again".

## Charts (Altair, per spec — not `st.line_chart`)

Built from `pd.DataFrame(sim.history)` in `app.py`. All three APIs below were validated by `chart.to_dict()` on Altair 6.2.2.

1. **Bus frequency** — `mark_line` layered with `mark_rule(strokeDash=[6, 4], color="red")` at 49.50 and 50.50 plus `mark_text` labels pinned to the left edge via `x=alt.value(5)`. Y scale `alt.Scale(zero=False, domain=[49.40, 50.60], nice=False)` — `nice=False` matters, or the domain widens and the rules drift off the visual edge.
2. **G101 over time** — `mark_line(interpolate="step-after")` on a derived 0/1 column with `axis=alt.Axis(values=[0, 1], labelExpr="datum.value == 1 ? 'ON' : 'OFF'")`.
3. **CB101 over time** — same treatment, labeled `CLOSED` / `OPEN`.

Combine with `alt.vconcat(...).resolve_scale(x="shared")`. Call `st.altair_chart(chart)` **without** `use_container_width` — legacy in 1.63; `width=None` already stretches.

---

## tests/test\_simulator.py

Control logic only, no Streamlit import. No `conftest.py` needed — the project is already editable-installed, so `from power_grid_simulator.simulator import ...` resolves.

- **Construction / reset** — initial state (t=0, 50.00, OFF, CLOSED, not running); `reset()` records the t=0 row (`len(history) == 1`); reset after ticks restores everything; `__init__` matches a freshly `reset()` instance.
- **Rate table** — parametrized over all four combos for the exact deltas, plus `RATES` is total over `{ON,OFF} × {OPEN,CLOSED}` (a missing key is a `KeyError` mid-tick).
- **Predictive interlock, the core** — CB101 does *not* flip early (still CLOSED at 49.55); the flip tick leaves frequency **at 49.55 with CB101 OPEN**, proving the *new* rate was applied rather than the old one; golden trace `[49.85, 49.70, 49.55, 49.55, 49.55, 49.55]`; the latch holds stable over 50 further ticks; the G101-ON cycle has period 51 / band [49.53, 50.49].
- **Safety invariant** — parametrized over all four initial states × 500 ticks: `49.50 < frequency < 50.50`.
- **Manual control** — `set_g101` is never blocked; `set_cb101(CLOSED)` at the latch returns False **and leaves ****`cb101`**** unchanged**; both directions allowed mid-band; settable while paused.
- **DRY (the spec calls this out explicitly)** — `monkeypatch` a spy over `set_cb101` and assert the auto-interlock path calls it with `override_interlock=True`, rather than assigning `cb101`.
- **Fuzz / property** (`parametrize("seed", range(50))`, 200 ticks, random G101 flips and random `set_cb101` at p=0.25):
  - CB101 is never CLOSED while frequency is below 49.50 — the spec's hardest constraint
  - frequency stays strictly inside the band, and always on the 2-decimal grid
  - `is_cb101_locked() == (set_cb101(opposite) is False)` at every step — **this is the test that catches the two-predicate hole described above**
  - history is self-consistent: `f[i] - f[i-1] == RATES[(g101[i], cb101[i])]` using the recorded post-tick states, which proves `_record()` runs after the flip
- **Load-bearing assumption guards** — the interlock is only sound because OPEN rates are ≥ 0 and CLOSED rates are ≤ 0, and because `max(|rate|) < 1.00 Hz` (the band width) so a flip cannot breach the opposite limit. Both are silent if someone edits the rate table; assert them.
- **Injected edge states** — an injected 49.45 opens CB101 (the only test exercising the reactive branch, so nobody deletes that line); with G101 OFF it then *stays* at 49.45 forever, asserting deliberately that the interlock can halt but not recover from an under-frequency.
- **Lifecycle** — `tick()` no-ops while stopped and while finished; `history` length is `t + 1`; stops at `t == duration`; duration reduced below `t`; duration zero.

## pyproject.toml

```toml
dependencies = ["streamlit>=1.63.0", "altair>=6.2.2", "pandas>=3.0.0"]

[dependency-groups]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]

```

`altair` and `pandas` are currently only available transitively through Streamlit but are imported directly by `app.py`, so promote them. `numpy`/`pyarrow` stay transitive. pytest goes in `[dependency-groups]` (PEP 735, uv-native, installed by `uv sync` by default) rather than optional-dependencies — it is not a feature of the distributed package.

## Deliberate spec deviations (worth a caption in the UI)

1. Predictive locking is **stricter** than "the operator can manually open CB101 at any time the frequency is < 50.50" — it blocks opening at f ≥ 50.38 with G101 ON. This is the direct consequence of the never-reach-the-limit requirement.
2. The interlock **halts** an under-frequency decay but cannot **recover** from one, because `(OFF, OPEN)` is 0.00 Hz/s. Recovery requires generation. Documented in the docstring and asserted by a test; the provable property is inductive ("safe state + tick → safe state"), not "always safe".

## Verification

```powershell
uv sync                              # picks up altair / pandas / pytest
uv run pytest -v                     # control logic green FIRST (CLAUDE.md: TDD)
uv run streamlit run src/power_grid_simulator/app.py

```

Write and run the tests **before** wiring the UI, per CLAUDE.md's TDD rule. While iterating on `app.py`: editing `simulator.py` hot-reloads the module but leaves an instance of the *old* class in `st.session_state`, producing a confusing `AttributeError` — restart the app rather than debug it.

Manual end-to-end check in the browser:

1. Press **Start** with defaults (G101 OFF, CB101 CLOSED). Frequency falls at 0.15 Hz/s and CB101 flips to OPEN on its own before it can cross 49.50, then holds flat at 49.55.
2. Confirm the CB101 button is greyed out at that hold point, the interlock banner explains that G101 must be turned ON, and the trace never crosses either dashed limit line.
3. Toggle **G101 ON**. Frequency climbs at 0.12 Hz/s on the next tick, the CB101 button re-enables once closing is safe, and CB101 auto-CLOSEs before 50.50 — settling into the sawtooth.
4. Click **Pause**: the trace freezes *and stops advancing* (verify t does not creep — this is the `run_every` decoration-time trap). **Reset** returns to t=0 / 50.00 / OFF / CLOSED with the G101 toggle cleared and an empty chart.
5. Click Start/Pause repeatedly and confirm `t` advances only on the timer, never per click.
6. Let it run to the duration: it stops at t=60, Start is disabled, and the page stops rerunning.
