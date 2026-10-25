"""Configurazione locale del controllo Jev.

Il file gestito contiene solo la modalità (``manual``, ``auto`` o ``off``); la
chiave vive in un file privato dedicato accanto alla configurazione. Le fonti
legacy restano leggibili, ma una chiave salvata dal setup Subtask ha la
precedenza anche quando nell'ambiente esiste una chiave diversa.

Nessuna funzione qui chiama il provider o stampa il valore della chiave.
"""

from __future__ import annotations

import getpass
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from . import config, redact

MODES = ("manual", "auto", "off")
DEFAULT_MODE = "manual"
DEFAULT_CONFIG = "~/.config/subtask/jev.json"
DEFAULT_LEGACY_KEY_FILE = "~/.config/typesafe/api-key"
SETUP_COMMAND = "subtask jev setup"

SOURCE_MANAGED = "managed"
SOURCE_ENV = "env"
SOURCE_ENV_FILE = "env_file"
SOURCE_KEY_FILE = "key_file"

MAX_CONFIG_BYTES = 64 * 1024
MAX_KEY_BYTES = 16 * 1024
MAX_ENV_BYTES = 256 * 1024

_CONFIG_KEYS = {"version", "mode"}

_SOURCE_LABELS = {
    SOURCE_MANAGED: "file privato Subtask",
    SOURCE_ENV: "variabile TYPESAFE_API_KEY",
    SOURCE_ENV_FILE: "file env Subtask",
    SOURCE_KEY_FILE: "file chiave legacy",
}


class JevConfigError(ValueError):
    """Configurazione o chiave non utilizzabile in modo sicuro."""


# Alias esplicito per i chiamanti che distinguono i due errori.
class JevKeyError(JevConfigError):
    pass


class JevKeyMissing(JevKeyError):
    pass


def config_path() -> Path:
    """Percorso della configurazione gestita, letto a ogni chiamata."""
    raw = os.environ.get("SUBTASK_JEV_CONFIG") or DEFAULT_CONFIG
    return Path(raw).expanduser()


def key_path() -> Path:
    """File privato della chiave Subtask, mai il file globale TypeSafe."""
    return config_path().parent / "jev-key"


def _uid() -> int:
    return os.getuid()


def _source_label(source: str | None) -> str:
    return _SOURCE_LABELS.get(source or "", "sconosciuta")


