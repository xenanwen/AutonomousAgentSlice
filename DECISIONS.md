# Decisions

Things I had to decide while building this, 

spent, and how the person could retry or recover.

## How I bounded the loop

Every run gets `max_steps = 5`, copied onto the run row so it stays true even if I change the
constant later. The planner proposes a list of steps, but it doesn't know about the limit — its
job is just to propose work. The loop is what enforces the budget, and it does that in three
places so one mistake can't break it:

1. I only create step rows for the first 5 planned steps, so the loop has nothing to iterate past.
2. The loop checks `current_step >= max_steps` before it charges and runs each step.
3. SQLite has `CHECK (credits_used <= max_steps)`, so a bad write is rejected by the database.

I also store `planned_steps`. If the planner wanted 8 steps and I could only run 5, the run ends
as `failed` with `error = "max_steps_exceeded"` even though every step that did run succeeded.
That way "ran out of budget" is not confused with "finished successfully."

## How I handled a mid-run failure

Tools raise a `ToolError`. The loop catches it, marks that step `failed` with the error message,
marks the run `failed` with the reason, and returns immediately. It does not undo anything.

The steps that already succeeded keep their `completed` status and their results. The failed step
keeps its error. The run stays in SQLite forever so you can look at it later. I don't retry
automatically, because retrying silently would hide the failure — if the user wants to try again
they start a new run with a new id, and the failed one stays there for debugging.

I also add a startup check: if the server was killed while a run was executing, that run would be
stuck in `running` forever, so on startup I mark any leftover `queued`/`running` run as
`failed` with `error = "interrupted"`.

## How I counted credits

One step = 1 credit, stored as an integer. I used an integer instead of a float because credits
are like money and floats drift — `0.1 + 0.2` isn't `0.3` in Python, and I didn't want a balance
that's slightly wrong after a few runs.

The credit is charged when a step **starts**, not when it succeeds. A tool that fails still used
resources, so it still costs its credit, and earlier credits are never refunded. So 2 successful
steps plus 1 failed step = 3 credits, and the run is `failed`. This means `credits_used` always
equals the number of steps that were started, which makes any run easy to check by hand.

## The trade-off I'm least sure about

`POST /runs` returns straight away with `status: "queued"` and the agent runs on a background
thread, so the browser has to poll `GET /runs/{id}` to see progress.

I chose this because the app is supposed to show the steps appearing one at a time, and if POST
blocked until the run finished, there'd be nothing to watch — you'd get the whole result at once.
It's also how real agent APIs work, since real runs take minutes.

But it made the system more complicated than a blocking POST would have been: I need a worker
thread with its own database session, `check_same_thread=False` on SQLite, polling in the
frontend, and the crash-recovery sweep above (which only exists *because* execution happens on a
thread that can die). A blocking POST would have been simpler and the response would have matched
the example in the spec exactly. I still think the polling design is the better one, but it's the
decision I'd most want a second opinion on.


### Maximum step limit

`MAX_STEPS = 5` (in `backend/config.py`).

> **Every run has a hard maximum number of executable steps (change this if you want). If the agent reaches the limit
> without completing, the run transitions to FAILED with `error = "max_steps_exceeded"`, and
> no additional steps are attempted.**

It is enforced in three independent places, so no single bug can defeat it:

1. Only `min(len(plan), max_steps)` step rows are ever created, so the loop physically
   cannot iterate further.
2. The loop re-checks `current_step >= max_steps` before charging each step.
3. The database has `CHECK (credits_used <= max_steps)` and `CHECK (current_step <= max_steps)`.

Because the planner does not know about `MAX_STEPS`, the run also records `planned_steps`.
If `planned_steps > max_steps` the plan could never have fit in the budget, and the run ends
as `max_steps_exceeded` even if every executed step succeeded.


### Failure semantics

A failed run is **evidence**. When a step fails, the system keeps:

- every earlier completed step and its result,
- the failed step, with its own error message and timestamps,
- the exact number of credits already consumed,
- the run-level failure reason (`tool_failed: ...` or `max_steps_exceeded`).

Nothing is erased, nothing is refunded, and the run is never marked completed. It stays in
SQLite so it can be inspected later, and `GET /runs/{run_id}` returns everything the
frontend needs to explain what happened.

