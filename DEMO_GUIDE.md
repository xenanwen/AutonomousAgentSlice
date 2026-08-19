# Video demo guide

A script for recording a ~4 minute walkthrough of the three required scenarios.
Everything below has been run end to end; nothing here is guesswork.

---

## Before you hit record

**1. Start clean.** A fresh database means small, readable run numbers and no leftover state:

```bash
cd kredete-agent-slice
source .venv/bin/activate
rm -f agent.db
uvicorn backend.main:app --reload
```

**2. Open two windows side by side:**

- **Browser** at `http://127.0.0.1:8000` (type `http://` explicitly so it doesn't jump to https)
- **Terminal**, in the project folder with the venv activated

**3. Make text big.** Browser: `Cmd +` two or three times. Terminal: bump the font to ~18pt.
Whoever watches this will be on a laptop screen.

**4. Optional but nice:** have `backend/agent/loop.py` open in an editor tab so you can point at
the actual loop when you explain the bound.

**5. Practice once without recording.** The whole thing takes about four minutes, and the agent
takes ~2 seconds per step, so there are natural pauses you can talk through.

**To reset between takes:** stop the server (`Ctrl+C`), `rm agent.db`, start it again.

---

## Scenario 1 — a goal that completes

**In the browser:**

1. Type `Research Python` into the goal box.
2. Click **Run**.
3. **Don't talk over the first two seconds** — let the viewer see the steps changing:
   `→ Search` in amber, then `✓ Search` in green, one at a time. That's the whole point of the
   progress design and it's over quickly.
4. When it finishes, point at the three things on screen:
   - **STATUS: COMPLETED**
   - **CREDITS USED: 3 / 5**
   - the **Final result** box with the summary text

**What to say:** see transcript §1.

The number to say out loud is **3 credits for 3 steps** — that's the invariant you want the
viewer to remember before scenario 3 tests it.

---

## Scenario 2 — the retried request

This is the one that needs the clearest framing, because the interesting part is what *doesn't*
happen. Do it twice: once visually, once with the raw HTTP.

### Part A — in the browser

1. The run from scenario 1 is still on screen. Scroll to the bottom.
2. Click **"Resend the same request (same Idempotency-Key)"**.
3. A green line appears:
   > *Replay returned the same run run_xxxx with 3 credits. No second execution, no extra charge.*
4. Point out that the **RUN ID at the top did not change** and **CREDITS USED is still 3 / 5**.

### Part B — in the terminal (the proof)

Run these two commands. They're byte-identical except that the second one is the "retry":

```bash
curl -i -X POST http://127.0.0.1:8000/runs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-retry-1' \
  -d '{"goal":"Summarize the Go language"}'

# ... now imagine the response above was lost in the network ...

curl -i -X POST http://127.0.0.1:8000/runs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-retry-1' \
  -d '{"goal":"Summarize the Go language"}'
```

Point at the first line of each response:

```
first  ->  HTTP/1.1 201 Created     run_id = run_8995f3bf16e3
second ->  HTTP/1.1 200 OK          run_id = run_8995f3bf16e3     <- same run
```

Then prove there's only one run, straight from the database:

```bash
python -c "import sqlite3; print(sqlite3.connect('agent.db').execute(\"select run_id, status, credits_used from runs where goal='Summarize the Go language'\").fetchall())"
```

```
[('run_8995f3bf16e3', 'completed', 3)]
```

**One row. Three credits.** (macOS also ships the `sqlite3` CLI if you prefer:
`sqlite3 agent.db "select run_id, status, credits_used from runs;"` — but test it before you
record, since the Python one-liner works everywhere.)

**What to say:** see transcript §2.

---

## Scenario 3 — a tool fails partway

**In the browser:**

1. Click the **"Fail at last step"** example chip. It fills in `Research Python and fail_writer`.
2. Click **Run**.
3. Let it play: steps 1 and 2 go green, step 3 turns red.
4. Point at the failure panel:

```
RUN FAILED
Reason:            Mock writer tool failed: simulated model timeout.
Credits used:      3
Completed steps:   planning, search
```

5. **Show that the failed run is preserved.** Copy the run id, open a new browser tab, and go to:

```
http://127.0.0.1:8000/runs/run_xxxxxxxx
```

The raw JSON shows `"status": "failed"`, `"credits_used": 3`, both completed steps with their
results, and the failed step with its error. Nothing was erased.

6. **Show recovery.** Go back to the app, click the **"Research Python"** chip, click **Run**.
   A run with a **new run id** completes normally. Then refresh the JSON tab from step 5 — the old
   failed run is still exactly as it was.

**What to say:** see transcript §3.

---

## Optional 30-second bonus

If you want to show the bound doing its job, click the **"Exceed step limit"** chip
(`Research Python and never finish`). The planner asks for 8 steps, the budget is 5:

```
5 steps run, all completed
STATUS: FAILED    CREDITS: 5 / 5
Reason: The agent hit its hard limit of 5 steps before finishing (max_steps_exceeded).
```

Worth including — it's the clearest possible picture of "bounded execution."

---

# Transcript

Say it in your own words; this is the content, not a script to memorise. Roughly 4 minutes.

## §1 — Opening + scenario 1 (~70 seconds)

> "This is a working slice of an autonomous agent. You give it a goal in plain language, it plans
> a few steps, runs them using a tool, and gives you a result. The backend is FastAPI with SQLite,
> the frontend is plain HTML and JavaScript. The model and the tools are mocked on purpose —
> the assessment is about reliable execution, not about calling an LLM, and mocking them means
> every scenario I'm about to show is reproducible.
>
> I'll give it the goal 'Research Python' and run it.
>
> [click Run, pause]
>
> You can see the steps appearing one at a time — planning, then search, then summarize. That's
> not an animation. The agent commits every state change to SQLite as it happens, and the browser
> polls `GET /runs/{id}` twice a second, so what you're watching is the real database state.
>
> It's finished. Status is completed, there's the final output, and credits used is 3 out of 5.
> Three steps ran, so exactly three credits were charged. That relationship is going to matter in
> a minute."

## §2 — Scenario 2, the retry (~80 seconds)

> "Now the retry case. Imagine I clicked Run, the agent actually started, but my wifi dropped and
> the response never made it back to me. My browser retries automatically. The danger is that the
> server treats that as a second request, runs the agent twice, and charges me twice.
>
> Here's what actually happens. I'll click 'Resend the same request'.
>
> [click]
>
> Same run id, still 3 credits, and the message confirms there was no second execution.
>
> Let me show the actual HTTP, because that's where the mechanism lives. Same request, sent twice.
>
> [run both curl commands]
>
> The first one comes back 201 Created. The second one comes back 200 OK with the same run id.
> Different status code, same run — so the client can tell a fresh run from a replay.
>
> And in the database:
>
> [run the sqlite3 command]
>
> One row. Three credits. The agent did not run again.
>
> The mechanism is the `Idempotency-Key` header. The client generates that key *before* it sends
> the request, which is the important part — if the response is lost, the client still knows the
> key even though it never learned the run id. On the server, that key is the primary key of an
> idempotency table in SQLite.
>
> The part I'd point out in a code review is what I deliberately *didn't* write. I didn't write
> 'if the key isn't in the database, create a run', because two retries arriving at the same
> moment could both pass that check and both create a run. Instead I insert the run, its steps and
> the idempotency record in one transaction, and if the insert fails with a duplicate key error, I
> catch it and return the run that already exists. The database's uniqueness constraint is doing
> the locking, and it's atomic, so exactly one request can win. I tested that with eight
> simultaneous requests — one 201, seven 200s, one run.
>
> And because that record is on disk rather than in memory, restarting the server doesn't lose the
> protection."

## §3 — Scenario 3, failure and recovery (~80 seconds)

> "Last case: something breaks partway through. My mock tool has a trigger phrase that makes it
> raise, so I can demonstrate this reliably instead of waiting for a real API to have a bad day.
>
> [click the chip, click Run]
>
> Planning succeeds, search succeeds, and the writing step fails.
>
> Here's the end state. The run is failed — permanently, that's a terminal state. The reason is
> attached to the exact step that caused it. The first two steps keep their completed status and
> their results. And credits used is 3.
>
> That number is the deliberate part. The credit is charged when a step *starts*, not when it
> succeeds, because by the time the tool fails the work has already been attempted. So the failed
> step still costs its credit, and the two earlier ones aren't refunded either. Two successes plus
> one failure equals three credits, and the run is failed. The rule is that credits always equal
> the number of steps that were started, which means you can audit any run by counting rows.
>
> Nothing was erased. If I fetch this run id directly from the API —
>
> [open the JSON in a new tab]
>
> — everything's still here: the completed steps with their results, the failed step with its
> error, and the three credits. A failed run is evidence, not garbage.
>
> To recover, the client starts a *new* run.
>
> [click the Research Python chip, Run]
>
> New run id, and it completes normally. The old failed run is untouched — I'm not mutating it
> into the new one, because if someone was charged three credits for a failed run, that record
> needs to still exist."

## §4 — The bound + closing (~50 seconds)

> "One more thing the system has to guarantee: the agent can never run forever.
>
> Every run has a hard limit of five steps, and I enforce it in three independent places so that
> one bug can't defeat it. First, I only ever create step rows for the steps I'm allowed to run,
> so the loop has nothing to iterate past. Second, the loop re-checks the limit before charging
> each step. Third, the database has a CHECK constraint that credits can never exceed max steps.
>
> The planner deliberately doesn't know about the limit — it just proposes work, and the loop
> enforces the budget. So I also record how many steps the plan *wanted*. If the plan needed more
> steps than the budget allowed, the run ends as failed with `max_steps_exceeded`, even if every
> step that ran succeeded. That way 'ran out of budget' never gets mistaken for 'finished'.
>
> [optional: click 'Exceed step limit' and let it fail at 5 of 5]
>
> So across all three cases the run always lands in a clear end state: completed with an output
> and exact credits, or failed with a reason, the steps that did succeed, and the credits that
> were actually spent. There's twenty automated tests covering all of it, including the retry
> case and a restart test that proves the state is really on disk."

---

## Things not to do on camera

- **Don't click Run twice quickly to demo the retry.** Each click generates a new idempotency key,
  so you'd correctly get two separate runs and it would look like the protection is broken. Use
  the **Resend** button or the curl commands — those are the actual retry.
- **Don't skip the two seconds of progress.** It's the most visual part of the whole thing.
- **Don't apologise for the mocked tools.** Say the reason once, confidently: deterministic and
  testable. It was the right call.
