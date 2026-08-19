"""
The mocked "model".

In a real agent this is where you would call an LLM and ask "what should I do
next?". Here we map the goal text to a fixed list of step names.

The important thing is the SHAPE: the rest of the system asks the planner for
a plan and does not care how the plan was produced. Swapping this file for a
real LLM call would not require changing the agent loop, the service, or the
API.
"""

from typing import List

# Step names the loop knows how to execute (see loop.py).
STEP_PLANNING = "planning"
STEP_SEARCH = "search"
STEP_ANALYZE = "analyze"
STEP_SUMMARIZE = "summarize"
STEP_WRITE_RESULT = "write_result"

# Goals containing these ask for a longer, analysis-flavoured plan.
_ANALYSIS_KEYWORDS = ("analyze", "analyse", "analysis", "compare", "deep dive")

# Goals containing these produce a plan that is deliberately longer than
# MAX_STEPS, so the run is guaranteed to hit the execution limit.
_RUNAWAY_KEYWORDS = ("never finish", "loop forever", "run forever", "unbounded")


def plan(goal: str) -> List[str]:
    """
    Decide the sequence of steps for this goal. Deterministic: the same goal
    always produces the same plan, which makes the tests reproducible.

    Note the planner does NOT know about MAX_STEPS. Its job is to propose work;
    enforcing the budget is the agent loop's job. Keeping those two concerns
    separate is what lets us detect "the plan could not fit in the budget".
    """
    lowered = goal.lower()

    if any(keyword in lowered for keyword in _RUNAWAY_KEYWORDS):
        # 8 steps, which is more than MAX_STEPS (5) -> the loop will stop it.
        return [
            STEP_PLANNING,
            STEP_SEARCH,
            STEP_ANALYZE,
            STEP_SEARCH,
            STEP_ANALYZE,
            STEP_SEARCH,
            STEP_ANALYZE,
            STEP_WRITE_RESULT,
        ]

    if any(keyword in lowered for keyword in _ANALYSIS_KEYWORDS):
        return [STEP_PLANNING, STEP_SEARCH, STEP_ANALYZE, STEP_WRITE_RESULT]

    # Default plan.
    return [STEP_PLANNING, STEP_SEARCH, STEP_SUMMARIZE]
