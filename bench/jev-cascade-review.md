# Independent adversarial review — commit 7df73fe

Review commit: `7df73feb94ead9b2b644fb0176819d8833990ae5`
Branch/worktree: `subtask/st-c9a86ab6d4` / `/root/wt/st-c9a86ab6d4`
Scope: this commit only. `subtask/jev_filter.py` predates the commit (last substantive
change in `783124d`), but it is the filter this commit applies through
`subtask/auto_review.details` and `bench/jev_cascade_eval.py`, so it was reviewed as
the integrated code.

No provider/API calls, no keys, no budget/policy changes, no SSH, no push, no full
suite. Only this file is written. All reproductions below are offline.

## severe_open=1

The one severe item is the advisory probability gate: a reviewed decision with a
missing, partial, or unknown-label probability distribution can route `"jev"` and
suppress the `"revisione normale necessaria"` advice. It is not an acceptance or a
security boundary, and the current automatic path does not launch a normal review;
see the reachability note under S1.

## Tests run

```text
PYTHONPATH=. /root/subtask/.venv/bin/python -m pytest -q \
  tests/test_jev_filter.py tests/test_jev_filter_regression.py \
  tests/test_jev_cascade.py tests/test_auto_review.py tests/test_jev_phase_eval.py
172 passed in 4.09s
```

Offline fuzzing (not pytest): 200,000 generated structures for `route` /
`delivery_route` and 50,000 for `auto_review.details`; no uncaught exception. The
fuzzer included `None`, booleans, non-finite floats, huge integers, lists, nested
dicts, unknown keys, and wrong field types.

## S1 — High: probability validation is conditional and its label set is self-referential

Locations: `subtask/jev_filter.py:353-365` (`_decision` skips validation when
`probabilities` is absent or `None`), `subtask/jev_filter.py:360-363`
(`_probabilities(probabilities, tuple(probabilities), ...)` makes the label-set check
a tautology), `subtask/jev_filter.py:496-503` (scope is “proven” without a
probability distribution).

Reproduction:

```python
from subtask.jev_filter import delivery_route, route

P = {"supported": 1.0, "contradicted": 0.0, "unproven": 0.0}
valid = {"provider_choice": "supported", "assessment": "supported", "probabilities": P}

missing = {"status": "reviewed", "answers": {"C1": {
    "provider_choice": "supported", "assessment": "supported"}}}
partial = {"status": "reviewed", "answers": {"C1": {
    **valid, "probabilities": {"supported": 1.0}}}}
unknown = {"status": "reviewed", "answers": {"C1": {
    **valid, "probabilities": {"supported": 1.0, "maybe": 0.0}}}}

for result in (missing, partial, unknown):
    report = route(result, ["C1"])
    print(report["route"], report["reason"])

scope = {"id": "scope", "assessment": "supported", "provider_choice": "supported"}
report = {"status": "reviewed",
          "assessments": [{"id": "C1", **valid}],
          "scope_assessment": scope}
print(delivery_route(report)["route"])
```

Observed: `jev all_required_decisive` for all three, and `jev` for the
scope-without-probabilities report. Expected under the documented contract:
`normal` (invalid output / unproven scope).

Why this matters:

- `route` is the function that decides whether the Jev answer may be selected instead
  of the normal path. It accepts a bare `{"provider_choice": "supported"}` and a
  one-label distribution for a three-choice question. `validate_answers` rejects the
  same data (`probabilities_mismatch` / `probability_out_of_range`).
- `delivery_route` calls the scope “proven” on `assessment`/`provider_choice` alone.
  The scope is the guard the documentation relies on to prevent a C1-only success
  from skipping normal. A scope report with no probabilities still passes `scope_proven`.
- `auto_review.details` (`subtask/auto_review.py:252-258`) then prints
  `Filtro Jev: giudizio disponibile ...` instead of
  `Filtro Jev: revisione normale necessaria ...`.
- In `bench/jev_cascade_eval.py:34` a malformed reviewed result would be selected as
  Jev rather than falling back. The live bench’s `phase.run_one` validates all
  questions before `route`, so the 10/10 live result is not affected; the auto path
  normally receives `delivery_review` reports that always carry probabilities. The
  defect is therefore a latent contract/robustness gap in the filter, not a
  demonstrated automatic normal-review skip in the current tree.

