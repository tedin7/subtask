# Final review — a1af3a8

Tested source: `a1af3a80abac57b25241a4800471cd44d979b1ce` (`a1af3a8`) in
detached `/root/wt/jev-cascade-final-review-20261026` (tree clean). Prior full
review: `76ff25430569aa3b7c20d2825b84c6871686fc5d` (330 lines, kept in history).

`severe_open=0`

## Status after fixes

- **S1 closed by 6128abd.** Probabilities are mandatory and exactly ternary;
  scope-only reports are rejected. Missing, partial, and unknown-label
  distributions now route `normal` with `probability_invalid`; a scope without
  probabilities also routes normal.
- **F1 closed by a1af3a8.** `_merge_cases`/`prepare` accept only reserved slots;
  two cases with one reservation raise `PhaseError` and write no manifest.
  `run_one` reports an unreserved slot as `PhaseError` before sending.
- **F2 closed for the reported cases.** `finish_reason`/`stop_reason`/`type`
  with `tool_calls`/`function_calls`, `role: tool`, and depth >16 now yield
  `normal` (`tool_use_detected`/`metadata_too_deep`). Residual low: a top-level
  `choice`/`probabilities` dictionary is still skipped by `_LABEL_FIELDS`
  (`subtask/jev_filter.py:136-137`), so a marker there can route `jev`.
- **F3 closed.** Blocked/skipped/unknown or absent reports display
  `Filtro Jev: non applicabile...`; no false model conclusion.
- **F4 closed.** Top-level `reason`/`error`, per-answer veto/error fields, and
  explicit `assessment: null`/`choice: null` force `normal`.
- **F5 closed for scope plus earlier veto.** `blocking_reasons` contains both
  `wrapper_veto` and `scope_unproven` when both apply. Residual low: early
  `delivery_route` returns (`assessments_invalid`, non-reviewed status) still
  lack `blocking_reasons`; the primary `reason` remains scope-led.
- **F6 docs-only.** Live source `6412f7c` versus hardened `a1af3a8`, the
  Jev-only false-supported metric, and the timing scope are documentation/
  evidence clarifications, not part of the `a1af3a8` source; no model rerun.

## Verification

`PYTHONPATH=. /root/subtask/.venv/bin/python -m pytest -q` on
`tests/test_jev_filter.py tests/test_jev_filter_regression.py
tests/test_jev_cascade.py tests/test_auto_review.py
tests/test_jev_phase_eval.py tests/test_jev_review_regressions.py`
→ **193 passed in 3.96s**.

Offline revalidation of the 10 old live Jev results with current `route`:
**9 jev / 1 normal**, identical to the stored routes; 0 API calls. No full
suite, network, keys, budget changes, or extra Jev calls.

## Trovato strada facendo (file:line,severity)

- `subtask/jev_filter.py:136-137` — top-level label/probability subtree skip
  remains (low).
- `subtask/jev_filter.py:518-524` — `blocking_reasons` appears only on the final
  normal path; early normal returns lack it (low).
- `docs/jev-cascade-20261025.md` / `docs/live-evidence/jev-cascade-20261025.json`
  — F6 source/metric/timing clarifications are docs-only (informational).
