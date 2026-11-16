"""Jev: il costo ignoto vale 0 sul registro vero (in una cartella tmp) e un costo noto non supera la prenotazione.

Spostato da arvos-todo (test_i_jev.py) il 16/11: prova codice di questo repo (ledger, delivery_review.charged_usd).
Gira in un processo a parte con HOME e SUBTASK_HOME in tmp, cosi il registro vero dell'operatore non si tocca.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[1]

PROVA = """
import json
from subtask import ledger
from subtask.delivery_review import charged_usd
out = {}
ledger.reserve('r-ignoto', 'st-uno', 2.0, cap=100.0, engine='jev')
usd, nota = charged_usd(USAGE_IGNOTO, 2.0)
ledger.close('r-ignoto', usd)
out['ignoto'] = [usd, ledger.committed(), ledger.open_for('st-uno')]
ledger.reserve('r-noto', 'st-due', 2.0, cap=100.0, engine='jev')
usd, nota = charged_usd({'input_tokens': 1000000}, 2.0)
ledger.close('r-noto', usd)
out['noto'] = [usd, ledger.committed()]
ledger.reserve('r-enorme', 'st-tre', 2.0, cap=100.0, engine='jev')
usd, nota = charged_usd({'input_tokens': 10**12}, 2.0)
ledger.close('r-enorme', usd)
out['enorme'] = [usd, ledger.committed()]
print(json.dumps(out))
"""


def corri_nel_registro(tmp_path, usage):
    home = tmp_path / "casa"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "SUBTASK_HOME": str(home / "subtask"),
        "PYTHONPATH": str(RADICE),
        "SUBTASK_JEV_INPUT_USD_M": "0.042",
    }
    r = subprocess.run(
        [sys.executable, "-c", PROVA.replace("USAGE_IGNOTO", usage)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        check=False,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize(
    "usage", ["None", "{'input_tokens': True}", "{'output_tokens': 5}", "{'input_tokens': -1}"]
)
def test_jev_con_costo_ignoto_scrive_zero_nel_registro_vero(tmp_path, usage):
    out = corri_nel_registro(tmp_path, usage)
    usd, committed, aperte = out["ignoto"]
    assert usd == 0.0 and committed == 0.0, "un costo ignoto non pesa sul tetto mensile"
    assert aperte == [], "la prenotazione e chiusa, non resta appesa"


def test_jev_con_costo_noto_paga_il_prezzo_di_listino_e_mai_oltre_la_prenotazione(tmp_path):
    out = corri_nel_registro(tmp_path, "None")
    assert 0 < out["noto"][0] < 2.0
    assert out["noto"][1] == out["noto"][0]
    assert out["enorme"][0] == 2.0, "mai sopra la prenotazione"
    assert out["enorme"][1] == pytest.approx(out["noto"][0] + 2.0)
