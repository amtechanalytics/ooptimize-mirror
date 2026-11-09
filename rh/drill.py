"""Repair drill. `bash _tools/aws-up.sh <codename> --inject-fault` sets RH_FAULT_INJECT=1 on the instance, and the
pilot rung then fails here on purpose (before any pilot spend), so the whole repair loop runs for real:
watchdog -> repair step -> self-check -> commit -> watchdog fast-forwards and pushes -> restart -> pilot passes.

The correct repair, and the only one: set DISARMED_FOR below to the run id quoted in the error message, run
`bash tools/selfcheck.sh`, then commit rh/drill.py with the message "fix: disarm repair drill". A later drill
(new run id) is armed again without any change to this file."""
import os

DISARMED_FOR = ""


def check() -> None:
    run_id = os.environ.get("RH_RUN_ID", "")
    if os.environ.get("RH_FAULT_INJECT") == "1" and DISARMED_FOR != run_id:
        raise RuntimeError(f"repair drill: planted fault in rh/drill.py for run {run_id} "
                           "(its docstring says how to repair it)")
