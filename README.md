# ooptimize — do performance rewrites of hot Java library methods break object-oriented contracts that the project's own tests miss?

## Objectives
Measure, on real hot paths of five Java libraries (eclipse-collections, RoaringBitmap, netty 4.2, JCTools,
caffeine, each pinned to an exact commit), how often a language model's "make this method faster" rewrite
compiles and passes the library's own targeted tests while still breaking a contract that mechanical oracles
can observe, and whether giving the model more surrounding code changes that rate.

- **Subjects.** Methods that appear in at least 1 % of stack samples of the library's own JMH benchmarks,
  resolved to source with the JDK's parser, 3-120 lines, hand-written source, tagged by contract surface
  (equals/hashCode, Comparable, synchronisation, defensive copies, exceptions, iteration order, state,
  collection returns) and chosen round-robin over tags; at most 50 per library, at most 250 in total.
- **Arms.** Three context rungs: R1 the method alone; R2 the enclosing source file; R3 the file plus
  supertypes/subtypes and call sites. Three models (two hosted, one open-weights); two samples per
  (method, rung, model); vendor-default sampling and reasoning.
- **Oracles.** Each patch is compiled into an overlay in front of the library's own build, then: targeted
  project tests (kept only if they pass twice on the original), a Randoop regression suite generated on the
  original (same filter), EqualsVerifier under a fixed configuration, SpotBugs on the patched classes against
  the same baseline, and synchronisation / defensive-copy diffs of the method text. Rows: R1 no compile,
  R2 project tests fail, R3a value / R3b exception / R3c iteration-order divergence, R4 equals/hashCode
  contract, R5 synchronisation removed, R6 representation exposure, R7 other new static findings.
- **Headline.** Silent break = compiles, passes the targeted tests, at least one of those tests ran, and one of
  R3a, R3b, R3c, R4, R5, R6 fired. Primary analysis on sample 1, Wilson intervals; the context ladder paired
  per method (Cochran's Q, exact McNemar, Holm); sample-to-sample flip rate; allocation per operation
  (JMH `-prof gc`) for tests-pass patches by outcome.
- **Controls.** For every method, an identity patch (must score clean) and a throw patch (must be caught
  wherever suites ran) measure the oracles' false-positive rate and reach.

## Layout
- `paper/` — the study: stages (toolchain, build, select, baseline, generate, score, alloc), the Java helpers
  (`paper/java/`), prompts and schema, oracles and taxonomy, statistics, sizing, analysis, brief
- `rh/` — harness core (ladder, ledger, vendors, data pins, gates, monitor, backup, notifications)
- `config/` — project, models, prices, budget, data pins (git commits and tool archives), gates, plan, compute
- `pilot/` — tests run on the instance in the pilot rung, one per certificate row
- `tests/` — harness unit and end-to-end tests, run against the frozen demo study in `tests/demo/`
- `tests/test_paper_*.py` — the study's own tests (taxonomy, statistics, sizing, prompts, child-process hygiene, and
  the scoring recipe and real stage code on a toy library when a JDK, Randoop and SpotBugs are present); they load
  `paper/` through `tests/_ooptpaper.py`
- `certificate/generation_certificate.md` — failure-mode register and how each mode is guarded

## Run ladder
`datacheck -> pilot -> canary -> full -> analysis -> finish`; state in `runs/state.json`.
The pilot runs every stage on JCTools with three methods; the canary builds, profiles and baselines all five
libraries, fixes the study size from the pilot's measured tokens (`runs/shared/oopt/sizing.json`) and runs six
cells per (rung, model); the full run does the rest. Results land in `results/` (tables T1-T9, control table,
audit sheet).

## Local commands
```
python -m rh.prep --offline       # integrity checks, unit tests, dry run on the fake vendor
python -m rh.runner --dry-run     # the whole ladder on the fake vendor, fake storage and fake local stages
python -m rh.notify print-status  # what the 30-minute status mail would say
```

## Environment variables
| Variable | Meaning |
|---|---|
| `RH_ROOT` | repository root (default: current directory) |
| `RH_RUN_ID` | run identifier (set at launch) |
| `RH_FAKE_VENDOR`, `RH_FAKE_AWS` | dry-run fakes (also replace the Java stages with deterministic stand-ins) |
| `RH_DATA_DIR` | dataset and build cache directory (library trees, Maven/Gradle caches, Randoop suites, overlays) |
| `RH_TOPIC_ARN` | notification topic (defaults to `config/project.yaml`) |
| `RH_MAX_HOURS` | wall-clock limit on the instance |
| `OOPT_WORKERS` | scoring / baseline worker count (default 28; 3-4 in the pilot) |
| `OOPT_TEST_JDK`, `OOPT_TEST_RANDOOP`, `OOPT_TEST_SPOTBUGS`, `OOPT_TEST_EV` | tool locations for `tests/test_paper_java_pipeline.py` and `tests/test_paper_stages_real.py` (skipped when unset and no JDK on PATH) |
| `RH_EXECUTOR_CMD`, `RH_EXECUTOR_RUNBOOK`, `CLAUDE_CODE_OAUTH_TOKEN` | repair executor (instance only; subscription token) |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY` | provider keys (instance only, from the secret store; never passed to build tools or tests) |
