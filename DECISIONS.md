# Decisions

Four things I had to decide while building this, and why I decided them the way I did.

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

## AI Note

Overruling:


I wrote the design first — the run state machine, the credit policy, and the idempotency contract — and then had the model generate code against that spec, file by file, reviewing each one before moving on. Roughly 3/4 of the final code came out of a model; 100% of it I can explain line by line.

I accepted most of the boilerplate as written: the SQLAlchemy model definitions, the Pydantic request schemas, the FastAPI error handlers, and the first draft of the test suite. These are well-trodden patterns, the model's version matched what I'd have written, and I did not rewrite them by hand as it would have bought me nothing. I did read every line.

I overrode it in two places. First, its initial version executed the agent inside the POST handler and returned the finished run. That's simpler, but it makes progress invisible to the client, so I moved execution to a background worker and had the frontend poll — accepting the extra complexity of a per-thread database session because watching steps appear was an explicit requirement. 

Second, it suggested caching results by goal text so identical goals would return the same run. Basically, AI asked me to make the same goal always return the same run_id, but I recognized that was not what the prompt was asking and pushed back because the the assessment requires the opposite. I rejected that: idempotency is about identifying a repeated request, not a repeated string, and only the client knows which it is. (requesting again because it didn't originally output and prompting the same text at a different time are different) That's why the key comes from a header.

The thing it got wrong that I caught: its idempotency check was if key not in db: create_run(). That reads fine and passes a sequential test, but two concurrent retries can both evaluate the condition before either writes, so both create a run and the client gets charged twice. I rewrote it to insert the run and the idempotency record in one transaction and catch the IntegrityError from the unique constraint, letting the database do the locking. 
