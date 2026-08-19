#!/usr/bin/env bash
# Runs the three required scenarios against a live server and prints the results.
#
#   ./demo_scenarios.sh                      # uses http://127.0.0.1:8000
#   PY=.venv/bin/python ./demo_scenarios.sh  # pick a specific interpreter
set -u
BASE="${1:-http://127.0.0.1:8000}"
PY="${PY:-python3}"

show () { $PY show_run.py "$BASE" "$1"; }

post () {  # post <key> <goal>; writes the body to /tmp/_post.json, echoes the HTTP code
  curl -s -o /tmp/_post.json -w "%{http_code}" -X POST "$BASE/runs" \
    -H 'Content-Type: application/json' -H "Idempotency-Key: $1" \
    -d "{\"goal\": \"$2\"}"
}

rid () { $PY -c 'import json;print(json.load(open("/tmp/_post.json"))["run_id"])'; }

runs_for_key () {  # how many runs exist for one idempotency key
  $PY -c "
import sqlite3
db = sqlite3.connect('agent.db')
print(db.execute('''select count(*) from runs r
                    join idempotency_records i on i.run_id = r.run_id
                    where i.key = ?''', ('$1',)).fetchone()[0])"
}

echo "==============================================================="
echo " SCENARIO 1 - a goal that completes in a few steps"
echo "==============================================================="
CODE=$(post "sc1-key" "Research Python")
RID=$(rid)
echo "POST /runs   Idempotency-Key: sc1-key   ->  HTTP $CODE   run_id=$RID"
sleep 1; echo "--- mid-run (this is what the browser polls) ---"; show "$RID"
sleep 3; echo "--- final ---"; show "$RID"

echo
echo "==============================================================="
echo " SCENARIO 2 - the client retries because the response was lost"
echo "==============================================================="
CODE=$(post "sc2-key" "Summarize the Go language")
RID1=$(rid)
echo "attempt 1    ->  HTTP $CODE   run_id=$RID1   <- imagine this response never arrives"
CODE=$(post "sc2-key" "Summarize the Go language")
RID2=$(rid)
echo "attempt 2    ->  HTTP $CODE   run_id=$RID2   <- byte-identical request, same key"
sleep 4
echo "--- final state of the one and only run ---"; show "$RID1"
echo "  same run_id returned:        $([ "$RID1" = "$RID2" ] && echo YES || echo NO)"
echo "  runs created by sc2-key:     $(runs_for_key sc2-key)"

echo
echo "==============================================================="
echo " SCENARIO 3 - a tool fails partway through"
echo "==============================================================="
CODE=$(post "sc3-key" "Research Python and fail_writer")
RID=$(rid)
echo "POST /runs   ->  HTTP $CODE   run_id=$RID"
sleep 4; show "$RID"

echo
echo "--- recovery: the client starts a NEW run with a NEW key ---"
CODE=$(post "sc3-retry-key" "Research Python")
RID2=$(rid)
echo "POST /runs   ->  HTTP $CODE   run_id=$RID2"
sleep 4; show "$RID2"

echo
echo "--- and the failed run is untouched, still readable, still charged ---"
show "$RID"
