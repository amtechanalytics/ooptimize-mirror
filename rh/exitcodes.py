"""Exit codes shared by runner, monitor and the Mac scripts, with plain-English meanings."""

DONE = 0
CRASH = 1
BUDGET = 10
PARSE_FLOOR = 11
GATE_FAIL = 12
DATA_FAIL = 13
PILOT_FAIL = 20
CANARY_FAIL = 21
KILLED = 30
CONFIG_INVALID = 40

MEANING = {
    DONE: "finished: every rung passed",
    CRASH: "crashed: unhandled exception (see runs/exit.json traceback)",
    BUDGET: "stopped: a spend ceiling would have been exceeded",
    PARSE_FLOOR: "stopped: rolling parse rate fell below the floor",
    GATE_FAIL: "stopped: a stage gate failed (counts or schema)",
    DATA_FAIL: "stopped: a pinned dataset failed verification",
    PILOT_FAIL: "stopped: pilot report has failures",
    CANARY_FAIL: "stopped: canary gate failed",
    KILLED: "stopped: kill marker seen",
    CONFIG_INVALID: "stopped: configuration is invalid",
}

# Terminal codes: the monitor never restarts after these; a human (via chat) decides.
TERMINAL = {DONE, BUDGET, KILLED, CONFIG_INVALID}
# Codes the monitor may retry / hand to the executor.
REPAIRABLE = {CRASH, PARSE_FLOOR, GATE_FAIL, DATA_FAIL, PILOT_FAIL, CANARY_FAIL}


class HarnessStop(Exception):
    code = CRASH

    def __init__(self, msg: str = ""):
        super().__init__(msg or MEANING.get(self.code, ""))


class BudgetExceeded(HarnessStop):
    code = BUDGET


class ParseFloor(HarnessStop):
    code = PARSE_FLOOR


class GateFailed(HarnessStop):
    code = GATE_FAIL


class DataFailed(HarnessStop):
    code = DATA_FAIL


class PilotFailed(HarnessStop):
    code = PILOT_FAIL


class CanaryFailed(HarnessStop):
    code = CANARY_FAIL


class Killed(HarnessStop):
    code = KILLED


class ConfigInvalid(HarnessStop):
    code = CONFIG_INVALID


def meaning(code: int) -> str:
    return MEANING.get(code, f"unknown exit code {code}")
