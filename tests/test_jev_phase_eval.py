"""Test offline dell'harness Jev per fasi: nessuna API, nessun ledger reale.

Le chiamate Jev e il motore normale sono iniettati con adapter finti.  I test
verificano soprattutto i confini che rendono la campagna ripetibile: intent
prima della chiamata, nessun retry dopo un esito ignoto, chiusura della sola
prenotazione usata, richiesta identica tra i motori e segreti fuori dagli
artefatti.
"""

from __future__ import annotations

import inspect
import json
import os
import stat
from pathlib import Path

import pytest

from bench import jev_phase_eval as bench

SECRET = "sk-test-abcdefghijklmnopqrstuvwxyz0123456789"
KEY = "sk-key-abcdefghijklmnopqrstuvwxyz0123456789"


def _reservations(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [
        {
            "slot": slot,
            "rid": f"jev-phase-20261025:slot-{slot:02d}",
            "usd": 2,
            "state": "reserved",
        }
        for slot in range(1, 13)
    ]
    path.write_text(json.dumps(data))
    return path


def _questions(names=("C1", "C2")) -> dict:
    return {
        name: {
            "type": "choice",
            "instructions": f"Judge {name} against the supplied material.",
            "criteria": {
                "supported": "Recorded evidence supports this criterion.",
                "contradicted": "Recorded evidence contradicts this criterion.",
                "unproven": "Evidence is absent or ambiguous.",
            },
        }
        for name in names
    }


def _case(case_id="c1", slot=1, truth=None, phase="p1", **extra) -> dict:
    case = {
        "id": case_id,
        "slot": slot,
        "phase": phase,
        "state": {
            "policy": "phase-eval",
            "case": case_id,
            "materials": {"request": {"level": "caller_supplied", "text": f"request {case_id}"}},
        },
        "questions": _questions(),
    }
    if truth is not None:
        case["truth"] = truth
    case.update(extra)
    return case


def _body(request: dict, choices=None, model="jev-1.14.0", **extra) -> dict:
    choices = choices or {}
    answers = {}
    for name, question in request["questions"].items():
        choice = choices.get(name, "unproven")
        probabilities = {label: 0.0 for label in question["criteria"]}
        probabilities[choice] = 1.0
        answers[name] = {
            "type": "choice",
            "choice": choice,
            "probabilities": probabilities,
        }
    return {
        "model": model,
        "answers": answers,
        "usage": {"input_tokens": 11, "output_tokens": 7},
        **extra,
    }


def _prepare(tmp_path: Path, cases: list[dict]) -> tuple[Path, Path]:
    reservations = _reservations(tmp_path / "reservations.json")
    root = tmp_path / "campaign"
    bench.prepare(root, {"cases": cases}, reservations=reservations)
    return root, reservations


def _close_spy():
    calls = []

    def close(rid, usd, op):
        calls.append((rid, usd, op))
        return usd

    return calls, close


def _run(root: Path, reservations: Path, slot=1, engine="jev", **kwargs):
    calls, close = _close_spy()
    request_fn = kwargs.pop("jev_request", lambda packet, key: _body(packet))
    normal_fn = kwargs.pop("normal_run", None)
    result = bench.run_one(
        root,
        slot,
        engine,
        execute=True,
        reservations=reservations,
        jev_key=lambda: KEY,
        jev_request=request_fn,
        normal_run=normal_fn,
        reservation_open=lambda rid: True,
        policy_check=lambda: True,
        close_reservation=close,
        **kwargs,
    )
    return result, calls


def _normal_outcome(request: dict, choices=None, *, tool_use=False):
    body = {"answers": _body(request, choices)["answers"]}
    return {
        "body_text": json.dumps(body),
        "tool_use": tool_use,
        "tools": ["read"] if tool_use else [],
        "usage": {"input": 20, "output": 5},
        "cost_reported_usd": 0.01,
        "session": "ses_test",
        "raw_lines": [json.dumps({"type": "text", "part": {"text": json.dumps(body)}})],
        "stderr": "",
        "exit": 0,
        "timed_out": False,
    }


def test_prepare_separates_private_truth_and_engine_neutral_requests(tmp_path):
    root, reservations = _prepare(
        tmp_path,
        [_case(truth={"C1": "supported", "C2": "unproven"}, provenance={"source_sha": "a" * 64})],
    )
    before = reservations.read_bytes()
    manifest = json.loads((root / "manifest.json").read_text())
    request = json.loads((root / "requests" / "slot-01.json").read_text())
    assert set(request) == {"state", "questions"}
    assert manifest["slots"]["1"]["request_sha256"] == bench._digest(request)
    saved_truth = json.loads((root / "private" / "truth.json").read_text())
    assert saved_truth["cases"]["c1"] == {"C1": "supported", "C2": "unproven"}
    provenance = json.loads((root / "private" / "provenance.json").read_text())
    assert provenance["cases"]["c1"] == {"source_sha": "a" * 64}
    saved_cases = (root / "private" / "cases.json").read_text()
    assert "truth" in saved_cases and "provenance" in saved_cases
    request_text = (root / "requests" / "slot-01.json").read_text()
    assert "truth" not in request_text and "provenance" not in request_text
    assert reservations.read_bytes() == before

    # Idempotente e senza nuove prenotazioni.
    bench.prepare(
        root,
        {"cases": [_case(truth={"C1": "supported", "C2": "unproven"},
                         provenance={"source_sha": "a" * 64})]},
        reservations=reservations,
    )
    again = json.loads((root / "manifest.json").read_text())
    assert again["request_set_sha256"] == manifest["request_set_sha256"]
    assert reservations.read_bytes() == before
    source = inspect.getsource(bench)
    assert "ledger.reserve(" not in source
    assert "core.start(" not in source


def test_prepare_incrementale_preserva_risultati_e_assegna_slot_liberi(tmp_path):
    first = _case("c1", 1, truth={"C1": "supported", "C2": "unproven"})
    root, reservations = _prepare(tmp_path, [first])
    result, calls = _run(root, reservations)
    assert result["status"] == "reviewed" and len(calls) == 1

    second = _case("c2", slot=None, truth={"C1": "unproven", "C2": "contradicted"})
    bench.prepare(root, {"cases": [first, second]}, reservations=reservations)
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["slots"]["1"]["engines"]["jev"]["status"] == "reviewed"
    assert manifest["slots"]["2"]["case"] == "c2"
    assert (root / "requests" / "slot-02.json").is_file()


def test_prepare_rifiuta_dati_privati_nel_packet_e_slot_duplicati(tmp_path):
    nested = _case()
    nested["request"] = {"state": nested.pop("state"), "questions": nested.pop("questions"),
                         "truth": {"C1": "supported"}}  # fmt: skip
    with pytest.raises(bench.PhaseError):
        _prepare(tmp_path, [nested])
    with pytest.raises(bench.PhaseError):
        _prepare(tmp_path / "other", [_case("a", 1), _case("b", 1)])
    with pytest.raises(bench.PhaseError):
        _prepare(tmp_path / "many", [_case(f"c{index}", None) for index in range(13)])


def test_run_one_senza_execute_non_scrive_intent_ne_contatta_provider(tmp_path):
    root, reservations = _prepare(tmp_path, [_case(truth={"C1": "supported", "C2": "unproven"})])
    called = []
    result = bench.run_one(
        root,
        1,
        "jev",
        execute=False,
        reservations=reservations,
        jev_key=lambda: pytest.fail("nessuna credenziale senza --execute"),
        jev_request=lambda packet, key: called.append(1),
        reservation_open=lambda rid: pytest.fail("nessun ledger senza --execute"),
        close_reservation=lambda *args: pytest.fail("nessuna chiusura senza --execute"),
    )
    assert result["execute"] is False
    assert result["status"] == "prepared"
    assert called == []
    assert not (root / "slots" / "slot-01.jev.intent.json").exists()


def test_jev_persiste_intent_prima_della_chiamata_e_chiude_solo_lo_slot_usato(tmp_path):
    root, reservations = _prepare(tmp_path, [_case(truth={"C1": "supported", "C2": "unproven"})])
    seen = {}

    def request(packet, key):
        intent = root / "slots" / "slot-01.jev.intent.json"
        seen["intent"] = json.loads(intent.read_text())
        manifest = json.loads((root / "manifest.json").read_text())
        seen["status"] = manifest["slots"]["1"]["engines"]["jev"]["status"]
        seen["packet"] = packet
        assert key == KEY
        return _body(packet, {"C1": "supported"}, note=KEY)

    result, calls = _run(root, reservations, jev_request=request)
    assert result["status"] == "reviewed"
    assert result["answers"]["C1"]["choice"] == "supported"
    assert result["answers"]["C1"]["probabilities"] == {
        "contradicted": 0.0,
        "supported": 1.0,
        "unproven": 0.0,
    }
    assert result["request_sha256"] == json.loads(
        (root / "manifest.json").read_text()
    )["slots"]["1"]["request_sha256"]
    assert seen["intent"]["state"] == "submit_intent"
    assert seen["intent"]["request_sha256"] == result["request_sha256"]
    assert seen["status"] == "submitting"
    assert seen["packet"]["model"] == "jev-latest"
    assert calls == [("jev-phase-20261025:slot-01", 2.0, "settle")]
    assert result["budget_charged_usd"] == 2.0
    assert result["invoice"] == "UNKNOWN"
    assert result["usage"] == {"input_tokens": 11, "output_tokens": 7}
    raw_path = root / result["raw"]
    assert raw_path.is_file()
    assert stat.S_IMODE(raw_path.stat().st_mode) == 0o600
    assert KEY not in raw_path.read_text()
    assert "[REDACTED]" in raw_path.read_text()
    # Gli slot non usati non vengono toccati: una sola chiusura.
    assert len(calls) == 1


def test_esito_ignoto_nessun_retry_e_chiusura_dopo_il_tentativo(tmp_path):
    root, reservations = _prepare(tmp_path, [_case(truth={"C1": "supported", "C2": "unproven"})])
    attempts = []

    def request(packet, key):
        attempts.append(1)
        raise TimeoutError(f"timeout with {SECRET}")

    result, closes = _run(root, reservations, jev_request=request)
    assert result["status"] == "unknown"
    assert result["error"] == {"type": "TimeoutError"}
    assert result["attempted"] is True
    assert closes == [("jev-phase-20261025:slot-01", 2.0, "settle")]
    assert len(attempts) == 1
    with pytest.raises(bench.PhaseError):
        _run(root, reservations, jev_request=request)
    assert len(attempts) == 1


def test_policy_non_auto_rifiuta_prima_di_intent_e_spesa(tmp_path):
    root, reservations = _prepare(tmp_path, [_case(truth={"C1": "supported", "C2": "unproven"})])
    called = []
    with pytest.raises(bench.PhaseError):
        bench.run_one(
            root,
            1,
            "jev",
            execute=True,
            reservations=reservations,
            reservation_open=lambda rid: True,
            policy_check=lambda: False,
            jev_key=lambda: KEY,
            jev_request=lambda packet, key: called.append(1),
            close_reservation=lambda *args: pytest.fail("nessuna chiusura senza tentativo"),
        )
    assert called == []
    assert not (root / "slots" / "slot-01.jev.intent.json").exists()


def test_intent_orfano_blocca_ogni_retry(tmp_path):
    root, reservations = _prepare(tmp_path, [_case(truth={"C1": "supported", "C2": "unproven"})])
    intent = root / "slots" / "slot-01.jev.intent.json"
    bench._write_json(intent, {"state": "submit_intent", "slot": 1, "engine": "jev"})
    called = []
    with pytest.raises(bench.PhaseError):
        _run(root, reservations, jev_request=lambda packet, key: called.append(1))
    assert called == []


def test_risposta_provider_non_valida_salva_raw_privato_e_errore_sicuro(tmp_path):
    root, reservations = _prepare(tmp_path, [_case(truth={"C1": "supported", "C2": "unproven"})])

    def request(packet, key):
        body = _body(packet)
        body["answers"]["C1"]["probabilities"] = {
            "supported": 0.9,
            "contradicted": 0.9,
            "unproven": 0.0,
        }
        body["note"] = SECRET
        return body

    result, closes = _run(root, reservations, jev_request=request)
    assert result["status"] == "invalid"
    assert result["error"]["code"] in {"choice_not_argmax", "probability_out_of_range"}
    assert set(result["error"]) <= {"type", "code", "http_status"}
    assert SECRET not in json.dumps(result)
    assert len(closes) == 1
    raw = root / result["raw"]
    assert raw.is_file() and stat.S_IMODE(raw.stat().st_mode) == 0o600
    assert SECRET not in raw.read_text()
    assert "[REDACTED]" in raw.read_text()


def test_errore_di_trasporto_non_espone_il_testo_dell_eccezione(tmp_path):
    root, reservations = _prepare(tmp_path, [_case(truth={"C1": "supported", "C2": "unproven"})])

    def request(packet, key):
        raise ValueError(f"provider echoed {KEY}")

    result, closes = _run(root, reservations, jev_request=request)
    assert result["status"] == "unknown"
    assert result["error"] == {"type": "ValueError"}
    assert KEY not in json.dumps(result)
    assert len(closes) == 1
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".txt", ".jsonl"}:
            assert KEY not in path.read_text(errors="replace"), path


