"""Tiny helper for the demo script: print one run as a readable table.

Usage:  python show_run.py <base_url> <run_id>
"""

import json
import sys
import urllib.request

base, run_id = sys.argv[1], sys.argv[2]
with urllib.request.urlopen(f"{base}/runs/{run_id}") as response:
    run = json.load(response)

print("  status        " + run["status"])
print("  credits_used  {} / {}".format(run["credits_used"], run["max_steps"]))
print("  error         " + str(run["error"]))
for step in run["steps"]:
    note = step["error"] or step["detail"] or ""
    print("  step {}  {:<12} {:<10} {}".format(
        step["step_number"], step["name"], step["status"], note))
if run["output"]:
    print("  --- output ---")
    for line in run["output"].splitlines():
        print("  " + line)
