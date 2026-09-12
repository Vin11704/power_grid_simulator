"""Unit tests for the power grid control logic.

Deliberately imports nothing from streamlit, pandas or altair: `simulator.py`
is UI-independent and these tests prove it stays that way.
"""

import inspect
import random
import re

import pytest

from power_grid_simulator.simulator import (
    CLOSED,
    OFF,
    ON,
    OPEN,
    OVER_FREQ,
    RATES,
    UNDER_FREQ,
    PowerGridSimulator,
)

ALL_STATES = [(g, cb) for g in (ON, OFF) for cb in (OPEN, CLOSED)]


def running_sim(**kwargs):
    """A started simulator with a duration long enough not to interfere."""
    sim = PowerGridSimulator(duration=kwargs.pop("duration", 10_000))
    for attr, value in kwargs.items():
        setattr(sim, attr, value)
    sim.start()
    return sim


def run_ticks(sim, n):
    """Tick n times and return the frequency trace of those ticks."""
    trace = []
    for _ in range(n):
        sim.tick()
        trace.append(sim.frequency)
    return trace


# --------------------------------------------------------------------------
# Construction / reset
# --------------------------------------------------------------------------

def test_initial_state():
    sim = PowerGridSimulator()
    assert sim.t == 0
    assert sim.frequency == 50.00
    assert sim.g101 == OFF
    assert sim.cb101 == CLOSED
    assert sim.running is False


def test_reset_records_the_t0_row():
    """Without this row the chart starts at 49.85 and hides the 50.00 start."""
    sim = PowerGridSimulator()
    assert sim.history == [
        {"t": 0, "frequency": 50.00, "g101": OFF, "cb101": CLOSED}
    ]


def test_reset_after_ticks_restores_initial_state():
    sim = running_sim()
    sim.set_g101(True)
    run_ticks(sim, 20)
    sim.reset()

    assert (sim.t, sim.frequency, sim.g101, sim.cb101) == (0, 50.00, OFF, CLOSED)
    assert sim.running is False
    assert len(sim.history) == 1


def test_init_delegates_to_reset():
    """DRY: construction must not duplicate reset()'s state setup."""
    fresh = PowerGridSimulator(duration=60)
    used = PowerGridSimulator(duration=60)
    used.start()
    run_ticks(used, 5)
    used.reset()

    assert used.__dict__ == fresh.__dict__


# --------------------------------------------------------------------------
# Rate table
# --------------------------------------------------------------------------

def test_rate_table_is_total():
    """A missing key would surface as a KeyError mid-tick."""
    assert set(RATES) == set(ALL_STATES)


@pytest.mark.parametrize(
    ("g101", "cb101", "expected"),
    [
        (ON, CLOSED, -0.05),
        (ON, OPEN, +0.12),
        (OFF, CLOSED, -0.15),
        (OFF, OPEN, 0.00),
    ],
)
def test_rate_table_applied_by_tick(g101, cb101, expected):
    sim = running_sim(g101=g101, cb101=cb101)
    before = sim.frequency
    sim.tick()
    assert round(sim.frequency - before, 2) == expected


# --------------------------------------------------------------------------
# Predictive interlock: the core of the design
# --------------------------------------------------------------------------

def test_cb101_does_not_flip_before_a_predicted_breach():
    sim = running_sim()
    for expected in (49.85, 49.70, 49.55):
        sim.tick()
        assert sim.frequency == expected
        assert sim.cb101 == CLOSED


def test_flip_happens_one_tick_early_and_applies_the_new_rate():
    """The single most important test of the predictive design.

    At 49.55 the next -0.15 step would land on 49.40, so CB101 opens *first*
    and the (OFF, OPEN) rate of 0.00 is applied instead. Frequency must stay
    at 49.55 -- if it moved, the stale rate was used.
    """
    sim = running_sim()
    run_ticks(sim, 3)
    assert (sim.frequency, sim.cb101) == (49.55, CLOSED)

    sim.tick()
    assert sim.cb101 == OPEN
    assert sim.frequency == 49.55


def test_golden_trace_with_g101_off():
    sim = running_sim()
    assert run_ticks(sim, 6) == [49.85, 49.70, 49.55, 49.55, 49.55, 49.55]
    assert [row["cb101"] for row in sim.history] == [
        CLOSED, CLOSED, CLOSED, CLOSED, OPEN, OPEN, OPEN
    ]


def test_under_frequency_latch_is_stable():
    """With no generation the frequency is held, not driven below the limit."""
    sim = running_sim()
    run_ticks(sim, 4)
    assert set(run_ticks(sim, 50)) == {49.55}
    assert sim.cb101 == OPEN


