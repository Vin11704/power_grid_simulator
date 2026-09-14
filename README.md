# Project Overview

A simulator for the single-bus power distribution system of a remote Arctic research station,
where one diesel generator carries heating, life support, communications and lab equipment. It
models the physical process, implements the control logic that keeps the bus inside its safe
operating band, and displays the resulting system state graphically over the run.

### System model

Station bus: diesel generator **G101**, circuit breaker **CB101**, feeding station load
**L101**, frequency sensor **FS101**. Nominal frequency is 50.00 Hz, with 49.50 Hz the
under-frequency limit and 50.50 Hz the over-frequency limit.

Bus frequency moves at a fixed rate determined entirely by the generator and breaker states:

| G101 | CB101 | Frequency rate | Meaning |
| --- | --- | --- | --- |
| ON | CLOSED | -0.05 Hz/s | station load drawing power |
| ON | OPEN | +0.12 Hz/s | no load, generator spinning freely |
| OFF | CLOSED | -0.15 Hz/s | load draining stored energy |
| OFF | OPEN | 0.00 Hz/s | generator off, load disconnected |

The simulation advances in discrete 1-second ticks over a configurable duration (60 seconds by default)

### Control logic

CB101 is governed by a safety interlock that keeps the bus strictly inside the safe band. It
moves the breaker *before* a limit would be reached rather than reacting once one has been
crossed, and after tripping it holds the breaker in its safety position, disabling the circuit breaker button from manual operation, until the bus has fully recovered to nominal frequency (50 Hz). G101 is never interlocked and stays under manual control throughout (mimicking a realistic scenario where the generator is not subject to automatic control). See [Assumptions](#assumptions) for what this behaviour
is and [Design notes](#design-notes) for why it was built this way.

### Operating the simulator

- **Run control** (sidebar) — set the duration, then Start, Pause and Reset.
- **Live metrics** — bus frequency with its delta against nominal, elapsed time, and current
  G101 and CB101 state, refreshed each second while running.
- **G101 toggle** — always available; the generator has no interlock.
- **CB101 open/close button** — disables itself whenever the interlock would immediately
  revert the command, alongside a banner naming what is being held and what will release it.
- **Charts** — bus frequency as reported by FS101 with both limits and the nominal release
  line marked, above step charts of G101 and CB101 state on a shared time axis.

### Project layout

- `src/power_grid_simulator/simulator.py` — state and safety logic. Imports no Streamlit,
  pandas or Altair, so it can be unit tested and driven on its own.
- `src/power_grid_simulator/app.py` — Streamlit UI and Altair charts. Renders state and
  forwards operator input; holds no control logic.
- `tests/test_simulator.py` — pytest suite covering the rate table, interlock timing, latch
  behaviour, command gating and the safety invariants.
- `AI_USAGE.md` — AI usage disclosure. `AI_prompt.md`, `first_plan_output.md`,
  `revised_1_plan_output.md` and `2nd_plan.md` are the planning artifacts behind it.

# Prerequisites

- git
- uv (package manager)
    
    run the following in a terminal if uv is not installed:
    `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

# Setup (Windows)

### Clone Repository:
``` powershell
git clone <repo>
cd <repo>
```

### Create Virtual Environment and Install Dependencies
```powershell
uv sync
```

### Run application
```powershell
uv run streamlit run src/power_grid_simulator/app.py   
```


## Assumptions
Assumptions that were not part of the original requirements but were made to complete the implementation:
- if frequency reaches >= 50.50 Hz, CB101 must automatically CLOSE, similar to the under-frequency case.
- bus frequency is stricly only within safe operating bounds; uses predictive auto-interlock logic to prevent frequency from going outside safe operating bounds. This is done by checking the next tick's frequency and automatically opening/closing CB101 if the next tick's frequency is predicted to be outside safe operating bounds
- during recovery, the interlock is latched and the button for CB101 is disabled until bus frequency reaches nominal frequency (50.00 Hz). 

## Design notes

- **Predictive, not reactive.** The interlock checks the frequency the next tick *would*
  produce and moves CB101 first, so the bus never actually reaches a limit. A reactive check would have to record a breaching value before it could respond to it, meaning the frequency would have already gone outside the safe band.
- **The latch releases at 50.00 Hz, not at the band edge**, so the breaker and its button
  don't chatter right at the limit — see [Assumptions](#assumptions) for the observable
  behaviour this produces.
- **Single-writer discipline.** `set_cb101` is the only writer of CB101's state, and the
  interlock's own enforcement step is the only writer of the latch. That keeps the function
  that decides what CB101 *should* be a pure query, safe for the UI to poll every render
  without side effects.

See `AI_USAGE.md` for the prompt-by-prompt generation of this implementation.
