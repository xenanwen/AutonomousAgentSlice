"""
The agent's tools. Both of them are mocked on purpose.

Why mocked: the assessment is about reliable execution, not about integrating
a search API. A mocked tool is also *deterministic*, which means the tests
give the same answer every single time they run.

Each tool can be told to fail by putting a trigger phrase in the goal. That is
how we demonstrate mid-run failure without depending on a flaky external
service.
"""

from typing import Dict, List

# Putting one of these in the goal makes fake_search() raise.
SEARCH_FAILURE_TRIGGERS = ("fail_tool", "force failure", "force-failure")

# Putting this in the goal makes fake_writer() raise, i.e. the run fails at the
# LAST step instead of the middle one. Useful for showing that several
# successful steps are preserved when a later step blows up.
WRITER_FAILURE_TRIGGERS = ("fail_writer", "fail writer")


class ToolError(Exception):
    """Raised when a tool cannot complete its work. The agent loop catches this."""


def _contains_any(text: str, triggers) -> bool:
    lowered = text.lower()
    return any(trigger in lowered for trigger in triggers)


def _topic_from(query: str) -> str:
    """
    Pull a rough 'topic' out of the query so the fake results look tailored.

    Purely cosmetic string handling - no cleverness intended.
    """
    noise = {
        "research", "about", "the", "a", "an", "and", "of", "on", "for",
        "please", "write", "summary", "summarise", "summarize", "analyze",
        "analyse", "report", "me", "force", "failure", "fail_tool", "into",
        "find", "out", "info", "information", "give", "tell", "fail_writer",
        "writer", "fail", "never", "finish", "keep", "going", "forever",
    }
    words = [w.strip(".,!?;:").lower() for w in query.split()]
    meaningful = [w for w in words if w and w not in noise]
    if not meaningful:
        return query.strip() or "the requested topic"
    return " ".join(meaningful[:2]).title()


def fake_search(query: str) -> Dict[str, object]:
    """
    A deterministic stand-in for a web search API.

    Same query in -> same results out, every single time.
    Raises ToolError when the query contains a search failure trigger.
    """
    if _contains_any(query, SEARCH_FAILURE_TRIGGERS):
        raise ToolError("Mock search tool failed: simulated upstream outage.")

    topic = _topic_from(query)
    results: List[str] = [
        f"{topic} is a general-purpose subject with broad practical use.",
        f"{topic} is commonly used in professional software development.",
        f"{topic} is widely discussed in data and research contexts.",
    ]
    return {"query": query, "results": results}


def fake_writer(goal: str, findings: List[str]) -> str:
    """
    A deterministic stand-in for 'ask the model to write the final answer'.

    Raises ToolError when the goal contains a writer failure trigger.
    """
    if _contains_any(goal, WRITER_FAILURE_TRIGGERS):
        raise ToolError("Mock writer tool failed: simulated model timeout.")

    topic = _topic_from(goal)
    body = " ".join(findings) if findings else "No findings were gathered."
    return f"Summary for goal '{goal.strip()}':\n\n{body}\n\nConclusion: {topic} is well documented across the sources reviewed."