def _read_owned(
    path: Path,
    limit: int,
    what: str,
    *,
    private: bool,
    error: type[JevConfigError] = JevConfigError,
) -> bytes | None:
    """Read a regular owned file without following symlinks or blocking on FIFOs."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError:
        raise error(f"{what} non è un file sicuro") from None
    try:
        info = os.fstat(descriptor)
        mode = stat.S_IMODE(info.st_mode)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != _uid():
            raise error(f"{what} deve essere un file regolare del proprietario")
        if private and mode & 0o077:
            raise error(f"{what} deve essere privato (0600)")
        if not private and mode & 0o022:
            raise error(f"{what} non deve essere scrivibile da altri")
        if info.st_size > limit:
            raise error(f"{what} è troppo grande")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise error(f"{what} è troppo grande")
        return b"".join(chunks)
    except OSError:
        raise error(f"{what} non è leggibile") from None
    finally:
        os.close(descriptor)


def _ensure_parent(path: Path) -> Path:
    parent = path.parent
    try:
        info = parent.lstat()
    except FileNotFoundError:
        parent.mkdir(mode=0o700, parents=True)
        os.chmod(parent, 0o700)
        info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != _uid():
        raise JevConfigError("la cartella della configurazione Jev non è sicura")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise JevConfigError("la cartella della configurazione Jev deve essere 0700")
    return parent


def _reject_existing(path: Path, what: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_uid != _uid():
        raise JevConfigError(f"{what} deve essere un file regolare del proprietario")


def _atomic_write(path: Path, data: bytes, what: str) -> None:
    parent = _ensure_parent(path)
    _reject_existing(path, what)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=parent)
        temporary = Path(name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        os.replace(temporary, path)
        temporary = None
        try:
            directory = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            directory = -1
        if directory >= 0:
            try:
                try:
                    os.fsync(directory)
                except OSError:
                    pass
            finally:
                os.close(directory)
    except JevConfigError:
        raise
    except OSError:
        raise JevConfigError(f"impossibile salvare {what} in modo sicuro") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _read_config() -> dict:
    path = config_path()
    raw = _read_owned(path, MAX_CONFIG_BYTES, "configurazione Jev", private=True)
    if raw is None:
        return {"version": 1, "mode": DEFAULT_MODE, "valid": True}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise JevConfigError("configurazione Jev non valida") from None
    if not isinstance(data, dict) or set(data) - _CONFIG_KEYS:
        raise JevConfigError("configurazione Jev non valida")
    version = data.get("version", 1)
    mode = data.get("mode", DEFAULT_MODE)
    if type(version) is not int or version != 1 or mode not in MODES:
        raise JevConfigError("configurazione Jev non valida")
    return {"version": 1, "mode": mode, "valid": True}


def load() -> dict:
    """Return the managed configuration, falling back to manual when invalid."""
    try:
        return _read_config()
    except JevConfigError:
        return {"version": 1, "mode": DEFAULT_MODE, "valid": False}


def _clean_key(value: str, error: type[JevConfigError] = JevKeyError) -> str:
    if type(value) is not str:
        raise error("chiave Jev non valida")
    key = value.strip()
    if not key or len(key) > MAX_KEY_BYTES:
        raise error("chiave Jev non valida")
    if any(character.isspace() or not character.isprintable() for character in key):
        raise error("chiave Jev non valida")
    redact.remember([key])
    return key


def _env_file_secrets() -> dict[str, str]:
    """Legacy SUBTASK_ENV_FILE, read safely when its path is overridden at runtime."""
    override = os.environ.get("SUBTASK_ENV_FILE")
    if not override:
        try:
            return config.load_secrets()
        except (OSError, UnicodeError):
            return {}
    try:
        raw = _read_owned(
            Path(override).expanduser(), MAX_ENV_BYTES, "file env", private=False, error=JevConfigError
        )
    except JevConfigError:
        return {}
    if raw is None:
        return {}
    values: dict[str, str] = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.removeprefix("export ").partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def _read_managed_key() -> str | None:
    raw = _read_owned(key_path(), MAX_KEY_BYTES, "chiave Jev", private=True, error=JevKeyError)
    if raw is None:
        return None
    try:
        return _clean_key(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise JevKeyError("chiave Jev non valida") from None


def _read_legacy_key_file() -> str | None:
    path = Path(os.environ.get("SUBTASK_JEV_KEY_FILE") or DEFAULT_LEGACY_KEY_FILE).expanduser()
    raw = _read_owned(path, MAX_KEY_BYTES, "file chiave Jev", private=True, error=JevKeyError)
    if raw is None:
        return None
    try:
        return _clean_key(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise JevKeyError("chiave Jev non valida") from None


def _resolve_with_source() -> tuple[str, str]:
    managed = _read_managed_key()
    if managed:
        return managed, SOURCE_MANAGED

    environment = os.environ.get("TYPESAFE_API_KEY", "")
    if environment.strip():
        return _clean_key(environment), SOURCE_ENV

    from_file = _env_file_secrets().get("TYPESAFE_API_KEY", "")
    if from_file.strip():
        return _clean_key(from_file), SOURCE_ENV_FILE

    legacy = _read_legacy_key_file()
    if legacy:
        return legacy, SOURCE_KEY_FILE

    raise JevKeyMissing("Jev non è configurato: esegui `subtask jev setup` oppure imposta TYPESAFE_API_KEY")


def resolve_key() -> str:
    """Return the active key and register it with the redactor. Never log it."""
    key, _ = _resolve_with_source()
    return key


def key_status() -> dict:
    """Describe key availability without ever returning the key itself."""
    try:
        _, source = _resolve_with_source()
    except JevKeyMissing:
        return {"configured": False, "source": None}
    except (JevConfigError, OSError):
        return {"configured": False, "source": None, "error": "unavailable"}
    return {"configured": True, "source": source}


def _budget_limits() -> dict:
    return {
        "monthly_cap_usd": float(config.MONTHLY_CAP_USD),
        "default_max_usd": float(config.DEFAULT_MAX_USD),
        "max_job_usd": float(config.MAX_JOB_USD),
        "review_allowance_usd": float(min(config.DEFAULT_MAX_USD, config.MAX_JOB_USD)),
    }


def status() -> dict:
    """Safe status for CLI/MCP/reporting: no key value and no config path."""
    configuration = load()
    keys = key_status()
    result = {
        "mode": configuration["mode"],
        "keyconfigured": bool(keys["configured"]),
        "source": keys["source"],
        "setup_command": SETUP_COMMAND,
        "config_valid": bool(configuration["valid"]),
        "budget": _budget_limits(),
    }
    if not configuration["valid"]:
        result["warning"] = "configurazione Jev non valida: resta attiva la modalità manuale"
    if keys.get("error"):
        result["key_error"] = keys["error"]
    return result


def _config_bytes(mode: str) -> bytes:
    payload = {"version": 1, "mode": mode}
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def configure(*, mode: str | None = None, api_key: str | None = None) -> dict:
    """Save mode and/or a new private key, then return the safe status."""
    wanted_mode = mode
    if mode is not None and (type(mode) is not str or mode not in MODES):
        raise JevConfigError("modalità Jev non valida: usa manual, auto oppure off")
    key: str | None = None
    if api_key is not None:
        key = _clean_key(api_key)
    if mode is None:
        mode = load()["mode"]
    if wanted_mode is None and key is None:
        return status()

    target_key = key_path()
    previous_key: bytes | None = None
    key_existed = False
    if key is not None:
        previous_key = _read_owned(target_key, MAX_KEY_BYTES, "chiave Jev", private=False, error=JevKeyError)
        key_existed = previous_key is not None
        _atomic_write(target_key, key.encode("utf-8") + b"\n", "chiave Jev")
    try:
        _atomic_write(config_path(), _config_bytes(mode), "configurazione Jev")
    except BaseException:
        if key is not None:
            try:
                if key_existed and previous_key is not None:
                    _atomic_write(target_key, previous_key, "chiave Jev")
                elif not key_existed:
                    target_key.unlink(missing_ok=True)
            except (OSError, JevConfigError):
                pass
        raise
    return status()


def _read_stdin_key() -> str:
    if sys.stdin is None:
        raise JevKeyError("nessuna chiave ricevuta su stdin")
    raw = sys.stdin.read(MAX_KEY_BYTES + 2)
    if raw is None or len(raw) > MAX_KEY_BYTES + 1:
        raise JevKeyError("chiave Jev su stdin troppo grande")
    return _clean_key(raw)


def _is_tty() -> bool:
    try:
        return bool(sys.stdin is not None and sys.stdin.isatty())
    except (AttributeError, OSError):
        return False


def _say(message: str = "", *, as_json: bool = False, end: str = "\n") -> None:
    stream = sys.stderr if as_json else sys.stdout
    print(message, file=stream, end=end, flush=True)


def _ask(prompt: str, *, as_json: bool) -> str:
    if as_json:
        _say(prompt, as_json=True, end="")
        return input("")
    return input(prompt)


def _hidden_key(prompt: str) -> str:
    try:
        return getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        raise
    except OSError:
        raise JevConfigError("impossibile leggere la chiave in modo nascosto") from None


def _ask_mode(current: str, *, as_json: bool) -> str:
    aliases = {
        "": current,
        "m": "manual",
        "manuale": "manual",
        "manual": "manual",
        "a": "auto",
        "automatico": "auto",
        "auto": "auto",
        "o": "off",
        "off": "off",
        "spento": "off",
        "disattivato": "off",
    }
    for _ in range(3):
        answer = _ask(f"Modalità Jev (manual/auto/off) [attuale: {current}]: ", as_json=as_json)
        choice = aliases.get(answer.strip().lower())
        if choice in MODES:
            return choice
        _say("Scelta non valida: usa manual, auto oppure off.", as_json=as_json)
    raise JevConfigError("modalità Jev non valida")


def _wizard(mode: str | None = None, *, as_json: bool = False) -> dict:
    current = status()
    if not current["config_valid"]:
        _say("Configurazione Jev non valida: verrà sostituita.", as_json=as_json)
    keys = key_status()
    new_key: str | None = None
    if keys["configured"]:
        _say(
            f"Chiave Jev già configurata (origine: {_source_label(keys['source'])}).",
            as_json=as_json,
        )
        _say("La chiave non viene mostrata.", as_json=as_json)
        entered = _hidden_key("Nuova chiave Jev (invio per mantenere quella attuale): ")
        if entered.strip():
            new_key = _clean_key(entered)
    else:
        _say("Nessuna chiave Jev configurata.", as_json=as_json)
        entered = _hidden_key("Chiave Jev (input nascosto, invio per saltare): ")
        if entered.strip():
            new_key = _clean_key(entered)

    if new_key is not None:
        _say(
            "La chiave sarà salvata nel file privato di Subtask, non nel file TypeSafe globale.",
            as_json=as_json,
        )
    chosen = mode if mode is not None else _ask_mode(current["mode"], as_json=as_json)
    result = configure(mode=chosen, api_key=new_key)
    _say("Configurazione Jev salvata.", as_json=as_json)
    if result["mode"] == "auto" and not result["keyconfigured"]:
        _say(
            "Attenzione: modalità auto senza chiave; nessun controllo Jev partirà.",
            as_json=as_json,
        )
    return result


def setup(*, key_stdin: bool = False, mode: str | None = None, as_json: bool = False) -> dict:
    """Configure Jev from hidden TTY input or explicit stdin for automation."""
    if key_stdin:
        key = _read_stdin_key()
        if mode is None:
            configuration = load()
            mode = configuration["mode"] if configuration["valid"] else DEFAULT_MODE
        return configure(mode=mode, api_key=key)
    if not _is_tty():
        raise JevConfigError(
            "il setup interattivo richiede un terminale (TTY): usa "
            "`subtask jev setup --key-stdin` con la chiave su stdin, oppure imposta TYPESAFE_API_KEY; "
            "per cambiare solo la modalità usa `subtask jev auto|manual|off`"
        )
    return _wizard(mode, as_json=as_json)


def format_status(value: dict) -> str:
    mode_labels = {"manual": "manuale", "auto": "automatica", "off": "disattivata"}
    key = "configurata" if value["keyconfigured"] else "non configurata"
    if value.get("source"):
        key += f" (origine: {_source_label(value['source'])})"
    elif value.get("key_error"):
        key += " (file non sicuro o illeggibile)"
    lines = [f"Modalità Jev: {mode_labels.get(value['mode'], value['mode'])}", f"Chiave: {key}"]
    if not value.get("config_valid", True):
        lines.append("Configurazione: non valida; resta attiva la modalità manuale")
    budget = value["budget"]
    lines.append(
        "Budget: "
        f"tetto mensile {budget['monthly_cap_usd']:.2f} $ · "
        f"job predefinito {budget['default_max_usd']:.2f} $ · "
        f"massimo job {budget['max_job_usd']:.2f} $"
    )
    lines.append(f"Setup: {value['setup_command']}")
    lines.append("Comandi: subtask jev setup | subtask jev auto | subtask jev manual | subtask jev off")
    return "\n".join(lines)


def _namespace_action(namespace: object) -> str:
    for name in ("jev_cmd", "jev_action", "action", "subcommand"):
        value = getattr(namespace, name, None)
        if value and value != "jev":
            return str(value)
    return "status"


def run_cli(namespace: object) -> int:
    """Run the `subtask jev` namespace and return a process exit code."""
    action = _namespace_action(namespace)
    as_json = bool(getattr(namespace, "json", False))
    key_stdin = bool(getattr(namespace, "key_stdin", False))
    mode = getattr(namespace, "mode", None)
    try:
        if action == "status":
            if key_stdin or mode is not None:
                raise JevConfigError("--key-stdin e --mode sono validi solo con `subtask jev setup`")
            result = status()
        elif action == "setup":
            result = setup(key_stdin=key_stdin, mode=mode, as_json=as_json)
        elif action in MODES:
            if key_stdin or mode is not None:
                raise JevConfigError("--key-stdin e --mode sono validi solo con `subtask jev setup`")
            result = configure(mode=action)
        else:
            raise JevConfigError("azione Jev non valida: usa status, setup, auto, manual oppure off")
    except KeyboardInterrupt:
        print("errore: setup Jev interrotto", file=sys.stderr)
        return 130
    except EOFError:
        print("errore: input del setup Jev terminato", file=sys.stderr)
        return 2
    except (JevConfigError, OSError, ValueError) as exc:
        print(f"errore: {redact.redact(str(exc))}", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(format_status(result))
    return 0
