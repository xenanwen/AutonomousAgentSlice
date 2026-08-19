"""
Central place for every policy constant in the system.

Anything that answers "how much?" or "how many?" lives here, so that the
policy of the system can be read in one screen instead of being scattered
across the code.
"""

import os

# ---------------------------------------------------------------------------
# Agent execution policy
# ---------------------------------------------------------------------------

# Hard upper bound on how many steps a single run may ever execute.
# This is what makes runaway execution impossible: the loop can physically
# not perform step number MAX_STEPS + 1.
MAX_STEPS = 5

# Every executed step costs exactly this many credits. An INTEGER on purpose:
# credits are a money-like counter and floating point values drift.
STEP_COST = 1

# Artificial pause between steps so a human can actually watch progress in the
# browser. Tests set AGENT_STEP_DELAY=0 to keep the suite fast and deterministic.
STEP_DELAY_SECONDS = float(os.getenv("AGENT_STEP_DELAY", "0.6"))

# ---------------------------------------------------------------------------
# Input validation policy
# ---------------------------------------------------------------------------

MAX_GOAL_LENGTH = 2000

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

# Path of the SQLite file. Overridable so tests can use a throwaway database.
DB_PATH = os.getenv("AGENT_DB_PATH", "agent.db")
