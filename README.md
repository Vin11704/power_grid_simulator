## Prerequisites

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

## Assumptions
Assumptions that were not part of the original requirements but were made to complete the implementation:
- if frequency reaches >= 50.50 Hz, CB101 must automatically CLOSE, similar to the under-frequency case.
- bus frequency is stricly only within safe operating bounds; uses predictive auto-interlock logic to prevent frequency from going outside safe operating bounds. This is done by checking the next tick's frequency and automatically opening/closing CB101 if the next tick's frequency is predicted to be outside safe operating bounds
- during recovery, the interlock is latched and the button for CB101 is disabled until bus frequency reaches nominal frequency (50.00 Hz). 
- 