def test_steady_state_cycle_with_g101_on():
    """Regression lock on the sawtooth: period 51, band [49.53, 50.49].

    Hand-derived: 49.66 up to 50.38 -> close -> down to 49.53 -> open (24 ticks),
    then 49.65 up to 50.49 -> close -> down to 49.54 -> open (27 ticks). The two
    sub-cycles differ because 0.12 and 0.05 are incommensurate on the 0.01 grid.
    """
    sim = running_sim()
    sim.set_g101(True)
    run_ticks(sim, 100)                      # let transients die out

    seen = {}
    for i in range(200):
        state = (sim.frequency, sim.cb101)
        if state in seen:
            assert i - seen[state] == 51
            break
        seen[state] = i
        sim.tick()
    else:
        pytest.fail("no cycle detected within 200 ticks")

    band = [f for f, _ in seen]
    assert (min(band), max(band)) == (49.53, 50.49)


@pytest.mark.parametrize(("g101", "cb101"), ALL_STATES)
def test_frequency_never_reaches_the_limits(g101, cb101):
    sim = running_sim(g101=g101, cb101=cb101)
    for frequency in run_ticks(sim, 500):
        assert UNDER_FREQ < frequency < OVER_FREQ


# --------------------------------------------------------------------------
# Manual control and the interlock override
# --------------------------------------------------------------------------

def test_set_g101_is_never_blocked():
    sim = running_sim()
    run_ticks(sim, 4)                        # sit at the under-frequency latch
    assert sim.is_cb101_locked() is True

    sim.set_g101(True)
    assert sim.g101 == ON
    sim.set_g101(False)
    assert sim.g101 == OFF


def test_set_cb101_rejected_when_it_would_be_reverted():
    """The setter, not just the greyed-out button, must enforce the interlock."""
    sim = running_sim()
    run_ticks(sim, 4)
    assert (sim.frequency, sim.cb101) == (49.55, OPEN)

    assert sim.set_cb101(CLOSED) is False
    assert sim.cb101 == OPEN                 # unchanged


def test_set_cb101_allowed_mid_band():
    sim = running_sim()
    assert sim.set_cb101(OPEN) is True
    assert sim.cb101 == OPEN
    assert sim.set_cb101(CLOSED) is True
    assert sim.cb101 == CLOSED


def test_set_cb101_allowed_while_paused():
    sim = PowerGridSimulator()
    assert sim.running is False
    assert sim.set_cb101(OPEN) is True


def test_interlock_override_beats_a_refused_command():
    sim = running_sim()
    run_ticks(sim, 4)
    assert sim.set_cb101(CLOSED) is False
    assert sim.set_cb101(CLOSED, override_interlock=True) is True
    assert sim.cb101 == CLOSED


def test_is_cb101_locked_is_false_mid_band():
    sim = running_sim()
    assert sim.is_cb101_locked() is False


def test_is_frequency_unsafe_is_false_in_normal_operation():
    """Kept as the spec-literal check even though the predictive interlock
    means it never fires on its own."""
    sim = running_sim()
    sim.set_g101(True)
    run_ticks(sim, 200)
    assert sim.is_frequency_unsafe() is False


# --------------------------------------------------------------------------
# DRY: the spec explicitly requires the interlock to reuse the setter
# --------------------------------------------------------------------------

def test_auto_interlock_reuses_set_cb101(monkeypatch):
    sim = running_sim()
    calls = []
    original = sim.set_cb101

    def spy(state, **kwargs):
        calls.append((state, kwargs))
        return original(state, **kwargs)

    monkeypatch.setattr(sim, "set_cb101", spy)
    run_ticks(sim, 4)                        # trips the under-frequency interlock

    assert calls == [(OPEN, {"override_interlock": True})]
    assert sim.cb101 == OPEN


def test_cb101_has_a_single_assignment_site():
    """set_cb101 must stay the only writer, so the interlock cannot be bypassed.

    The negative lookahead matters: `self.cb101 == CLOSED` is a comparison, not
    an assignment.
    """
    source = inspect.getsource(PowerGridSimulator)
    assert len(re.findall(r"self\.cb101\s*=(?!=)", source)) == 1


# --------------------------------------------------------------------------
# Load-bearing assumptions about the rate table
# --------------------------------------------------------------------------

def test_opening_never_lowers_and_closing_never_raises_frequency():
    """Soundness premise: OPEN is the recovery direction for under-frequency
    and CLOSED for over-frequency. Silently false if the table is edited."""
    for g101 in (ON, OFF):
        assert RATES[(g101, OPEN)] >= 0
        assert RATES[(g101, CLOSED)] <= 0


