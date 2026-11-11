# Single Python environment

GAKU now uses `podcast-pipeline/.venv` with **CPython 3.12.10 x64**.
3.12 was chosen over 3.14 for mature Windows wheels for faster-whisper,
CTranslate2, PyAV, and Gradio. The official Python.org 3.12.10 installer was
Authenticode-verified as Python Software Foundation and installed per-user,
without changing PATH or removing existing Python versions. This is the last
Python.org 3.12 Windows installer; later 3.12 security releases are source-only.
A later security-patched managed 3.12 runtime can be tested separately.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\run.ps1                         # Gradio UI (no generation on startup)
.\run.ps1 -Script build_video.py --help
```

`requirements.txt` pins the complete installed dependency set. It includes
both Gradio and faster-whisper in this one environment. The venv is ignored
by Git; never commit it. Use the venv executable rather than a bare `python`.
Python subprocesses already use `sys.executable` and now inherit this venv.

## Helpers outside this checkout

The parent `PROJECT GAKU` directory is **not a Git repository**. Therefore
its launch/review helpers cannot be directly included in this branch's commits.
Their exact idempotent repair logic is versioned here:

```powershell
.\.venv\Scripts\python.exe tools/migrate_project_paths.py --apply
.\.venv\Scripts\python.exe tools/configure_python_helpers.py --apply
```

The first repairs `.PROJECT` paths. The second points historical launchers and
review helper subprocesses at `.venv`, bootstraps `app.py` and `gaku.py` into
this interpreter before third-party imports, and makes parent requirements
refer to this checkout's lock. Original parent files are backed up once under
ignored `output/takeover_logs/parent_before` and `python_parent_before`.
No CHIE files, Desktop files, credentials, or other project roots are changed.
Historical topic launchers are not run by this setup and may request generation;
only execute them after verifying free-tier/budget and local-only flags.

## Decoder compatibility regression

The initial dependency install selected PyAV 19.0.2, whose `av.open()` removed
`metadata_errors`; faster-whisper 1.2.1 still passes it. Imports and `pip check`
passed but real transcription failed. The lock now uses **PyAV 18.1.0** (the
last pre-19 release), verified with an actual decoded WAV and fresh preview ASR.
Run `python tests/test_python_environment.py` after any dependency upgrade.
Upstream: https://github.com/SYSTRAN/faster-whisper/issues/1589
