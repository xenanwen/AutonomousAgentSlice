/*
 * Frontend logic. Three jobs:
 *   1. POST /runs with an Idempotency-Key
 *   2. Poll GET /runs/{id} every 500ms while the run is queued or running
 *   3. Render progress, the final result, or the failure state
 */

const POLL_INTERVAL_MS = 500;
const TERMINAL_STATUSES = ["completed", "failed"];

// Elements
const form = document.getElementById("goal-form");
const goalInput = document.getElementById("goal");
const forceFailure = document.getElementById("force-failure");
const runButton = document.getElementById("run-button");
const formError = document.getElementById("form-error");

const runPanel = document.getElementById("run-panel");
const runIdEl = document.getElementById("run-id");
const runStatusEl = document.getElementById("run-status");
const runCreditsEl = document.getElementById("run-credits");
const stepList = document.getElementById("step-list");

const resultBlock = document.getElementById("result-block");
const resultOutput = document.getElementById("result-output");

const failureBlock = document.getElementById("failure-block");
const failureReason = document.getElementById("failure-reason");
const failureCredits = document.getElementById("failure-credits");
const failureCompleted = document.getElementById("failure-completed");

const replayButton = document.getElementById("replay-button");
const replayNote = document.getElementById("replay-note");

// State of the last submitted request, so we can replay it with the SAME key.
let lastRequest = null; // { goal, key, runId }
let pollTimer = null;

/* ------------------------------------------------------------------ */
/* Networking                                                          */
/* ------------------------------------------------------------------ */

async function postRun(goal, idempotencyKey) {
  const response = await fetch("/runs", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      // This header is what makes a retry safe: the server returns the run
      // this key already created instead of starting a second one.
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({ goal }),
  });

  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.message || "The request failed.");
  }
  // 201 = a new run was created, 200 = this was a replayed request.
  return { run: body, created: response.status === 201 };
}

async function fetchRun(runId) {
  const response = await fetch(`/runs/${runId}`);
  const body = await response.json();
  if (!response.ok) throw new Error(body.message || "Could not load the run.");
  return body;
}

/* ------------------------------------------------------------------ */
/* Polling                                                             */
/* ------------------------------------------------------------------ */

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function startPolling(runId) {
  stopPolling();
  pollTimer = setInterval(async () => {
    try {
      const run = await fetchRun(runId);
      render(run);
      // Stop as soon as the run reaches a terminal state - no endless polling.
      if (TERMINAL_STATUSES.includes(run.status)) {
        stopPolling();
        setBusy(false);
      }
    } catch (error) {
      stopPolling();
      setBusy(false);
      showError(error.message);
    }
  }, POLL_INTERVAL_MS);
}

/* ------------------------------------------------------------------ */
/* Rendering                                                           */
/* ------------------------------------------------------------------ */

const STEP_ICONS = {
  pending: "○",   // ○
  running: "→",   // →
  completed: "✓", // ✓
  failed: "✗",    // ✗
};

function render(run) {
  runPanel.hidden = false;
  runIdEl.textContent = run.run_id;

  runStatusEl.textContent = run.status.toUpperCase();
  runStatusEl.className = `status-badge status-${run.status}`;

  runCreditsEl.textContent = `${run.credits_used} / ${run.max_steps}`;

  // Steps
  stepList.innerHTML = "";
  run.steps.forEach((step) => {
    const item = document.createElement("li");
    item.className = `step-${step.status}`;

    const icon = document.createElement("span");
    icon.className = "icon";
    icon.textContent = STEP_ICONS[step.status] || "○";

    const text = document.createElement("div");
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = `${step.step_number}. ${step.name.replace(/_/g, " ")}`;
    text.appendChild(name);

    const note = step.error || step.detail;
    if (note) {
      const detail = document.createElement("div");
      detail.className = "detail";
      detail.textContent = note;
      text.appendChild(detail);
    }

    item.appendChild(icon);
    item.appendChild(text);
    stepList.appendChild(item);
  });

  // Success
  const succeeded = run.status === "completed";
  resultBlock.hidden = !succeeded;
  if (succeeded) resultOutput.textContent = run.output || "";

  // Failure
  const failed = run.status === "failed";
  failureBlock.hidden = !failed;
  if (failed) {
    failureReason.textContent = humanReason(run);
    failureCredits.textContent = String(run.credits_used);
    const done = run.steps.filter((s) => s.status === "completed").map((s) => s.name);
    failureCompleted.textContent = done.length ? done.join(", ") : "none";
  }

  replayButton.hidden = false;
}

function humanReason(run) {
  const error = run.error || "unknown error";
  if (error === "max_steps_exceeded") {
    return `The agent hit its hard limit of ${run.max_steps} steps before finishing (max_steps_exceeded).`;
  }
  if (error.startsWith("tool_failed: ")) {
    return error.slice("tool_failed: ".length);
  }
  return error;
}

function showError(message) {
  formError.textContent = message;
  formError.hidden = false;
}

function clearError() {
  formError.hidden = true;
  formError.textContent = "";
}

function setBusy(busy) {
  runButton.disabled = busy;
  runButton.textContent = busy ? "Running..." : "Run";
}

function resetPanel() {
  stopPolling();
  resultBlock.hidden = true;
  failureBlock.hidden = true;
  replayButton.hidden = true;
  replayNote.hidden = true;
  stepList.innerHTML = "";
  // Clear the previous run's badge immediately, so an old COMPLETED/FAILED
  // state is never shown next to a brand new run.
  runStatusEl.textContent = "QUEUED";
  runStatusEl.className = "status-badge status-queued";
  runIdEl.textContent = "-";
  runCreditsEl.textContent = "0";
}

/* ------------------------------------------------------------------ */
/* Events                                                              */
/* ------------------------------------------------------------------ */

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();

  let goal = goalInput.value.trim();
  if (!goal) {
    showError("Please enter a goal.");
    return;
  }
  // The checkbox appends the documented trigger phrase that makes the mocked
  // search tool raise, so the failure path is easy to demonstrate.
  if (forceFailure.checked && !/force failure/i.test(goal)) {
    goal = `${goal} and force failure`;
  }

  resetPanel();
  setBusy(true);

  // A fresh key per user-initiated submission. Retries reuse it (see below).
  const key = crypto.randomUUID();

  try {
    const { run } = await postRun(goal, key);
    lastRequest = { goal, key, runId: run.run_id };
    render(run);
    startPolling(run.run_id);
  } catch (error) {
    setBusy(false);
    showError(error.message);
  }
});

// Demonstrates idempotency: sends the exact same body with the exact same key.
replayButton.addEventListener("click", async () => {
  if (!lastRequest) return;
  replayNote.hidden = true;
  try {
    const { run, created } = await postRun(lastRequest.goal, lastRequest.key);
    render(run);
    replayNote.hidden = false;
    replayNote.textContent = created
      ? `Unexpected: a new run was created (${run.run_id}).`
      : `Replay returned the same run ${run.run_id} with ${run.credits_used} credits. No second execution, no extra charge.`;
  } catch (error) {
    showError(error.message);
  }
});

// Example chips fill the input.
document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    goalInput.value = chip.dataset.goal;
    forceFailure.checked = false;
  });
});
