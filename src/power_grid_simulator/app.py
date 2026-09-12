"""Streamlit UI for the station power grid simulator.

All control logic lives in ``simulator.py``; this module only renders state and
forwards operator input to it.

Layout is dictated by two Streamlit behaviours, not by taste:

* ``run_every`` is evaluated at *decoration time*, in the main script body. So
  Start / Pause / Reset must live outside the fragment -- a fragment-only rerun
  would never re-evaluate it and the timer would keep firing while paused.
* Widgets outside a fragment do not refresh on timer reruns. So anything that
  must stay current every second -- the metrics, the interlock banner, and the
  CB101 button's disabled state -- has to live inside it.
"""

import time

import altair as alt
import pandas as pd
import streamlit as st

from power_grid_simulator.simulator import (
    CLOSED,
    DEFAULT_DURATION,
    NOMINAL_FREQ,
    OFF,
    ON,
    OPEN,
    OVER_FREQ,
    UNDER_FREQ,
    PowerGridSimulator,
)

# Fire slightly early so timer jitter cannot drop a tick.
TICK_INTERVAL = 0.95
G101_KEY = "g101_toggle"
# Altair refuses to render more than 5000 rows; history is duration + 1.
MAX_DURATION = 3600

LIMIT_COLOUR = "#d62728"
NOMINAL_COLOUR = "#2ca02c"

st.set_page_config(page_title="Power Grid Simulator", layout="wide")


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------

def get_simulator():
    if "sim" not in st.session_state:
        st.session_state.sim = PowerGridSimulator()
        st.session_state.last_tick = 0.0
        # Seed the keyed toggle so it agrees with the model on first render --
        # otherwise st.toggle defaults to False while sim.g101 starts ON.
        st.session_state[G101_KEY] = st.session_state.sim.g101 == ON
    return st.session_state.sim


# --------------------------------------------------------------------------
# Callbacks. These run before the script body, which is why they can touch
# widget state and why control-vs-chart ordering in the script stops mattering.
# --------------------------------------------------------------------------

def on_start():
    st.session_state.sim.start()
    st.session_state.last_tick = time.monotonic()


def on_pause():
    st.session_state.sim.stop()
    st.info("Paused. Click Start to resume.")


def on_reset():
    st.session_state.sim.reset()
    st.session_state.last_tick = time.monotonic()
    # Only legal inside a callback: widgets are not instantiated yet. Synced
    # from the sim rather than hardcoded, so it can't drift from reset()'s
    # actual default.
    st.session_state[G101_KEY] = st.session_state.sim.g101 == ON


def on_g101_change():
    st.session_state.sim.set_g101(st.session_state[G101_KEY])


def on_cb101_click():
    sim = st.session_state.sim
    target = OPEN if sim.cb101 == CLOSED else CLOSED
    if not sim.set_cb101(target):
        # Reachable if the button was rendered before an interlock trip: a
        # widget outside the last rerun can carry a stale disabled flag.
        st.toast(
            f"Interlock: cannot {target.lower()} CB101 at {sim.frequency:.2f} Hz.",
            icon="⚠️",
        )


# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------

def frequency_chart(history):
    """Bus frequency, the two safety limits, and the nominal recovery target."""
    limits = pd.DataFrame(
        {
            "limit": [UNDER_FREQ, OVER_FREQ],
            "label": [
                f"Under-frequency limit {UNDER_FREQ:.2f} Hz",
                f"Over-frequency limit {OVER_FREQ:.2f} Hz",
            ],
        }
    )
    nominal = pd.DataFrame(
        {"limit": [NOMINAL_FREQ], "label": [f"Nominal {NOMINAL_FREQ:.2f} Hz — interlock release"]}
    )

    trace = alt.Chart(history).mark_line(point=True).encode(
        x=alt.X("t:Q", title="Time (s)"),
        y=alt.Y(
            "frequency:Q",
            title="Bus frequency (Hz)",
            # 0.10 Hz of headroom keeps the limit lines and their labels clear
            # of the frame edge. nice=False is required, or Altair rounds the
            # domain outward and the headroom becomes unpredictable.
            scale=alt.Scale(zero=False, domain=[49.40, 50.60], nice=False),
        ),
        tooltip=["t:Q", "frequency:Q", "g101:N", "cb101:N"],
    )
    rules = alt.Chart(limits).mark_rule(
        strokeDash=[2, 3], color=LIMIT_COLOUR, size=1.5,
    ).encode(y="limit:Q")
    labels = alt.Chart(limits).mark_text(
        align="left", baseline="bottom", dx=4, dy=-3, fontSize=11,
        color=LIMIT_COLOUR,
    ).encode(x=alt.value(5), y=alt.Y("limit:Q"), text="label:N")
    nominal_rule = alt.Chart(nominal).mark_rule(
        strokeDash=[2, 3], color=NOMINAL_COLOUR, size=1.5,
    ).encode(y="limit:Q")
    nominal_label = alt.Chart(nominal).mark_text(
        align="left", baseline="bottom", dx=4, dy=-3, fontSize=11,
        color=NOMINAL_COLOUR,
    ).encode(x=alt.value(5), y=alt.Y("limit:Q"), text="label:N")

    return alt.layer(rules, labels, nominal_rule, nominal_label, trace).properties(
        height=300, title="Bus frequency (FS101)"
    )