Why the tests miss it: `tests/test_jev_filter.py` has no missing/partial/unknown
probability case; `test_delivery_route_defaults_missing_scope_id_to_scope` (lines
675-682) omits scope probabilities and asserts `jev`, codifying the gap. A minimal
fix is to require a probability mapping in `_decision`, validate its labels against
`KNOWN_VERDICTS` (or a caller-supplied label set), and make `scope_proven` depend on
a validated distribution; then update that test to include probabilities.

## F1 — Medium: `prepare` can create slots that have no reservation, then `run_one` crashes

Locations: `bench/jev_phase_eval.py:296-298` (new “1..N consecutive” check),
`bench/jev_phase_eval.py:515-527` (`prepare` limits only to `MAX_SLOTS`),
`bench/jev_phase_eval.py:483-490` (free slots are still 1..12),
`bench/jev_phase_eval.py:919` (unguarded `reservations_data[slot]`).

Reproduction:

```python
import json, tempfile
from pathlib import Path
from bench import jev_phase_eval as b

root = Path(tempfile.mkdtemp())
res = root / "reservations.json"
res.write_text(json.dumps([{"slot": 1, "rid": "small-1", "usd": 2, "state": "reserved"}]))
case = lambda i: {"id": f"c{i}", "phase": "p", "state": {"x": 1},
                  "questions": {"C1": {"type": "choice",
                                       "criteria": {"a": "a"}}}}
out = b.prepare(root / "study", {"cases": [case(1), case(2)]}, reservations=res)
print(out["slots"])                       # [1, 2]
print(json.loads((root / "study" / "manifest.json").read_text())["reservations"].keys())  # ['1']
b.run_one(root / "study", 2, "normal", execute=False, reservations=res)  # KeyError(2)
```

Observed: `prepare` succeeds with slots `[1, 2]` and a manifest containing only
reservation `1`; dry-run `run_one` for slot 2 raises `KeyError(2)` at
`bench/jev_phase_eval.py:919`, before its `PhaseError` guards. `bench/jev_cascade_eval.run`
calls the same `run_one`, so a cascade on such a slot aborts instead of recording a
normal fallback. No provider is called for the unreserved slot, so this is a broken
campaign/partial-spend risk, not an unauthorized spend.

The new test (`tests/test_jev_phase_eval.py:26-32`) only exercises
`_load_reservations`; it never calls `prepare` with more cases than reservations or
`run_one` on the extra slot. A fix should cap free slots to `sorted(slots)`, reject
explicit slots absent from `slots`, and turn `reservations_data[slot]` into a clear
`PhaseError`.

## F2 — Medium/Low: tool-use detection is a blocklist with common holes

Locations: `subtask/jev_filter.py:58-74` (`_TOOL_TYPES` lacks plural
`tool_calls`/`function_calls`), `subtask/jev_filter.py:119-132` (only `stop_reason`
and `type` are checked; no `finish_reason`, no `role: "tool"`; depth > 16 returns
`False`; `choice`/`probabilities` subtrees are skipped wholesale).

Reproduction:

```python
from subtask.jev_filter import route
P = {"supported": 1.0, "contradicted": 0.0, "unproven": 0.0}
ans = {"C1": {"provider_choice": "supported", "assessment": "supported",
              "probabilities": P}}
for extra in ({"finish_reason": "tool_calls"},
              {"stop_reason": "tool_calls"},
              {"type": "tool_calls"},
              {"messages": [{"role": "tool", "content": "x"}]}):
    print(extra, route({"status": "reviewed", "answers": ans, **extra}, ["C1"])["route"])
```

Observed: all four route `jev`. A `tool_use` marker nested 17 dicts deep also routes
`jev` because `_has_tool_marker` returns `False` at depth > 16. The filter’s existing
tool tests cover `tool_use`, `server_tool_use`, `mcpToolUse`, `tool_calls` as a key,
and `stop_reason: tool_use`, but not these common shapes. The live Jev result does
not carry these fields, so this is primarily a filter-API gap; it becomes relevant if
a raw provider/chat body is ever routed.

## F3 — Low: `details` reports a filter result when no review happened

Location: `subtask/auto_review.py:252-258`.

Reproduction:

```python
from subtask import auto_review
print(auto_review.details({"attempt": 1, "jev_review": {
    "attempt": 1, "state": "skipped", "reason": "continuation"}}))
```

Observed:

```text
Jev: non eseguito
continuation
Filtro Jev: revisione normale necessaria (non avviata automaticamente)
```

