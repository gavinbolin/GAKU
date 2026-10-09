"""Kokoro container lifecycle tests. No Docker, no network.

Run:  python tests/test_kokoro_lifecycle.py
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import kokoro_lifecycle as kl  # noqa: E402
import research_podcast  # noqa: E402


DAEMON_STDERR = (
    "error during connect: open //./pipe/dockerDesktopLinuxEngine: "
    "The system cannot find the file specified. Is the docker daemon running?"
)
GPU_IMAGE = "ghcr.io/remsky/kokoro-fastapi-gpu:latest"


def _assert_kokoro_command(cmd: list[str]) -> None:
    if any(tok in {"rm", "rmi", "kill", "prune"} for tok in cmd):
        raise AssertionError(f"forbidden docker subcommand: {cmd}")
    if len(cmd) < 2 or cmd[1] not in {"inspect", "start", "stop", "run"}:
        raise AssertionError(f"unexpected docker command: {cmd}")
    action = cmd[1]
    if action in {"start", "stop"} and cmd[2:] != ["kokoro"]:
        raise AssertionError(f"docker {action} must target only kokoro: {cmd}")
    if action == "inspect" and cmd[-1] != "kokoro":
        raise AssertionError(f"docker inspect must target only kokoro: {cmd}")
    if action == "run":
        if "--name" not in cmd or cmd[cmd.index("--name") + 1] != "kokoro":
            raise AssertionError(f"docker run --name must be kokoro: {cmd}")
        blob = " ".join(cmd)
        if "kokoro-fastapi-cpu" in blob or "postgres" in blob or "nginx" in blob:
            raise AssertionError(f"docker run must not use another image/name: {cmd}")


class _Resp:
    def __init__(self, status: int = 200, body: bytes = b"") -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class FakeHTTP:
    def __init__(self) -> None:
        self.voices_ok = True
        self.voices_calls = 0
        self.speech_calls: list[dict] = []
        self.speech_body = b"RIFFfakewav"

    def urlopen(self, req, timeout=None):  # noqa: ANN001
        url = getattr(req, "full_url", None) or req.get_full_url()
        if url.rstrip("/").endswith("/v1/audio/voices"):
            self.voices_calls += 1
            if not self.voices_ok:
                raise urllib.error.URLError("kokoro down")
            return _Resp(200, b'{"voices":["af_bella"]}')
        if url.rstrip("/").endswith("/v1/audio/speech"):
            self.speech_calls.append(json.loads(req.data.decode("utf-8")))
            return _Resp(200, self.speech_body)
        raise AssertionError(f"unexpected URL {url}")


class FakeDocker:
    def __init__(self, http: FakeHTTP) -> None:
        self.http = http
        self.present = True
        self.daemon_down = False
        self.exists = True
        self.running = True
        self.image = GPU_IMAGE
        self.start_rc = 0
        self.stop_rc = 0
        self.run_rc = 0
        self.flip_voices_on_start = True
        self.commands: list[list[str]] = []
        self.timeouts: list[object] = []

    def which(self, cmd: str):
        if cmd != "docker" or not self.present:
            return None
        return "docker"

    def run(self, cmd, **kwargs):
        cmd = list(cmd)
        _assert_kokoro_command(cmd)
        timeout = kwargs.get("timeout")
        action = cmd[1]
        if action in {"start", "run"}:
            if timeout != kl.DOCKER_START_TIMEOUT_SEC:
                raise AssertionError(
                    f"docker {action} timeout is not the long start timeout: {timeout} {cmd}"
                )
        elif timeout != kl.DOCKER_CALL_TIMEOUT_SEC:
            raise AssertionError(f"docker call timeout is not short: {timeout} {cmd}")
        self.commands.append(cmd)
        self.timeouts.append(timeout)
        if self.daemon_down:
            return subprocess.CompletedProcess(cmd, 1, "", DAEMON_STDERR)
        if action == "inspect":
            if not self.exists:
                return subprocess.CompletedProcess(cmd, 1, "", "Error: No such object: kokoro")
            fmt = cmd[cmd.index("-f") + 1] if "-f" in cmd else ""
            running = "true" if self.running else "false"
            if "State.Running" in fmt and "Config.Image" in fmt:
                out = f"{running} {self.image}\n"
            elif "Config.Image" in fmt:
                out = self.image + "\n"
            else:
                out = running + "\n"
            return subprocess.CompletedProcess(cmd, 0, out, "")
        if action == "start":
            if self.start_rc == 0:
                self.running = True
                if self.flip_voices_on_start:
                    self.http.voices_ok = True
            err = "" if self.start_rc == 0 else "start failed"
            return subprocess.CompletedProcess(cmd, self.start_rc, "kokoro\n", err)
        if action == "stop":
            if self.stop_rc == 0:
                self.running = False
            err = "" if self.stop_rc == 0 else "stop failed"
            return subprocess.CompletedProcess(cmd, self.stop_rc, "kokoro\n", err)
        if action == "run":
            if self.run_rc == 0:
                self.exists = True
                self.running = True
                self.image = GPU_IMAGE
                if self.flip_voices_on_start:
                    self.http.voices_ok = True
            err = "" if self.run_rc == 0 else "gpu start failed"
            return subprocess.CompletedProcess(cmd, self.run_rc, "cid\n", err)
        raise AssertionError(cmd)


class KokoroLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._argv = sys.argv[:]
        self._env = {
            k: os.environ.get(k)
            for k in ("GAKU_KOKORO_AUTOSTOP", "TTS_BACKEND", "KOKORO_URL")
        }
        for key in self._env:
            os.environ.pop(key, None)
        kl._reset_state()
        self.http = FakeHTTP()
        self.docker = FakeDocker(self.http)
        self._patches = [
            patch.object(kl.shutil, "which", self.docker.which),
            patch.object(kl.subprocess, "run", self.docker.run),
            patch.object(kl.urllib.request, "urlopen", self.http.urlopen),
        ]
        for item in self._patches:
            item.start()
        self._orig_mcp = research_podcast.MCPClient
        self._orig_copy = research_podcast._copy_to_drive
        self._mcp_created: list = []

    def tearDown(self) -> None:
        for item in self._patches:
            item.stop()
        for cmd in self.docker.commands:
            _assert_kokoro_command(cmd)
        sys.argv = self._argv
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        research_podcast.MCPClient = self._orig_mcp
        research_podcast._copy_to_drive = self._orig_copy
        kl._reset_state()

    def _stop_cmds(self) -> list[list[str]]:
        return [c for c in self.docker.commands if c[1] == "stop"]

    def test_stop_after_normal_kokoro_use(self) -> None:
        with redirect_stderr(io.StringIO()):
            with kl.kokoro_session("http://localhost:8880") as session:
                self.assertTrue(session.ensured)
                self.assertFalse(session.stopped)
        self.assertTrue(session.stopped)
        self.assertEqual(self._stop_cmds(), [["docker", "stop", "kokoro"]])
        self.assertFalse(any(c[1] in {"start", "run"} for c in self.docker.commands))

    def test_stop_when_body_raises(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(RuntimeError):
                with kl.kokoro_session("http://localhost:8880"):
                    raise RuntimeError("build failed")
        self.assertEqual(self._stop_cmds(), [["docker", "stop", "kokoro"]])

    def test_stop_on_keyboard_interrupt(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                with kl.kokoro_session("http://localhost:8880"):
                    raise KeyboardInterrupt()
        self.assertEqual(self._stop_cmds(), [["docker", "stop", "kokoro"]])

    def test_no_stop_when_env_disables_autostop(self) -> None:
        for value in ("0", "false", "no", "off", "FALSE", "No"):
            with self.subTest(value=value):
                self.docker.commands.clear()
                os.environ["GAKU_KOKORO_AUTOSTOP"] = value
                self.assertFalse(kl.autostop_enabled())
                with redirect_stderr(io.StringIO()):
                    with kl.kokoro_session("http://localhost:8880") as session:
                        pass
                self.assertFalse(session.stopped)
                self.assertEqual(self._stop_cmds(), [])
        os.environ["GAKU_KOKORO_AUTOSTOP"] = "1"
        self.assertTrue(kl.autostop_enabled())
        self.assertFalse(kl.autostop_enabled(cli_disable=True))
        os.environ.pop("GAKU_KOKORO_AUTOSTOP", None)
        self.assertTrue(kl.autostop_enabled())

    def test_no_stop_when_autostop_flag_false(self) -> None:
        with redirect_stderr(io.StringIO()):
            with kl.kokoro_session("http://localhost:8880", autostop=False) as session:
                pass
        self.assertFalse(session.stopped)
        self.assertEqual(self._stop_cmds(), [])

    def test_no_stop_for_unrelated_image_we_did_not_start(self) -> None:
        self.docker.image = "library/nginx:latest"
        err = io.StringIO()
        with redirect_stderr(err):
            with kl.kokoro_session("http://localhost:8880") as session:
                pass
        self.assertFalse(session.stopped)
        self.assertEqual(self._stop_cmds(), [])
        self.assertIn("not stopping", err.getvalue())
        self.assertTrue(any(c[1] == "inspect" and c[-1] == "kokoro" for c in self.docker.commands))

    def test_stop_when_we_started_a_non_kokoro_image(self) -> None:
        self.http.voices_ok = False
        self.docker.running = False
        self.docker.image = "redis:7"
        with redirect_stderr(io.StringIO()):
            with kl.kokoro_session("http://localhost:8880") as session:
                pass
        self.assertTrue(session.stopped)
        self.assertEqual(self._stop_cmds(), [["docker", "stop", "kokoro"]])
        self.assertTrue(any(c[1:] == ["start", "kokoro"] for c in self.docker.commands))
        self.assertFalse(any(c[1] == "run" for c in self.docker.commands))

    def test_docker_start_uses_longer_timeout(self) -> None:
        self.http.voices_ok = False
        self.docker.running = False
        with redirect_stderr(io.StringIO()):
            self.assertTrue(kl.try_start_kokoro())
        paired = [
            (cmd, timeout)
            for cmd, timeout in zip(self.docker.commands, self.docker.timeouts)
            if cmd[1] == "start"
        ]
        self.assertEqual(
            paired,
            [(["docker", "start", "kokoro"], kl.DOCKER_START_TIMEOUT_SEC)],
        )
        self.assertGreater(kl.DOCKER_START_TIMEOUT_SEC, kl.DOCKER_CALL_TIMEOUT_SEC)

    def test_skip_when_docker_missing(self) -> None:
        self.docker.present = False
        err = io.StringIO()
        with redirect_stderr(err):
            stopped = kl.stop_kokoro_if_ours()
        self.assertFalse(stopped)
        self.assertEqual(self.docker.commands, [])
        self.assertIn("not on PATH", err.getvalue())

    def test_skip_when_daemon_down(self) -> None:
        self.docker.daemon_down = True
        err = io.StringIO()
        with redirect_stderr(err):
            stopped = kl.stop_kokoro_if_ours()
        self.assertFalse(stopped)
        self.assertEqual(self._stop_cmds(), [])
        self.assertIn("daemon", err.getvalue().lower())

    def test_skip_when_already_stopped(self) -> None:
        self.docker.running = False
        err = io.StringIO()
        with redirect_stderr(err):
            stopped = kl.stop_kokoro_if_ours()
        self.assertFalse(stopped)
        self.assertEqual(self._stop_cmds(), [])
        self.assertIn("already stopped", err.getvalue())

    def test_skip_when_container_missing(self) -> None:
        self.docker.exists = False
        err = io.StringIO()
        with redirect_stderr(err):
            stopped = kl.stop_kokoro_if_ours()
        self.assertFalse(stopped)
        self.assertEqual(self._stop_cmds(), [])
        self.assertIn("does not exist", err.getvalue())
        self.assertNotIn("daemon", err.getvalue().lower())

    def test_inspect_timeout_does_not_raise(self) -> None:
        def boom(cmd, **kwargs):
            self.docker.commands.append(list(cmd))
            _assert_kokoro_command(list(cmd))
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout") or 1)

        err = io.StringIO()
        with patch.object(kl.subprocess, "run", boom), redirect_stderr(err):
            stopped = kl.stop_kokoro_if_ours()
        self.assertFalse(stopped)
        self.assertIn("timed out", err.getvalue())
        self.assertEqual(self._stop_cmds(), [])

    def test_stop_after_failed_ensure_still_stops_container_we_started(self) -> None:
        self.http.voices_ok = False
        self.docker.running = False
        self.docker.flip_voices_on_start = False
        err = io.StringIO()
        with patch.object(kl, "wait_for_kokoro", lambda *_a, **_k: False), redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                with kl.kokoro_session("http://localhost:8880"):
                    self.fail("body should not run when ensure fails")
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(self._stop_cmds(), [["docker", "stop", "kokoro"]])

    def test_create_uses_gpu_image_and_never_falls_back_to_cpu(self) -> None:
        self.http.voices_ok = False
        self.docker.exists = False
        self.docker.run_rc = 1
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                with kl.kokoro_session("http://localhost:8880"):
                    self.fail("body should not run")
        runs = [c for c in self.docker.commands if c[1] == "run"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(
            runs[0][1:],
            ["run", "-d", "--name", "kokoro", "--gpus", "all", "-p", "8880:8880", GPU_IMAGE],
        )
        self.assertEqual(self._stop_cmds(), [])

    def test_create_then_stop_targets_only_kokoro(self) -> None:
        self.http.voices_ok = False
        self.docker.exists = False
        with redirect_stderr(io.StringIO()):
            with kl.kokoro_session("http://localhost:8880") as session:
                pass
        self.assertTrue(session.stopped)
        actions = [c[1] for c in self.docker.commands]
        self.assertIn("run", actions)
        self.assertIn("stop", actions)
        for cmd in self.docker.commands:
            _assert_kokoro_command(cmd)
            self.assertNotIn("redis", cmd)
            self.assertNotIn("nginx", cmd)
            self.assertNotIn("postgres", cmd)

    def test_smoke_writes_wav_and_reports_stop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "smoke.wav"
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                rc = kl.run_smoke("Hello from Gaku.", "http://localhost:8880", out_path=dest)
            self.assertEqual(rc, 0)
            self.assertEqual(dest.read_bytes(), b"RIFFfakewav")
            self.assertEqual(len(self.http.speech_calls), 1)
            speech = self.http.speech_calls[0]
            self.assertEqual(speech["model"], "kokoro")
            self.assertEqual(speech["voice"], "af_bella")
            self.assertEqual(speech["response_format"], "wav")
            self.assertEqual(speech["input"], "Hello from Gaku.")
            text = buf.getvalue()
            self.assertIn(str(dest), text)
            self.assertIn("kokoro_stopped=True", text)
            self.assertEqual(self._stop_cmds(), [["docker", "stop", "kokoro"]])

    def test_smoke_main_uses_session_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "smoke.wav"
            buf = io.StringIO()
            with patch.object(kl, "SMOKE_WAV", dest), redirect_stdout(buf), redirect_stderr(io.StringIO()):
                rc = kl.main(["--smoke", "Hello from Gaku."])
            self.assertEqual(rc, 0)
            self.assertTrue(dest.is_file())
            self.assertIn("kokoro_stopped=True", buf.getvalue())
            self.assertEqual(self.http.speech_calls[0]["voice"], "af_bella")

    def test_module_source_never_removes_containers(self) -> None:
        src = (HERE / "kokoro_lifecycle.py").read_text(encoding="utf-8")
        self.assertNotIn("docker rm", src)
        self.assertNotIn('"rm"', src)
        self.assertNotIn("'rm'", src)

    def _install_mcp(self, mode: str) -> None:
        created = self._mcp_created

        class FakeMCP:
            def __init__(self, cmd):
                self.cmd = cmd
                self.closed = False
                created.append(self)

            def call(self, name, payload):
                if name == "generate_audio" and mode == "interrupt":
                    raise KeyboardInterrupt()
                if name == "generate_audio" and mode == "boom":
                    raise RuntimeError("synth failed")
                if name == "synthesize_research_to_script":
                    return {"gemini_failed": True, "warning": "mocked"}
                if name == "generate_audio":
                    if mode == "ok":
                        return {"clips": [{"path": "line.wav", "line_id": "l1", "duration_ms": 800}]}
                    return {"clips": []}
                if name == "concatenate_audio_track":
                    out = Path(payload["out_path"])
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(b"RIFF")
                    return {}
                return {"valid": True}

            def close(self):
                self.closed = True

        research_podcast.MCPClient = FakeMCP

    def _episode_args(self, td: Path, extra: list[str] | None = None) -> list[str]:
        ep = td / "episode.json"
        ep.write_text(
            json.dumps({"topic": "UnitTestTopic", "hosts": [], "segments": []}),
            encoding="utf-8",
        )
        args = [
            "--topic", "UnitTestTopic",
            "--episode-in", str(ep),
            "--out", str(td / "out"),
            "--no-upload",
            "--skip-factcheck",
        ]
        if extra:
            args.extend(extra)
        return args

    def test_pipeline_stops_after_kokoro_step(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._install_mcp("noclips")
            sys.argv = ["research_podcast.py", *self._episode_args(Path(td))]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = research_podcast.main()
        self.assertEqual(rc, 4)
        self.assertEqual(self._stop_cmds(), [["docker", "stop", "kokoro"]])
        self.assertGreaterEqual(self.http.voices_calls, 1)
        self.assertTrue(self._mcp_created[-1].closed)

    def test_pipeline_stops_after_normal_completion(self) -> None:
        def fake_run(cmd, **kwargs):
            # research_podcast.subprocess is the same module kokoro_lifecycle
            # uses, so docker calls must still reach the fake.
            if cmd and cmd[0] == "ffmpeg":
                Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[-1]).write_bytes(b"ID3fake")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return self.docker.run(cmd, **kwargs)

        research_podcast._copy_to_drive = lambda *_a, **_k: None
        with tempfile.TemporaryDirectory() as td:
            self._install_mcp("ok")
            sys.argv = ["research_podcast.py", *self._episode_args(Path(td))]
            with patch.object(research_podcast.subprocess, "run", fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    rc = research_podcast.main()
        self.assertEqual(rc, 0)
        self.assertEqual(self._stop_cmds(), [["docker", "stop", "kokoro"]])

    def test_pipeline_stops_when_generate_audio_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._install_mcp("boom")
            sys.argv = ["research_podcast.py", *self._episode_args(Path(td))]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    research_podcast.main()
        self.assertEqual(self._stop_cmds(), [["docker", "stop", "kokoro"]])
        self.assertTrue(self._mcp_created[-1].closed)

    def test_pipeline_stops_on_keyboard_interrupt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._install_mcp("interrupt")
            sys.argv = ["research_podcast.py", *self._episode_args(Path(td))]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    research_podcast.main()
        self.assertEqual(self._stop_cmds(), [["docker", "stop", "kokoro"]])
        self.assertTrue(self._mcp_created[-1].closed)

    def test_pipeline_cli_flag_disables_stop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._install_mcp("noclips")
            sys.argv = [
                "research_podcast.py",
                *self._episode_args(Path(td), ["--no-kokoro-autostop"]),
            ]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = research_podcast.main()
        self.assertEqual(rc, 4)
        self.assertEqual(self._stop_cmds(), [])
        self.assertGreaterEqual(self.http.voices_calls, 1)

    def test_pipeline_env_disables_stop(self) -> None:
        os.environ["GAKU_KOKORO_AUTOSTOP"] = "0"
        with tempfile.TemporaryDirectory() as td:
            self._install_mcp("noclips")
            sys.argv = ["research_podcast.py", *self._episode_args(Path(td))]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = research_podcast.main()
        self.assertEqual(rc, 4)
        self.assertEqual(self._stop_cmds(), [])
        self.assertGreaterEqual(self.http.voices_calls, 1)

    def test_pipeline_does_not_stop_when_tts_backend_is_not_kokoro(self) -> None:
        os.environ["TTS_BACKEND"] = "vibevoice"
        with tempfile.TemporaryDirectory() as td:
            self._install_mcp("noclips")
            sys.argv = ["research_podcast.py", *self._episode_args(Path(td))]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = research_podcast.main()
        self.assertEqual(rc, 4)
        self.assertEqual(self.http.voices_calls, 0)
        self.assertEqual(self.docker.commands, [])

    def test_pipeline_does_not_stop_when_build_never_reaches_tts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            notes = Path(td) / "notes.md"
            notes.write_text("Research notes for a unit test.\n", encoding="utf-8")
            self._install_mcp("noclips")
            sys.argv = [
                "research_podcast.py",
                "--topic", "UnitTestTopic",
                "--research-md", str(notes),
                "--out", str(Path(td) / "out"),
                "--no-upload",
                "--skip-factcheck",
            ]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = research_podcast.main()
        self.assertEqual(rc, 3)
        self.assertEqual(self.http.voices_calls, 0)
        self.assertEqual(self._stop_cmds(), [])

    def test_help_documents_cli_flag(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as ctx:
                sys.argv = ["research_podcast.py", "--help"]
                research_podcast.main()
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("--no-kokoro-autostop", buf.getvalue())
        self.assertIn("GAKU_KOKORO_AUTOSTOP", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
