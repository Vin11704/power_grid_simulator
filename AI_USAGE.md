# Model and tool:
Claude Code as a Visual Code Studios Extension. Using models Claude Opus 5 for planning and Claude Sonnet 5 for implementation. 

# Prompts and Output
1. refer to [AI_AI_prompt.md](AI_AI_prompt.md)
    
    **Prompt**: plan this @AI_prompt.md prompt.
    
    **Output**: refer to [first_plan_output.md](first_plan_output.md).

    **Modifications**: 
    - remove any mentions of missing dependencies and clarify by commenting on the claude code plan that dependencies and packages will be added manually. refer to [revised_1_plan_output.md](revised_1_plan_output.md). Packages and libraries are managed using `uv` myself.

    - modified margins on the streamlit UI such that the charts displayed are wide and centered in the middle of the page. Previously, the charts were displayed on the left side of the page with a small width.

2. modify the interlock mechanism to make sure that the button for the open/close CB101 is disabled until it at least reaches the nominal frequency value.

    **Output**: refer to [2nd_plan.md](2nd_plan.md).

    This modification was done as part of an assumption I considered where the nominal frequency value is a critical parameter for the operation of the CB101. By disabling the button until this value is reached, we ensure that the system always returns to its optimal condition before allowing freedom for the operator/user to open or close the CB101. 

    Modification specific to this 2nd prompt:
    - Blocked `is_frequency_unsafe()` from being called in the `is_cb101_locked()` function. This function is unlikely to return `True` unless a frequency at an unsafe level is injected externally. The predictive interlock mechanism already prevents the user from opening or closing CB101 when the frequency is outside the safe band, so this check is redundant and unnecessary.