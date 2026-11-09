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

## Verdict

Template: every G-SANDBOX row is covered by a passing test in tests/; G-STATIC rows are enforced by the
prep report; PREP rows run online on the Mac before launch; PILOT rows each have a pilot test that runs
in the pilot rung on the instance; A06 (real vendor batches) is first exercised by the canary.
A paper adds its own rows (its datasets, schemas, arms, stage counts) with the same discipline.
