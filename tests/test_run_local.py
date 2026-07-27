from __future__ import annotations

import os
import sys
from argparse import Namespace
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

RUN_LOCAL_PATH = Path(__file__).resolve().parents[1] / "run_local.py"
SPEC = spec_from_file_location("run_local", RUN_LOCAL_PATH)
assert SPEC is not None and SPEC.loader is not None
run_local = module_from_spec(SPEC)
SPEC.loader.exec_module(run_local)


def test_local_launcher_binds_to_loopback_by_default(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["run_local.py"])
    assert run_local.parse_args().host == "127.0.0.1"


def test_frontend_freshness_uses_source_timestamps(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    source = frontend / "src" / "main.tsx"
    output = frontend / "dist" / "index.html"
    source.parent.mkdir(parents=True)
    output.parent.mkdir(parents=True)
    source.write_text("source", encoding="utf-8")
    output.write_text("build", encoding="utf-8")

    os.utime(source, (100, 100))
    os.utime(output, (200, 200))
    assert run_local._frontend_build_is_fresh(frontend)

    os.utime(source, (300, 300))
    assert not run_local._frontend_build_is_fresh(frontend)


def test_worker_only_never_runs_frontend_build(monkeypatch) -> None:
    args = Namespace(
        api_only=False,
        worker_only=True,
        host="127.0.0.1",
        port=8765,
        no_browser=True,
        storage="local",
        skip_redis_check=False,
        rebuild_frontend=True,
    )
    monkeypatch.setattr(run_local, "parse_args", lambda: args)
    monkeypatch.setattr(
        run_local,
        "apply_default_environment",
        lambda: os.environ.update(
            {
                "VIDEO_CONVERTER_STORAGE": "local",
                "REDIS_URL": run_local.DEFAULT_REDIS_URL,
                "DATA_ROOT": run_local.DEFAULT_DATA_ROOT,
                "MEDIA_MOUNTS": run_local.DEFAULT_MEDIA_MOUNTS,
            }
        ),
    )
    monkeypatch.setattr(
        run_local,
        "build_frontend",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("npm must not run")),
    )
    monkeypatch.setattr(run_local, "create_runtime_directories", lambda: None)
    monkeypatch.setattr(run_local, "check_python_packages", lambda: None)
    monkeypatch.setattr(run_local, "warn_for_missing_binaries", lambda _names: None)
    monkeypatch.setattr(run_local, "print_configuration", lambda _args: None)
    monkeypatch.setattr(run_local, "start_processes", lambda _args: [])
    monkeypatch.setattr(run_local, "maybe_open_browser", lambda _args: None)
    monkeypatch.setattr(run_local, "wait_for_processes", lambda _processes: 0)

    assert run_local.main() == 0
