# Generation certificate — failure-mode register

Every row is one way the run can fail and how it is guaranteed not to. Levels:
**G-SANDBOX** = executed in the generation sandbox (evidence: test or command) ·
**G-STATIC** = checked by inspection/static analysis only ·
**PREP** = checked online by the Mac prep report before launch ·
**PILOT** = cannot be proven before the instance; a pilot test with the same id runs in the pilot rung ·
**CANARY** = only observable at volume; enforced by the canary gate and re-projection.
Paper-specific rows are appended below the base rows with ids continuing each group.

| ID | Group | Failure mode | Guarantee | Evidence / test |
|---|---|---|---|---|
| D01 | dataset | pinned record resolves to the expected title and file | G-SANDBOX | metadata fetched at approach time; title/file/size written to config/data_pins.json |
| D02 | dataset | download fails, is partial, empty, wrong size or wrong sha256 | PILOT | pilot/test_D02_download.py over runs/datacheck.json; resumable download + hash cache in rh/datapins.py |
| D03 | dataset | wrong rows, columns, encoding or duplicate keys | PILOT | pilot/test_D03_shape.py; unit tests tests/test_datapins.py cover each failure class |
| D04 | dataset | each failure class actually stops the run | G-SANDBOX | tests/test_datapins.py::test_failures (sha, size, columns, rows, encoding, missing file), dup keys, zip member |
| D05 | dataset | git pin: wrong commit, missing required path, file-count mismatch, cache not reused on resume | G-SANDBOX | tests/test_v102_pins.py (local bare repo: right/wrong commit, missing path, count mismatch, resume); real clone checked by pilot/test_D02_download.py |
| D06 | dataset | binary pin: size, sha256, host checksum mismatch; content changed after first lock; unpack fails | G-SANDBOX | tests/test_v102_pins.py (size, sha, checksum_url, lock-then-mismatch, tar.gz unpack); real download checked by pilot/test_D02_download.py |
| A01 | api | key missing or invalid for a provider | PILOT | pilot/test_A01_auth.py; PREP A04 checks the same keys from the Mac |
| A02 | api | model id mistyped or not offered | PILOT | pilot/test_A02_model_ids.py; PREP A04 |
| A03 | api | real reply does not parse under the stage schema for some (arm, model) | PILOT | pilot/test_A03_schema.py over runs/pilot/<stage>.jsonl |
| A04 | api | empty or truncated output silently accepted | G-SANDBOX | tests/test_vendors.py::test_parse_openai_cached_split_and_empty, test_parse_anthropic_usage_and_truncation; rh/cells.py rejects truncated; an empty, non-truncated reply is asked again up to 2 times, each attempt paid (tests/test_v102_parallel.py::test_empty_reply_is_asked_again_and_paid) |
| A05 | api | parse failures pile up unnoticed | G-SANDBOX | rh/cells.py ParseMonitor (rolling floor) + gate parse rate; tests/test_ladder.py |
| A06 | api | batch submit/poll/results/cancel misbehaves at the vendor; a batch cancelled by kill.sh loses or double-pays requests on resume | CANARY | fake batch path in tests/test_ladder.py::test_full_ladder; cancelled-batch resume (finished items kept once, unprocessed ones resubmitted) in tests/test_v102_parallel.py::test_kill_cancels_batch_then_resume_resubmits_only_unprocessed; real batches first run in the canary |
| A07 | api | rate limits hit at planned concurrency | PILOT | pilot/test_A07_ratelimit.py (headers present, >= 30% headroom); token bucket per model in rh/vendors.py |
| A08 | api | prompt cache not used | PILOT | pilot/test_A08_cache.py for stages with cache: true; exempt below 1024 prefix tokens (prep L13); the first call per (model, prefix) runs alone so the cache is warm before the rest; Groq/xAI caching is best effort, so a miss there is a pilot warning, not a failure |
| A09 | api | transient 429/5xx not retried, or 4xx retried forever | G-SANDBOX | tests/test_vendors.py::test_retry_on_429_then_success, test_400_is_not_retried |
| A10 | api | vendor request/response shapes wrong (cache_control, token param, no sampling) | G-SANDBOX | tests/test_vendors.py request/parse tests with real-shape fixtures |
| C01 | config | invalid or placeholder configuration | G-SANDBOX | rh/config.py validate; tests/test_ledger_config.py; prep L05 |
| C02 | config | sampling parameters sent | G-SANDBOX | tests/test_ledger_config.py::test_sampling_params_rejected; builders never add them |
| C03 | config | codename inconsistent across folder/README/config | G-STATIC | prep L06 |
| C04 | config | README names a venue/publisher or AI use; attribution in files or commits | G-STATIC | prep L07, L08, L09; commit-msg hook strips trailers |
| C05 | config | projected spend above ceiling or pilot ceiling | G-SANDBOX | prep L12 projection; ledger hard check before every call (tests/test_ledger_config.py) |
| C06 | config | high budget tier used without approval | G-SANDBOX | tests/test_ledger_config.py::test_high_tier_needs_approval |
| C07 | config | prompt + output exceeds a model's context | G-STATIC | prep L13 per-call token limits |
| S01 | stages | zero or implausible counts pass silently | G-SANDBOX | rh/gates.py (zero cells, group coverage, min_rows); tests/test_ladder.py |
| S02 | stages | duplicate cells (double ingestion) | G-SANDBOX | tests/test_ladder.py::test_duplicate_cells_fail_the_gate |
| S03 | stages | crash loses work or repeats paid calls on resume | G-SANDBOX | tests/test_ladder.py::test_crash_then_resume_has_no_duplicate_calls |
| S04 | stages | concurrent ledger writes corrupt spend | G-SANDBOX | tests/test_ledger_config.py::test_ceiling_enforced_and_lock_integrity |
| S05 | stages | kill marker not seen | PILOT | pilot/test_S05_killpoll.py (real bucket read); tests/test_ladder.py::test_kill_flag_stops_then_resume |
| S06 | stages | S3 backup not working | PILOT | pilot/test_S06_backup.py |
| S07 | stages | notifications not delivered | PILOT | pilot/test_S07_mail.py (sends a TEST mail) |
| S08 | stages | dead, stalled or looping runner; wall-clock overrun; repair step leaves untested edits running or a request for a human ignored | G-SANDBOX | tests/test_monitor_gates_misc.py (restart, executor, dead, budget, done, stall, kill, wall, uncommitted edits reverted, needs-human ends the run) |
| S09 | stages | budget stop turned into a repairable failure (executor spends more) | G-SANDBOX | tests/test_ladder.py::test_budget_ceiling_stops_run (exit 10, terminal) |
| I01 | infra | Mac caller is admin or wrong profile | PREP | prep A01 (role research-<codename>-runner) |
| I02 | infra | IAM permissions insufficient | PREP | prep A02 simulate-principal-policy over config/iam-required.json |
| I03 | infra | secret keys missing or expiring before the run ends | PREP | prep A03 |
| I04 | infra | wrong Python / no venv | PREP | prep L01 |
| I05 | infra | executor subscription token missing/expired or command broken on the instance | PILOT | pilot/test_I05_executor.py (one-turn request through the real wrapper) |
| I06 | infra | instance not using its own role | PILOT | pilot/test_I06_instance_role.py |
| I07 | infra | GitHub push or S3/SNS access missing | PREP | prep A05, A06, A07 |
| I08 | infra | results lost at the end | G-SANDBOX | rh/backup.py final tarball sha256 round-trip; tests/test_ladder.py::test_full_ladder |
| I09 | infra | the repair step's SELFCHECK cannot pass on the instance (clone differs from the Mac, live environment leaks) | PILOT | pilot/test_I09_selfcheck.py runs tools/selfcheck.sh from the clone in the pilot rung |
| I10 | infra | repair loop (watchdog -> repair step -> self-check -> commit -> push -> restart) never exercised for real; a repair commit lost or overwritten on the Mac | G-SANDBOX | tests/test_monitor_gates_misc.py (same crash twice -> repair step at once); tests/test_v103_prep.py::test_repair_drill_fails_pilot_only_when_armed_and_asked; _tools tests/test_new_paper.py (repairs pulled, a zip that undoes them refused); live drill: aws-up.sh <codename> --inject-fault |

