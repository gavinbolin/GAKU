"""Start the Kokoro TTS container for a Gaku build and stop it afterwards.

Mirrors ``tools/pdf_read_aloud/pdf_common.py`` ``try_start_kokoro``: reuse the
container named ``kokoro`` when it exists, otherwise create the GPU image.
There is no silent CPU-image fallback.

``GAKU_KOKORO_AUTOSTOP`` defaults on. ``0`` / ``false`` / ``no`` / ``off``
leave the container running. ``kokoro_session`` stops in ``finally`` only
after this process actually ensured Kokoro.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

KOKORO_CONTAINER = "kokoro"
GPU_IMAGE = "ghcr.io/remsky/kokoro-fastapi-gpu:latest"
IMAGE_MARKER = "kokoro-fastapi"
DOCKER_CALL_TIMEOUT_SEC = 15
DOCKER_START_TIMEOUT_SEC = 120
_INSPECT_FMT = "{{.State.Running}} {{.Config.Image}}"

REPO_ROOT = Path(__file__).resolve().parent
SMOKE_WAV = REPO_ROOT / "output" / "kokoro_smoke" / "smoke.wav"
SMOKE_VOICE = "af_bella"

# Set by ensure_kokoro for the current process. stop_kokoro_if_ours reads them.
_ensured = False
_started_by_us = False


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def default_base_url() -> str:
    return (os.environ.get("KOKORO_URL") or "http://localhost:8880").strip().rstrip("/")


def autostop_enabled(*, cli_disable: bool = False) -> bool:
    """True unless the CLI flag or GAKU_KOKORO_AUTOSTOP disables autostop."""
    if cli_disable:
        return False
    raw = os.environ.get("GAKU_KOKORO_AUTOSTOP")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _reset_state() -> None:
    global _ensured, _started_by_us
    _ensured = False
    _started_by_us = False


class KokoroUse:
    """Result object yielded by ``kokoro_session``."""

    def __init__(self) -> None:
        self.ensured = False
        self.stopped = False


def kokoro_ok(base_url: str, timeout: float = 3.0) -> bool:
    url = base_url.rstrip("/") + "/v1/audio/voices"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            return 200 <= int(status) < 300
    except Exception:
        return False


def _docker_bin() -> str | None:
    return shutil.which("docker")


def _run(cmd: list[str], timeout: float = DOCKER_CALL_TIMEOUT_SEC) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        eprint("[kokoro] docker not on PATH")
        return None
    except OSError as exc:
        eprint(f"[kokoro] docker call failed ({exc})")
        return None


def _classify_docker_error(stdout: str, stderr: str) -> str:
    blob = f"{stdout}\n{stderr}".lower()
    if any(marker in blob for marker in ("no such object", "no such container")):
        return "missing"
    daemon_markers = (
        "docker daemon",
        "cannot connect to the docker",
        "is the docker daemon running",
        "error during connect",
        "dockerdesktoplinuxengine",
        "npipe",
        "//./pipe/docker",
        "the system cannot find the file specified",
        "connection refused",
    )
    if any(marker in blob for marker in daemon_markers):
        return "daemon"
    if "not found" in blob:
        return "missing"
    return "daemon"


class _Inspect:
    def __init__(self, kind: str, running: bool = False, image: str = "") -> None:
        self.kind = kind  # ok | missing | daemon | timeout
        self.running = running
        self.image = image


def _inspect(docker: str) -> _Inspect:
    proc = _run([docker, "inspect", "-f", _INSPECT_FMT, KOKORO_CONTAINER])
    if proc is None:
        return _Inspect("timeout")
    if proc.returncode != 0:
        return _Inspect(_classify_docker_error(proc.stdout or "", proc.stderr or ""))
    running_s, sep, image = (proc.stdout or "").strip().partition(" ")
    if not sep or not image:
        return _Inspect("daemon")
    return _Inspect("ok", running=running_s.lower() == "true", image=image.strip())


def try_start_kokoro() -> bool:
    """Start or create the ``kokoro`` container. Never falls back to the CPU image.

    Returns True when the container is running (already, or because this call
    started it). Sets ``_started_by_us`` only when this call issued a successful
    ``docker start`` or ``docker run``.
    """
    global _started_by_us
    docker = _docker_bin()
    if not docker:
        eprint("[kokoro] Docker not found on PATH.")
        return False

    eprint("[kokoro] Checking for existing 'kokoro' container...")
    inspected = _inspect(docker)
    if inspected.kind == "timeout":
        eprint("[kokoro] docker inspect timed out.")
        return False
    if inspected.kind == "daemon":
        eprint("[kokoro] Docker daemon is not running.")
        return False
    if inspected.kind == "ok":
        eprint(
            f"[kokoro] Found container image={inspected.image!r} running={str(inspected.running).lower()!r}"
        )
        if inspected.running:
            return True
        eprint("[kokoro] Starting existing container: docker start kokoro")
        started = _run(
            [docker, "start", KOKORO_CONTAINER],
            timeout=DOCKER_START_TIMEOUT_SEC,
        )
        if started is None or started.returncode != 0:
            if started is None:
                eprint(
                    "[kokoro] docker start kokoro timed out after "
                    f"{DOCKER_START_TIMEOUT_SEC} s"
                )
            else:
                eprint((started.stderr or started.stdout or "").strip())
            return False
        _started_by_us = True
        return True

    eprint(f"[kokoro] No 'kokoro' container; creating {GPU_IMAGE} with --gpus all")
    created = _run(
        [
            docker,
            "run",
            "-d",
            "--name",
            KOKORO_CONTAINER,
            "--gpus",
            "all",
            "-p",
            "8880:8880",
            GPU_IMAGE,
        ],
        timeout=DOCKER_START_TIMEOUT_SEC,
    )
    if created is None or created.returncode != 0:
        if created is None:
            eprint(
                "[kokoro] docker run kokoro timed out after "
                f"{DOCKER_START_TIMEOUT_SEC} s"
            )
        else:
            eprint((created.stderr or created.stdout or "").strip())
        eprint("[kokoro] GPU start failed. Run start_kokoro_and_remotion.bat from PROJECT GAKU.")
        eprint("[kokoro] Refusing silent CPU fallback.")
        return False
    _started_by_us = True
    return True


def wait_for_kokoro(base_url: str, seconds: float = 90.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if kokoro_ok(base_url):
            return True
        time.sleep(2.0)
        eprint("[kokoro] waiting for HTTP...")
    return False


def ensure_kokoro(base_url: str) -> None:
    """If Kokoro answers /v1/audio/voices, do nothing. Otherwise start it.

    Records that this process ensured Kokoro, and whether this process was the
    one that started the container. Raises SystemExit(2) when it cannot be
    brought up. Does not switch to the CPU image.
    """
    global _ensured, _started_by_us
    _ensured = True
    _started_by_us = False
    base_url = (base_url or default_base_url()).rstrip("/")
    if kokoro_ok(base_url):
        eprint(f"[kokoro] OK at {base_url}")
        return
    eprint(f"[kokoro] Not responding at {base_url}; attempting docker start...")
    if not try_start_kokoro():
        eprint(
            "Kokoro TTS is down. Start it with PROJECT GAKU\\start_kokoro_and_remotion.bat\n"
            "  or: docker start kokoro\n"
            "  or: docker run -d --name kokoro --gpus all -p 8880:8880 "
            f"{GPU_IMAGE}"
        )
        raise SystemExit(2)
    if not wait_for_kokoro(base_url, 90.0):
        eprint(
            "Kokoro container started but API never came up on port 8880.\n"
            "Check: docker logs kokoro\n"
            "Or re-run: PROJECT GAKU\\start_kokoro_and_remotion.bat"
        )
        raise SystemExit(2)
    eprint(f"[kokoro] OK at {base_url}")


def _ours(image: str) -> bool:
    return _started_by_us or (IMAGE_MARKER in (image or "").lower())


def stop_kokoro_if_ours() -> bool:
    """Stop only the container named ``kokoro``, and only if we started it or
    its image name contains ``kokoro-fastapi``.

    Returns True when ``docker stop kokoro`` succeeded. Docker missing, the
    daemon down, a missing container, or an already-stopped container prints
    one stderr line and returns False. Never raises. Never removes containers
    and never names any container other than ``kokoro``.
    """
    try:
        return _stop_kokoro_if_ours()
    except Exception as exc:
        # Docker failures are handled above. Don't mask test assertions or
        # programmer errors as a clean skip.
        if isinstance(exc, AssertionError):
            raise
        eprint(f"[kokoro] stop skipped ({exc})")
        return False


def _stop_kokoro_if_ours() -> bool:
    docker = _docker_bin()
    if not docker:
        eprint("[kokoro] docker not on PATH; not stopping")
        return False
    inspected = _inspect(docker)
    if inspected.kind == "timeout":
        eprint("[kokoro] docker inspect timed out; not stopping")
        return False
    if inspected.kind == "missing":
        eprint("[kokoro] container kokoro does not exist; not stopping")
        return False
    if inspected.kind != "ok":
        eprint("[kokoro] Docker daemon is not running; not stopping")
        return False
    if not _ours(inspected.image):
        eprint(
            "[kokoro] container kokoro is not a kokoro-fastapi image and this "
            "process did not start it; not stopping"
        )
        return False
    if not inspected.running:
        eprint("[kokoro] container kokoro is already stopped; not stopping")
        return False
    eprint("[kokoro] stopping container kokoro")
    stopped = _run([docker, "stop", KOKORO_CONTAINER])
    if stopped is None:
        eprint("[kokoro] docker stop kokoro timed out; not stopping")
        return False
    if stopped.returncode != 0:
        kind = _classify_docker_error(stopped.stdout or "", stopped.stderr or "")
        blob = f"{stopped.stdout or ''}\n{stopped.stderr or ''}".lower()
        if kind == "missing":
            eprint("[kokoro] container kokoro does not exist; not stopping")
        elif "is not running" in blob or "already stopped" in blob:
            eprint("[kokoro] container kokoro is already stopped; not stopping")
        elif kind == "daemon":
            eprint("[kokoro] Docker daemon is not running; not stopping")
        else:
            eprint("[kokoro] docker stop kokoro failed; not stopping")
        return False
    eprint("[kokoro] stopped container kokoro")
    return True


@contextmanager
def kokoro_session(base_url: str | None = None, *, autostop: bool | None = None) -> Iterator[KokoroUse]:
    """Ensure Kokoro on enter. Stop it in ``finally`` if this session ensured it.

    ``autostop=None`` follows ``GAKU_KOKORO_AUTOSTOP`` (default on). Pass
    False for ``--no-kokoro-autostop``.
    """
    global _ensured, _started_by_us
    outcome = KokoroUse()
    do_stop = autostop_enabled() if autostop is None else bool(autostop)
    _ensured = False
    _started_by_us = False
    try:
        ensure_kokoro(base_url or default_base_url())
        outcome.ensured = True
        yield outcome
    finally:
        if _ensured and do_stop:
            outcome.stopped = stop_kokoro_if_ours()
        elif _ensured:
            outcome.stopped = False
            eprint("[kokoro] autostop disabled; leaving container kokoro running")


def synthesize_wav(
    base_url: str,
    text: str,
    voice: str,
    out_path: Path,
    *,
    timeout: float = 120.0,
) -> Path:
    payload = {
        "model": "kokoro",
        "voice": voice,
        "input": text,
        "response_format": "wav",
        "speed": 1.0,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/audio/speech",
        data=data,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            audio = resp.read()
            if not 200 <= int(status) < 300:
                raise RuntimeError(f"Kokoro speech failed: HTTP {status}")
    except urllib.error.URLError as exc:
        eprint(f"[kokoro] speech request failed: {exc}")
        raise
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(audio)
    return out_path


def run_smoke(
    text: str,
    base_url: str | None = None,
    *,
    out_path: Path | None = None,
    autostop: bool | None = None,
) -> int:
    """Ensure Kokoro, synthesize one line, then stop via the session finally."""
    base = (base_url or default_base_url()).rstrip("/")
    dest = Path(out_path) if out_path is not None else SMOKE_WAV
    with kokoro_session(base, autostop=autostop) as session:
        synthesize_wav(base, text, SMOKE_VOICE, dest)
    print(dest)
    print(f"kokoro_stopped={session.stopped}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ensure the Kokoro TTS container and stop it after a short smoke synthesis."
    )
    parser.add_argument(
        "--smoke",
        metavar="TEXT",
        help='Synthesize TEXT to output/kokoro_smoke/smoke.wav (e.g. --smoke "Hello from Gaku.").',
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Kokoro base URL (default: KOKORO_URL or http://localhost:8880).",
    )
    parser.add_argument(
        "--no-kokoro-autostop",
        action="store_true",
        help="Do not stop the kokoro container after --smoke.",
    )
    args = parser.parse_args(argv)
    if not args.smoke:
        parser.error('--smoke TEXT is required, for example: --smoke "Hello from Gaku."')
    return run_smoke(
        args.smoke,
        args.base_url,
        autostop=autostop_enabled(cli_disable=args.no_kokoro_autostop),
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        eprint("[kokoro] interrupted")
        raise SystemExit(130)