def test_normale_stesso_request_sha_sessione_nuova_e_nessuna_chiusura(tmp_path):
    root, reservations = _prepare(tmp_path, [_case(truth={"C1": "supported", "C2": "unproven"})])
    jev_result, jev_closes = _run(root, reservations, jev_request=lambda packet, key: _body(packet))
    seen = {}

    def normal(prompt, *, model, session_dir, timeout):
        seen["prompt"] = prompt
        seen["model"] = model
        seen["session_dir"] = session_dir
        request = json.loads((root / "requests" / "slot-01.json").read_text())
        return _normal_outcome(request, {"C1": "supported"})

    result, closes = _run(root, reservations, engine="normal", normal_run=normal)
    assert jev_closes and closes == []
    assert result["status"] == "reviewed"
    assert result["request_sha256"] == jev_result["request_sha256"]
    assert result["model"] == bench.NORMAL_MODEL
    assert seen["model"] == bench.NORMAL_MODEL
    assert seen["session_dir"].is_dir()
    request = json.loads((root / "requests" / "slot-01.json").read_text())
    assert json.dumps(request, ensure_ascii=False, sort_keys=True) in seen["prompt"]
    assert "truth" not in seen["prompt"] and "provenance" not in seen["prompt"]
    assert "budget_charged_usd" not in result
    config = json.loads((seen["session_dir"] / "opencode.json").read_text())
    assert {"action": "*", "resource": "*", "effect": "deny"} in config["permissions"]
    assert config["mcp"] == {"servers": {}}
    argv = bench._normal_argv(bench.NORMAL_MODEL, "PACKET")
    assert "--standalone" in argv and "--session" not in argv and "--continue" not in argv
    assert argv[-1] == "PACKET"
    assert argv[argv.index("--model") + 1] == bench.NORMAL_MODEL