## Study rows (ooptimize)

| ID | Group | Failure mode | Guarantee | Evidence / test |
|---|---|---|---|---|
| E01 | study | pinned JDK 21 / Maven / Randoop / SpotBugs / EqualsVerifier do not run, or the Java helpers do not compile on the instance | PILOT | pilot/test_E01_toolchain.py over runs/pilot/oopt/toolchain.json; gate check_toolchain stops the pilot |
| E02 | study | a library does not build, its JMH jar is missing or lists < 20 benchmarks, or a module has no test classpath | PILOT | pilot/test_E02_build.py (pilot library); gate check_build (>= 3 of 5 libraries in canary/full) |
| E03 | study | selector picks nothing, or a picked method is not exactly its source text (offset drift, overloads, abstract) | PILOT | pilot/test_E03_selector.py (text[start:end] == src on the real tree); unit tests tests/test_paper_sizing_select.py, test_paper_oracles.py::test_parse_jmh_runs_and_frames |
| E04 | study | baseline tests are flaky, so a patched failure would be noise; original does not recompile under the recipe | PILOT | pilot/test_E04_baseline.py (third run of every kept project and Randoop test passes 100 %); gate check_baseline (flaky share <= 30 %, >= 40 % of methods with a kept project test) |
| E05 | study | an oracle fires on unchanged code (false positive) or does not see the patched class (overlay not first on the classpath) | PILOT | pilot/test_E05_controls.py (identity controls all R0; throw controls caught where suites ran); gate check_score repeats it at canary and full volume |
| E06 | study | allocation measurement does not produce bytes/op with the overlay first on the classpath | PILOT | pilot/test_E06_alloc.py; gate check_alloc (>= 50 % of eligible cells measured; share reported) |
| E07 | study | a prompt exceeds the per-call limit, replies are truncated, or the token calibration for sizing is implausible | PILOT | pilot/test_E07_prompt_budget.py; prep L13 on the plan (18,000 + max output <= every model's context) |
| E08 | study | EqualsVerifier helper cannot fail a broken equals/hashCode (R4 could never fire) | PILOT | pilot/test_E08_equalsverifier.py (correct class PASS, broken hashCode FAIL); the jar is not reachable from the generation sandbox |
| E09 | study | the scoring recipe misbehaves with the pinned tools on the instance | PILOT | pilot/test_E09_toy_pipeline.py re-runs tests/test_paper_java_pipeline.py with the pinned JDK, Randoop, SpotBugs and EqualsVerifier |
| E10 | study | taxonomy rows or the headline rule (compile + tests pass + >= 1 test ran + contract row) wrong | G-SANDBOX | tests/test_paper_oracles.py (every row, R7 outside the headline, tests-ran rule, PASS->FAIL-only R4) |
| E11 | study | statistics wrong (Wilson, exact McNemar, Cochran's Q df = 2, Holm, bootstrap) | G-SANDBOX | tests/test_paper_stats.py against closed forms and chi-square table values |
| E12 | study | sizing exceeds the ceiling, ignores compute/spend/canary/executor, breaks the 50-per-library cap, or runs below the floor | G-SANDBOX | tests/test_paper_sizing_select.py::test_sizing_caps_at_250, test_sizing_shrinks_to_fit_and_floor_stops (GateFailed before full spend); written once to runs/shared/oopt/sizing.json |
| E13 | study | reply schema accepts junk or rejects wrapped-but-valid JSON; prefix too short to cache or not stable | G-SANDBOX | tests/test_paper_prompts.py (fences, prose, bad enums, empty body; prefix >= 4,800 chars, no dates) |
| E14 | study | the scoring recipe (javac overlay, targeted tests, Randoop replay, SpotBugs delta, copy/sync diffs) misclassifies | G-SANDBOX | tests/test_paper_java_pipeline.py on a toy library with the sandbox JDK 21, Randoop 4.3.4 and SpotBugs 4.10.5: identity R0; copy removed = silent break (R6, tests ran); sync removed R5; throw R2; R1; wrong_method; nested class; tests/test_paper_stages_real.py drives the real select -> baseline -> score -> alloc stage code on the same toy library (JMH calls stubbed): filters, tags, controls, overlays, gates, resume |
| E15 | study | MethodLocator mis-parses records, nested/anonymous classes, text blocks or bad snippets | G-SANDBOX | tests/test_paper_java_pipeline.py::test_locator_on_tricky_java |
| E16 | study | a build tool or test sees API keys; a hung child stalls the run silently | G-SANDBOX | tests/test_paper_env_controls.py (keys and JAVA_TOOL_OPTIONS stripped; timeout -> 124; heartbeat touched while a child runs) |
| E17 | study | scoring workers interfere (shared overlay or temp dirs) | G-STATIC | paper/s_score.py: one work dir per cell (hash of the cell id), per-cell java.io.tmpdir, overlay copied out before cleanup |
| E18 | study | resume re-scores or double-counts cells; a resume on a new instance (empty /opt/data) reuses records whose builds, baseline suites or overlays are gone | G-SANDBOX | paper/s_score.py and s_alloc.py skip cell ids already in the output (tests/test_paper_stages_real.py re-runs score: no new rows); build_present / baseline_present re-check the files before reusing a record, and allocation rebuilds a missing overlay from the reply (same test wipes /opt/data artefacts and re-runs baseline and alloc); generate resume is the template's (S03) |
| E19 | study | whole study path broken (stages, gates, sizing, analysis, brief) | G-SANDBOX | prep L14 dry run (fake vendor + fake local stages through the real taxonomy and analysis); tests/test_paper_*.py load paper/ directly (tests/_ooptpaper.py) |
| E20 | study | fewer than 150 usable methods or < 3 libraries with >= 40 at volume | CANARY | gate check_select at the canary (all five libraries profiled) and the sizing floor |
| E21 | study | real token use far above plan, so the full run would breach the ceiling | CANARY | sizing from pilot measurements with 1.25x output safety and 15 % margin; template canary re-projection (API + compute) stops before the full run |
| E22 | study | identity/throw controls degrade at volume (load, timeouts) | CANARY | gate check_score on canary and full rows (identity compile >= 95 %, R0 >= 90 %, throw caught >= 50 % where reached) |
| E23 | study | a pinned commit, tool archive or unpack is wrong, or the code only works with the sandbox JDK | G-SANDBOX | rh.datapins.verify_pin run in the sandbox on 8 of 10 pins: the five git pins (exact commit, .java counts 918/115/1022/172/51 = expected_rows, required paths), Temurin 21.0.13+9 (size, sha256, host .sha256.txt, unpacked, java -version), Randoop 4.3.4, SpotBugs 4.10.5 (unpacked); the study's Java tests re-run green on the pinned Temurin + Randoop + SpotBugs. Maven 3.9.11 and EqualsVerifier 4.5.2 hosts are unreachable here: D02 in the pilot (host checksum + sha256 lock) |
| E24 | study | concurrent JMH runs (one lane per library) fail at once on JMH's machine-wide lock, so a library profiles nothing; a failed profile or an empty selection is reused on resume | G-SANDBOX | every JMH call passes -Djmh.ignoreLock=true (tests/test_paper_canary_fixes.py::test_every_jmh_call_ignores_the_machine_wide_lock); raw outputs without a stack profile and empty selections are recomputed (test_lock_failure_is_not_a_profile_and_is_redone); found in run ooptimize-20261107T033817Z canary |
| E25 | study | a library build needs a JDK the instance does not have (caffeine: build logic on JDK 25, compiles with JDK 26 toolchains) | CANARY | caffeine alone may auto-provision Gradle toolchains (foojay resolver in its settings), for the build and the classpath step (test_caffeine_build_and_classpath_may_provision_toolchains); study compiles and runs stay on the pinned JDK 21; gate check_build needs 3 of 5 |
| E26 | study | repair commits made on the instance lost when the harness is re-delivered | G-STATIC | f13bcc4a (JMH result dir; EqualsVerifier suppress list; D02 for git pins) and 3aed78a1 (retry failed allocation rows; thread-unique tmp name in rh/util.write_json) folded and listed in config/repairs_folded.txt; regressions in tests/test_paper_canary_fixes.py and tests/test_paper_stages_real.py |
| E27 | study | the test runner cannot drive JUnit Platform tests (reflection looked Launcher.execute up on the package-private launcher class: IllegalAccessException; netty and RoaringBitmap kept 0 tests) | G-SANDBOX | tests/test_paper_platform_runner.py against the real junit-platform-launcher 1.8.2 with a stub TestEngine: the old lookup fails exactly as on the instance, the fixed runner reports each test; found in the second canary of run ooptimize-20261107T033817Z |
| E28 | study | a module's test classpath lacks the JUnit Platform runtime its tests need (engines and launcher are added by surefire/Gradle at test time; eclipse-collections lists only jupiter-api 6.1.3, caffeine's TestNG tests need the TestNG engine), so the runner falls back to JUnit 4 and keeps nothing | G-SANDBOX + CANARY | paper/s_build.platform_needs completes the classpath from Maven Central (jupiter engine, platform engine, launcher, vintage for JUnit 4, TestNG engine 1.1.0 / 1.0.6); tests/test_paper_platform_runner.py::test_platform_needs on the four classpath shapes recorded on the instance; the baseline gate (>= 40 % of methods with a kept project test) re-checks it at the canary |
| E29 | study | a selection record skipped because the build failed in an earlier attempt is reused after the build is fixed (caffeine, second canary) | G-SANDBOX | paper/s_select reuses a record only when it holds methods; tests/test_paper_canary_fixes.py |

## Verdict

Template (rhkit 1.0.3): every G-SANDBOX row is covered by a passing test in tests/; G-STATIC rows are enforced by
the prep report; PREP rows run online on the Mac before launch; PILOT rows each have a pilot test that runs in the
pilot rung on the instance; A06 (real vendor batches) is first exercised by the canary.

Study (ooptimize, rebuilt on rhkit 1.0.3, 2026-11-06): E10-E16, E18, E19, E23, E24, E27-E29 executed in the generation
sandbox and pass (137 tests = 73 harness + 64 study, including the Java recipe and the real stage code on a toy
library with JDK 21, Randoop 4.3.4 and SpotBugs 4.10.5, and again with the pinned Temurin 21.0.13+9; prep --offline:
every local check PASS, dry run end to end). E17 by inspection. E01-E09 need the instance (pinned JDK/Maven, the five
library builds, Maven Central for EqualsVerifier, real replies) and run as pilot tests on JCTools; E20-E22 are only
observable at volume and are enforced by the canary gates, the sizing floor and the template's re-projection. Not
provable before the instance: whether each library's build command works at its pinned commit with JDK 21 (E02; the
repair step may edit paper/libraries.py, never the pins). Revision 2 (7 Nov, after the first AWS run): pilot passed on the instance after two repair commits (folded, E26); canary
failed on E24 and E25, both fixed. Revision 3 (7 Nov, after the second canary: 197 methods selected, 8 with a kept
project test): E27-E29 fixed and proven against the real JUnit Platform launcher. Verdict: GENERATION PASS — ready for
new-paper.sh --update and a resume.