The same happens for `blocked`/`unknown` with no `report`: `delivery_route({})`
returns `normal`, so the new line claims a filter result for a review that did not
run. It is conservative (it asks for normal), but misleading: “revisione normale
necessaria” is not a conclusion of the Jev filter when there is no Jev report. Gate
the route display on `entry["state"] == "reviewed"` and a present report, or label it
“filtro non applicabile”.

## F4 — Low: veto/error fields outside the top-level set are ignored

Locations: `subtask/jev_filter.py:76-87` (`_VETO_FIELDS` is top-level only),
`subtask/jev_filter.py:336-347` (`assessment: null` / `choice: null` are treated as
absent), `subtask/jev_filter.py:424-430` (only top-level vetoes).

Reproduction:

```python
from subtask.jev_filter import route
P = {"supported": 1.0, "contradicted": 0.0, "unproven": 0.0}
base = {"provider_choice": "supported", "assessment": "supported", "probabilities": P}
print(route({"status": "reviewed", "reason": "wrapper_veto",
             "answers": {"C1": base}}, ["C1"])["route"])                  # jev
print(route({"status": "reviewed", "answers": {"C1": {**base, "error": "tool failed"}}},
            ["C1"])["route"])                                             # jev
print(route({"status": "reviewed", "answers": {"C1": {**base, "choice": None}}},
            ["C1"])["route"])                                             # jev
```

`delivery_review` emits top-level `limitations`, which is covered, and its
assessments do not carry per-answer `error`/`veto` fields. A generic reviewed result
that encodes the veto in `reason` or per answer is accepted as clean; the documented
“local veto forces normal” rule is therefore narrower than it reads. Low because the
current producer does not emit those shapes.

## F5 — Low: `delivery_route` overwrites the route reason with `scope_unproven`

Location: `subtask/jev_filter.py:505-509`.

Reproduction:

```python
from subtask.jev_filter import delivery_route
P = {"supported": 1.0, "contradicted": 0.0, "unproven": 0.0}
report = {"status": "reviewed", "limitations": ["verification_not_run"],
          "assessments": [{"id": "C1", "assessment": "supported",
                           "provider_choice": "supported", "probabilities": P}],
          "scope_assessment": {"id": "scope", "assessment": "unproven",
                               "provider_choice": "supported", "probabilities": P}}
print(delivery_route(report)["reason"])   # scope_unproven, not wrapper_veto
```

Routing remains `normal`, so this is not a skip; it hides the actual veto from the
route output. The same overwrite can mask `tool_use_detected`/`probability_invalid`
when the scope is also unproven. Preserve the first blocking reason or expose a
`blocking_reasons` list.

## F6 — Low: live-study provenance and metric caveats

Verified claims from `docs/jev-cascade-20261025.md` and
`docs/live-evidence/jev-cascade-20261025.json`:

| Claim | Evidence | Result |
|---|---|---|
| 10 cascade correct | `overall.cascade_correct=10`; per-case selected = Jev except slot 6 normal | confirmed |
| 9 Jev correct | `overall.jev_correct=9`; slot 6 Jev `unproven`, gold `contradicted` | confirmed |
| 10 normal correct | `overall.normal_correct=10` | confirmed |
| 1 fallback | `normal_calls_in_cascade=1`; only slot 6 `fallback=true` | confirmed |
| C1 only, not full scope | prereg `required_ids=["C1"]`; `bench/jev_cascade_eval.py:23`; requests also ask `C1_evidence`, `scope`, `scope_evidence`; `limits` line 838; docs 70-72 and 84-87 | confirmed as a stated limitation |
| fixed Jev-first | `bench/jev_cascade_eval.py:33-44`; all slots Jev then normal; audit compares the request SHA | confirmed |
| normal baseline reused on fallback | code 36-38; slot 6; `comparison_limit` line 60; the same normal result supplies `normal_component_s` | confirmed |
| deterministic recovery statuses already known | docs 32-36; recovery cases c07-c10 contain 401 and the .99 distribution; `limits` line 844 | confirmed |
| times 12.864 / 134.356 / medians 0.300 / 10.153 | recomputed from `case_results` | confirmed |
| models `jev-1.14.0`, `opencode-go/deepseek-v4.1-flash#max` | per-case `provider_model`; prereg `normal_model` | confirmed |

Caveats, not contradictions:

- `reconciliation-audit.json:100-103` records `actual_experiment_source: 6412f7c
  filter, coordinator cascade` and `parser_source: 652f3a4 plus bounded-reservation
  support`, while this review’s commit is `7df73fe`. The public docs do not state the
  source commit of the live run. For the C1 `supported`/`contradicted`/`unproven`
  outcomes the route decision is unchanged, and the audit’s
  `outcomes_unchanged: true` re-validates the raw results; the 10/10 is still not a
  rerun of the exact final commit. Add the source commit to the docs.
- The public `false_supported` aggregate is cascade-level (`overall.false_supported`),
  not Jev alone. In this dataset Jev itself also has zero false-`supported` answers
  (recomputed from `case_results`), so the claim holds; a future fallback could mask
  a Jev false-`supported` in the aggregate. State the metric’s level.
- The docs’ “dieci inferenze normali di confronto” includes the fallback normal,
  which is also the baseline; the prose later clarifies this. No numeric mismatch.
- The preregistration sets Jev max 20 USD and max total 24 USD; the public evidence
  does not show actual spend, and the docs correctly say the invoice is UNKNOWN. The
  720 USD cap is an authorization claim, not a test result; no failure found.

## Positive checks (no bypass found)

- Duplicate IDs: `validate_answers` rejects duplicate question/answer IDs (list
  forms); `delivery_route` rejects duplicate assessment IDs and treats a duplicate
  scope ID as `scope_unproven`; `route` rejects duplicate required IDs. The only
  duplicate that JSON itself collapses is duplicate object keys, which is outside the
  filter.
- Malformed structures: `route`/`delivery_route` never raised an uncaught exception
  across the fuzz run (they returned `jev` or `normal` according to the logic above);
  `validate_answers` raises `InvalidAnswer` by design. `auto_review.details` also
  survived 50,000 generated status/report shapes.
- Top-level wrapper vetoes: `veto`, `vetoes`, `wrapper_veto`, `deterministic_veto`,
  `forced_normal`, `blocked`, `limitations`, `errors`, and `error` are checked and
  force `normal`; the top-level `tool_use`/`tools` checks also work.
- Live claims: the aggregate numbers, phase table, fallback, baseline reuse, C1-only
  scope, and timing totals are internally consistent with the public evidence.

## Trovato strada facendo (file:line,severity)

- `subtask/jev_filter.py:353-365` — probabilities optional; missing/None skips
  validation (S1, high).
- `subtask/jev_filter.py:360-363` — expected probability labels are the dictionary’s
  own keys, so partial/unknown-label distributions pass (S1, high).
- `subtask/jev_filter.py:496-503` — scope `proven` without any probability check (S1,
  high).
- `bench/jev_phase_eval.py:296-298`, `:515-527`, `:483-490`, `:919` — `prepare` and
  `_merge_cases` can assign slots beyond the reservation file; `run_one` then raises
  `KeyError` (F1, medium).
- `tests/test_jev_phase_eval.py:26-32` — new test only covers `_load_reservations`,
  not the `prepare`/`run_one` coupling (F1, medium).
- `subtask/jev_filter.py:58-74`, `:119-132` — `tool_calls`/`finish_reason`/`role:
  tool` and depth > 16 are not detected (F2, medium/low).
- `subtask/auto_review.py:252-258` — filter line for `blocked`/`skipped`/`unknown`
  with no report (F3, low).
- `subtask/jev_filter.py:76-87`, `:336-347`, `:424-430` — `reason`, per-answer
  `error`/veto, and `assessment: null` / `choice: null` are ignored (F4, low).
- `subtask/jev_filter.py:505-509` — `scope_unproven` overwrites another blocking
  reason (F5, low).
- `docs/jev-cascade-20261025.md:102-105` + private
  `reconciliation-audit.json:100-103` — live source commit is 6412f7c, not 7df73fe;
  docs do not state it; public evidence remains internally consistent (F6, low).
- `docs/live-evidence/jev-cascade-20261025.json:837-846` — limits correctly state
  C1-only, fixed order, baseline reuse, and known recovery statuses; no claim
  contradiction found (informational).

## Verdict

Do not treat this commit as a clean, severities-closed review: `severe_open=1` for the
probability/scope gate. The live study claims are numerically sound for the frozen
10-case, C1-only sample, and the live run is not a rerun of the final commit. The
reservation/prepare bug is a concrete regression in the benchmark, and the tool/veto
gaps are worth closing before calling the filter a general guard. No source was
changed by this review.