def test_normale_invalido_se_usa_strumenti(tmp_path):
    root, reservations = _prepare(tmp_path, [_case(truth={"C1": "supported", "C2": "unproven"})])
    request = json.loads((root / "requests" / "slot-01.json").read_text())

    def normal(prompt, *, model, session_dir, timeout):
        return _normal_outcome(request, {"C1": "supported"}, tool_use=True)

    result, closes = _run(root, reservations, engine="normal", normal_run=normal)
    assert result["status"] == "invalid"
    assert result["error"]["code"] == "normal_tool_use"
    assert result["tool_use"] is True
    assert result["answers"] == {}
    assert closes == []


def test_normal_cli_finta_usa_parser_standalone_e_sessione_nuova(tmp_path, monkeypatch):
    root, reservations = _prepare(tmp_path, [_case(truth={"C1": "supported", "C2": "unproven"})])
    binary = tmp_path / "bin"
    binary.mkdir()
    script = binary / "opencode"
    body = {
        "answers": {
            "C1": {
                "type": "choice",
                "choice": "supported",
                "probabilities": {"supported": 1.0, "contradicted": 0.0, "unproven": 0.0},
            },
            "C2": {
                "type": "choice",
                "choice": "unproven",
                "probabilities": {"supported": 0.0, "contradicted": 0.0, "unproven": 1.0},
            },
        }
    }
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "assert args[:3] == ['run', '--format', 'json'], args\n"
        "assert '--standalone' in args and '--session' not in args and '--continue' not in args\n"
        "pathlib.Path('argv.json').write_text(json.dumps(args))\n"
        f"body = {json.dumps(body)!r}\n"
        "print(json.dumps({'type': 'text', 'sessionID': 'ses_fake', "
        "'part': {'text': json.dumps(json.loads(body))}}))\n"
        "print(json.dumps({'type': 'step_finish', 'sessionID': 'ses_fake', "
        "'part': {'cost': 0.01, 'tokens': {'input': 3, 'output': 4}}}))\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary}:{os.environ['PATH']}")
    result, closes = _run(root, reservations, engine="normal")
    assert closes == []
    assert result["status"] == "reviewed"
    assert result["answers"]["C1"]["choice"] == "supported"
    assert result["usage"] == {"input": 3, "output": 4}
    assert result["cost_reported_usd"] == 0.01
    assert result["session"] == "ses_fake"
    argv = json.loads((root / "private" / "normal" / "slot-01" / "argv.json").read_text())
    assert argv[-1].startswith("You are an independent inference model")
    assert json.dumps(
        json.loads((root / "requests" / "slot-01.json").read_text()),
        ensure_ascii=False,
        sort_keys=True,
    ) in argv[-1]


