# Context

Build a power grid simulator. This will be a streamlit app that models bus frequency, applies control logic and lets an operator interact with it live while it runs. 

# System Model
Main system components: 
- Generator G101: the station's sole diesel generator
- Circuit breaker CB101: connects or disconnects the station load from the bus
- Station load L101: everything the station needs to stay alive (this is implicit as represented by CB101's state)
- Frequency sensor FS101: measures the bus frequency

Optimal nominal frequency: 50 Hz 
Over-frequency: > 50.50 Hz (must never reach this value)
Under-frequency: < 49.50 Hz (must never reach this value)
CB101 must NEVER be CLOSED while frequency is below 49.50 Hz  

Frequency rate of change per second, by state:

| G101 | CB101  | Rate         |
|------|--------|--------------|
| ON   | CLOSED | -0.05 Hz/s   |
| ON   | OPEN   | +0.12 Hz/s   |
| OFF  | CLOSED | -0.15 Hz/s   |
| OFF  | OPEN   | 0 Hz/s (no change)       |

Simulation steps in discrete 1-second time steps (ticks) over a configurable duration.

# Control logic 
- G101 has a manual ON/OFF toggle. No automatic trigger. Default state upon running simulator: OFF
- CB101 has a manual OPEN/CLOSE toggle. Default state upon running simulator: CLOSEDThere is an automatic interlock state that is triggered when the frequency is outside the safe range:
    - if frequency reaches <= 49.50 Hz, CB101 must automatically OPEN, regardless of manual toggle state (streamlit will change display from CLOSED to OPEN where applicable)
    - if frequency reaches >= 50.50 Hz, CB101 must automatically CLOSE, regardless of manual toggle state (streamlit will change display from OPEN to CLOSED where applicable)
    - The operator can also manually close CB101 at any time the frequency is > 49.50 Hz.
    - The operator can also manually open CB101 at any time the frequency is < 50.50 Hz.
- UI must make the interlock visible by graying out and disabling the OPEN/CLOSE button whenever the frequency is outside the safe range. The button should be re-enabled when the frequency returns to the safe range.

# Interaction
Make the simulator feel "live" and not batch:
- User/Operator can flip G101 ON/OFF and CB101 OPEN/CLOSE (both subject to the interlock) while simulation is running, and be able to see the effect on subsequent ticks.
- Use `st.session_state` to hold running history (t, GB101, CB101, frequency) and current simulator state.
- Use `st.fragment(run_every='1s')` to run the simulation in 1-second ticks, this will help the simulation to run on its own timer while the rest of the page stays responsive. 
- Provide Start, Pause and Reset buttons to control the simulation. Provide configurable time duration (e.g. number input in seconds, set default to 60 seconds).

# Backend
Simulation as a Python class (PowerGridSimulator) in the file `simulator.py`. This must be kept independent from `app.py` where the streamlit is kept, such that unit test can easily be implemented. This should own the simulator state (t, frequency, G101, CB101, history) and exposes these functions:
- tick(): a method that advances per step (applies the rate table mentioned in System Model, auto-interlock, records history)
- method to set G101 ON/OFF
- method to set CB101 OPEN/CLOSE 
- method to automatically OPEN/CLOSE CB101 based on the interlock logic condition. Reuse the same method that sets CB101 OPEN/CLOSE to avoid code duplication. The UI will query each rerun to know whether the interlock is active and disable the button accordingly.
- a reset method. 
- a start method.
- a stop method. 

Wire each button/toggle in app.py to the corresponding method on one GridSimulator instance held in st.session_state. Button clicks should update the state immediately, independent of the tick timer. Only the st.fragment(run_every="1s") block should call tick().

# Display Requirementas
- Bus frequency line chart, with the 49.50 Hz and 50.50 Hz limits drawn as clearly labeled horizontal reference lines.
- G101 state (ON/OFF) over time.
- CB101 state (OPEN/CLOSED) over time.

For this, use altair as it has good integration support with streamlit, rather than streamlit's `line_chart`. 

# Additional Information
- Leave all markdown files untouched. You may read them, but do not edit or write in any of them. 
- Do include basic unit tests for the simulation/control logic module