There are four failure reasons, all terminal:

- `tool_failed: <message>` — a tool raised during a step.
- `max_steps_exceeded` — the plan did not fit inside the budget.
- `internal_error` — an unexpected bug in our own code, caught so that it still leaves an
  accurate record instead of a lie.
- `interrupted` — the process died while the run was executing (see below).

**Crash recovery.** A run is executed by an in-memory worker thread, so killing the server
mid-run kills the thread with it, and nothing is left to move that run out of `running` — it
would sit there forever and the frontend would poll it forever. On startup the app therefore
sweeps every non-terminal run and closes it out:

```text
startup -> SELECT * FROM runs WHERE status IN ('queued','running')
        -> the in-flight step becomes failed
        -> the run becomes failed with error 'interrupted'
        -> credits already charged are kept, not refunded
```

 **any run that is not actively executing is in a
terminal state.** Recovered runs are logged by id on startup, and the client recovers the
same way it recovers from any failure — by starting a new run.

### Retry and recovery

Failed runs are **not** retried automatically. The original run is preserved, and the client
starts a new one:

```text
run_123 -> FAILED      (kept forever, still readable via GET /runs/run_123)
retry   -> run_124     (a new run, a new idempotency key, its own credits)
```

The old run is never silently mutated into the new one. That keeps the audit trail honest:
if a customer was charged 3 credits for a failed run, that record still exists.

### Idempotency

The client sends an `Idempotency-Key` header. The first request creates the run and stores
`key -> run_id`. Any later request with the same key returns that same run without creating
a second one, executing the agent again, or charging a single extra credit.

**The concurrency part.** This implementation deliberately does *not*
do:

```python
if key not in database:      # <-- two simultaneous requests can BOTH pass this
    create_run()
```

Instead it INSERTs, and lets the **PRIMARY KEY on `idempotency_records`**
reject the loser:

```python
try:
    session.commit()               # run + steps + idempotency record, one transaction
except IntegrityError:             # someone else won the race
    session.rollback()
    return existing_run_for(key)   # treat duplicate-key as "this request already has a run"
```

 The run, its step rows, and the
idempotency record are committed as a single transaction, so it is impossible to end up with
a run that has no key or a key that points at nothing.

The record lives in SQLite rather than in memory, so **restarting the server does not lose
the protection** (there is a test for exactly that).

Reusing one key for a *different* goal returns `409 Conflict`.

### Why SQLite

The requirements are: survive a restart, enforce uniqueness atomically, and be trivial to
run locally. SQLite does all those in one file.


## AI Note

Overruling:


I wrote the design first — the run state machine, the credit policy, and the idempotency contract — and then had the model generate code against that spec, file by file, reviewing each one before moving on. Roughly 3/4 of the final code came out of a model; 100% of it I can explain line by line.

I accepted most of the boilerplate as written: the SQLAlchemy model definitions, the Pydantic request schemas, the FastAPI error handlers, and the first draft of the test suite. These are well-trodden patterns, the model's version matched what I'd have written, and I did not rewrite them by hand as it would have bought me nothing. I did read every line.

I overrode it in two places. First, its initial version executed the agent inside the POST handler and returned the finished run. That's simpler, but it makes progress invisible to the client, so I moved execution to a background worker and had the frontend poll — accepting the extra complexity of a per-thread database session because watching steps appear was an explicit requirement. 

Second, it suggested caching results by goal text so identical goals would return the same run. Basically, AI asked me to make the same goal always return the same run_id, but I recognized that was not what the prompt was asking and pushed back because the the assessment requires the opposite. I rejected that: idempotency is about identifying a repeated request, not a repeated string, and only the client knows which it is. (requesting again because it didn't originally output and prompting the same text at a different time are different) That's why the key comes from a header.

The thing it got wrong that I caught: its idempotency check was if key not in db: create_run(). That reads fine and passes a sequential test, but two concurrent retries can both evaluate the condition before either writes, so both create a run and the client gets charged twice. I rewrote it to insert the run and the idempotency record in one transaction and catch the IntegrityError from the unique constraint, letting the database do the locking. 