def test_report_conta_qualita_astensioni_ignote_e_fattura_unknown(tmp_path):
    root, reservations = _prepare(
        tmp_path,
        [
            _case(
                "c1",
                1,
                truth={"C1": "supported", "C2": "unproven"},
                phase="p1",
                guard={"C1": "unproven"},
            ),
            _case("c2", 2, truth={"C1": "contradicted", "C2": "supported"}, phase="p2"),
        ],
    )
    for slot, choices in ((1, {"C1": "supported", "C2": "unproven"}),
                          (2, {"C1": "contradicted", "C2": "supported"})):  # fmt: skip
        _run(root, reservations, slot=slot, jev_request=lambda packet, key, c=choices: _body(packet, c))
    for slot, choices in ((1, {"C1": "unproven", "C2": "supported"}),
                          (2, {"C1": "unproven", "C2": "unproven"})):  # fmt: skip
        request = json.loads((root / "requests" / f"slot-{slot:02d}.json").read_text())

        def normal(prompt, *, model, session_dir, timeout, request=request, choices=choices):
            return _normal_outcome(request, choices)

        _run(root, reservations, slot=slot, engine="normal", normal_run=normal)
    public = bench.report(root)
    assert "fattura: UNKNOWN" in public
    assert "corrette=4" in public
    assert "astensioni=3" in public
    assert "errori=1" in public
    assert "domande appaiate: 4" in public
    assert "Guard deterministica" in public and "accordo=" in public
    assert "Dettaglio privato" not in public
    private = bench.report(root, private=True)
    assert "Dettaglio privato" in private
    assert "truth=" in private
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "date": "2026-10-25",
                "review_states": {"reviewed": 17, "unknown": 3},
                "budget_charged_usd": 40.0,
                "invoice": "UNKNOWN",
                "cases": [{}],
            }
        )
    )
    with_baseline = bench.report(root, baseline=baseline)
    assert "Baseline storica" in with_baseline
    assert "reviewed" in with_baseline and "unknown" in with_baseline


def test_cli_prepare_e_run_one_senza_execute_restano_offline(tmp_path, capsys):
    root = tmp_path / "campaign"
    reservations = _reservations(tmp_path / "reservations.json")
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"cases": [_case(truth={"C1": "supported", "C2": "unproven"})]}))
    assert bench.main(["prepare", "--root", str(root), "--cases", str(cases),
                       "--reservations", str(reservations)]) == 0  # fmt: skip
    assert json.loads(capsys.readouterr().out)["api_calls"] == 0
    assert bench.main(["run-one", "--root", str(root), "--slot", "1", "--engine", "jev",
                       "--reservations", str(reservations)]) == 0  # fmt: skip
    plan = json.loads(capsys.readouterr().out)
    assert plan["execute"] is False
    assert not (root / "slots" / "slot-01.jev.intent.json").exists()