def state_chart(history, column, title, high_label, low_label, colour):
    """Step chart of a two-valued component state over time."""
    numeric = f"{column}_num"
    data = history.assign(**{numeric: (history[column] == high_label).astype(int)})

    return alt.Chart(data).mark_line(
        interpolate="step-after", size=3, color=colour,
    ).encode(
        x=alt.X("t:Q", title="Time (s)"),
        y=alt.Y(
            f"{numeric}:Q",
            title=title,
            scale=alt.Scale(domain=[-0.2, 1.2]),
            axis=alt.Axis(
                values=[0, 1],
                labelExpr=f"datum.value == 1 ? '{high_label}' : '{low_label}'",
                grid=False,
            ),
        ),
        tooltip=["t:Q", f"{column}:N"],
    ).properties(height=120, title=title)


def render_charts(sim):
    history = pd.DataFrame(sim.history)
    _, col_main, _ = st.columns([1, 10, 1])
    with col_main:
        st.altair_chart(
            alt.vconcat(
                frequency_chart(history),
                state_chart(history, "g101", "G101", ON, OFF, "#2ca02c"),
                state_chart(history, "cb101", "CB101", CLOSED, OPEN, "#1f77b4"),
            ).resolve_scale(x="shared"), use_container_width=True
        )


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------

sim = get_simulator()

st.title("Power Grid Simulator")
st.caption(
    "Station bus: diesel generator **G101**, circuit breaker **CB101** feeding "
    "station load L101, frequency sensor **FS101**. Nominal 50.00 Hz; the bus "
    "must never reach 49.50 Hz or 50.50 Hz."
)

# -- run controls: outside the fragment, so run_every is re-evaluated --------

with st.sidebar:
    st.header("Run control")

    duration = st.number_input(
        "Duration (s)",
        min_value=1,
        max_value=MAX_DURATION,
        value=DEFAULT_DURATION,
        step=10,
        disabled=sim.running,
        help="Simulation length in 1-second ticks.",
    )
    sim.duration = int(duration)

    start_col, pause_col, reset_col = st.columns(3)
    start_col.button(
        "Start",
        on_click=on_start,
        disabled=sim.running or sim.is_finished,
        width="stretch",
    )
    pause_col.button(
        "Pause", on_click=on_pause, disabled=not sim.running, width="stretch"
    )
    reset_col.button("Reset", on_click=on_reset, width="stretch")

    if sim.is_finished:
        st.info(f"Run complete at t = {sim.t} s. Reset to run again.")

# run_every must be computed here, in the main script body.
run_every = "1s" if (sim.running and not sim.is_finished) else None


@st.fragment(run_every=run_every)
def live_view():
    sim = st.session_state.sim

    if sim.running and not sim.is_finished:
        now = time.monotonic()
        # Wall-clock guard: a full rerun re-executes this fragment, so without
        # it every button click would inject an extra tick. Also keeps one tick
        # to roughly one real second.
        if now - st.session_state.last_tick >= TICK_INTERVAL:
            st.session_state.last_tick = now
            sim.tick()
            if sim.is_finished:
                # The run ended inside a timer rerun, so run_every above was
                # never re-evaluated. Without this the fragment would rerun
                # every second forever.
                st.rerun(scope="app")

    locked = sim.is_cb101_locked()

    metric_cols = st.columns(4)
    metric_cols[0].metric("Bus frequency", f"{sim.frequency:.2f} Hz",
                          delta=f"{sim.frequency - 50.00:+.2f} vs nominal")
    metric_cols[1].metric("Time", f"{sim.t} / {sim.duration} s")
    metric_cols[2].metric("G101", sim.g101)
    metric_cols[3].metric("CB101", sim.cb101)

    control_cols = st.columns([1, 1, 1])
    control_cols[0].toggle(
        "G101 generator",
        key=G101_KEY,
        on_change=on_g101_change,
        help="Manual only -- the generator has no interlock.",
    )
    control_cols[1].button(
        "Open CB101" if sim.cb101 == CLOSED else "Close CB101",
        on_click=on_cb101_click,
        disabled=locked,
        width="stretch",
        help="Disabled while the interlock would immediately revert the change.",
    )

    if locked:
        if sim.cb101 == OPEN:
            recovery = (
                "Turn G101 ON to restore frequency."
                if sim.g101 == OFF
                else "Frequency is recovering."
            )
            control_cols[2].warning(
                f"**INTERLOCK** — CB101 held OPEN at {sim.frequency:.2f} Hz. "
                f"It stays locked open until the bus recovers to "
                f"{NOMINAL_FREQ:.2f} Hz, even though frequency is already back "
                f"inside the safe band. {recovery}"
            )
        else:
            control_cols[2].warning(
                f"**INTERLOCK** — CB101 held CLOSED at {sim.frequency:.2f} Hz. "
                f"It stays locked closed until the bus falls back to "
                f"{NOMINAL_FREQ:.2f} Hz, even though frequency is already back "
                f"inside the safe band."
            )

    render_charts(sim)


live_view()

st.caption(
    "The interlock is *predictive*: CB101 moves on the tick before a limit "
    "would be crossed, so the bus never reaches 49.50 / 50.50 Hz. Once tripped, "
    "it also *latches*: the breaker stays forced in its safety position, and "
    "the button stays disabled, for the whole recovery -- releasing only once "
    "the bus is back at 50.00 Hz, not just back inside the safe band."
)