def test_no_single_step_can_cross_the_whole_band():
    """Guarantees a flip cannot breach the *opposite* limit in one tick."""
    assert max(abs(rate) for rate in RATES.values()) < OVER_FREQ - UNDER_FREQ


# --------------------------------------------------------------------------
# Property / fuzz tests over random operator behaviour
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(50))
def test_fuzz_random_operator_actions_stay_safe(seed):
    rng = random.Random(seed)
    sim = running_sim()

    for _ in range(200):
        if rng.random() < 0.25:
            sim.set_g101(rng.random() < 0.5)
        if rng.random() < 0.25:
            sim.set_cb101(rng.choice([OPEN, CLOSED]))

        # The lock predicate the UI uses must agree with what the setter does.
        locked = sim.is_cb101_locked()
        before = sim.cb101
        opposite = OPEN if before == CLOSED else CLOSED
        accepted = sim.set_cb101(opposite)
        sim.cb101 = before                   # restore; probe must not perturb the run
        assert locked is (accepted is False)

        sim.tick()

    for row in sim.history:
        # The spec's hardest constraint.
        assert not (row["cb101"] == CLOSED and row["frequency"] < UNDER_FREQ)
        assert UNDER_FREQ < row["frequency"] < OVER_FREQ
        # The invariant that makes comparing raw sums against stored values safe.
        assert row["frequency"] == round(row["frequency"], 2)


@pytest.mark.parametrize("seed", range(20))
def test_fuzz_history_is_self_consistent(seed):
    """Each recorded step equals the rate of its own recorded state, which
    proves _record() runs *after* the interlock rather than before it."""
    rng = random.Random(seed)
    sim = running_sim()

    for _ in range(200):
        if rng.random() < 0.25:
            sim.set_g101(rng.random() < 0.5)
        sim.tick()

    for previous, current in zip(sim.history, sim.history[1:]):
        expected = RATES[(current["g101"], current["cb101"])]
        assert round(current["frequency"] - previous["frequency"], 2) == expected


# --------------------------------------------------------------------------
# Injected out-of-band states: the reactive branch and its known limits
# --------------------------------------------------------------------------

def test_reactive_branch_opens_cb101_for_an_injected_under_frequency():
    """The only test that exercises the reactive branch, which is unreachable
    in normal operation -- keep it so the branch is not deleted as dead code."""
    sim = running_sim(frequency=49.45)
    assert sim.cb101 == CLOSED
    sim.tick()
    assert sim.cb101 == OPEN


def test_interlock_cannot_recover_under_frequency_without_generation():
    """Documents a real limitation: (OFF, OPEN) is 0.00 Hz/s, so the interlock
    can halt an under-frequency decay but not reverse it."""
    sim = running_sim(frequency=49.45)
    run_ticks(sim, 20)
    assert (sim.frequency, sim.cb101) == (49.45, OPEN)

    sim.set_g101(True)                       # generation is the only way back
    run_ticks(sim, 5)
    assert sim.frequency > UNDER_FREQ


def test_injected_over_frequency_recovers():
    sim = running_sim(frequency=50.55, g101=ON, cb101=OPEN)
    assert run_ticks(sim, 2) == [50.50, 50.45]
    assert sim.cb101 == CLOSED


# --------------------------------------------------------------------------
# Lifecycle: start / stop / duration
# --------------------------------------------------------------------------

def test_tick_is_a_noop_while_stopped():
    sim = PowerGridSimulator()
    sim.tick()
    assert (sim.t, sim.frequency, len(sim.history)) == (0, 50.00, 1)


def test_start_and_stop():
    sim = PowerGridSimulator()
    sim.start()
    assert sim.running is True
    sim.tick()
    sim.stop()
    assert sim.running is False
    sim.tick()
    assert sim.t == 1


def test_run_stops_at_the_configured_duration():
    sim = PowerGridSimulator(duration=10)
    sim.start()
    run_ticks(sim, 25)

    assert sim.t == 10
    assert sim.is_finished is True
    assert sim.running is False
    assert len(sim.history) == 11         # duration + 1, including the t=0 row


def test_duration_reduced_below_current_t_finishes_the_run():
    sim = PowerGridSimulator(duration=60)
    sim.start()
    run_ticks(sim, 10)

    sim.duration = 5
    assert sim.is_finished is True
    sim.tick()
    assert sim.t == 10                    # no further advance


def test_zero_duration_never_runs():
    sim = PowerGridSimulator(duration=0)
    assert sim.is_finished is True
    sim.start()
    sim.tick()
    assert sim.t == 0
