from __future__ import annotations

import errno
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import video_converter.worker.main as worker


@pytest.fixture
def repair_paths(tmp_path):
    source = tmp_path / "media" / "clip.mp4"
    source.parent.mkdir()
    source.write_bytes(b"source")
    root = tmp_path / "data"
    cfg = SimpleNamespace(
        data_root=root,
        outputs_dir=root / "outputs",
        temp_dir=root / "temp",
        min_free_disk_bytes=0,
        ffmpeg_stall_timeout_seconds=30,
    )
    return source, cfg


def fake_process(monkeypatch, *, failure=False, running=False, cancel_event=None, empty=False):
    processes = []

    class Process:
        returncode = None
        terminated = False
        killed = False

        def __init__(self, cmd, **kwargs):
            self.path = Path(cmd[-1])
            self.path.write_bytes(b"" if empty else b"repaired")
            kwargs["stderr"].write(b"invalid input" if failure else b"")
            assert "-nostdin" in cmd
            processes.append(self)
            if cancel_event is not None:
                cancel_event.set()

        def wait(self, timeout=None):
            assert timeout is None or 0 < timeout <= 5
            if running and not self.terminated and not self.killed:
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            self.returncode = 1 if failure or self.terminated else 0
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    monkeypatch.setattr(worker.subprocess, "Popen", Process)
    return processes


def assert_clean(source, cfg):
    assert source.read_bytes() == b"source"
    assert list(source.parent.iterdir()) == [source]
    assert not list(cfg.outputs_dir.iterdir())
    assert not list(cfg.temp_dir.iterdir())


def test_repair_publishes_unique_copies_without_worker_global_settings(repair_paths, monkeypatch):
    source, cfg = repair_paths
    fake_process(monkeypatch)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(data_root=Path("stale")))
    outputs = [worker.fix_mp4(source, runtime_settings=cfg) for _ in range(2)]
    assert outputs[0] != outputs[1]
    assert all(path.parent == cfg.outputs_dir.resolve() for path in outputs)
    assert all(path.read_bytes() == b"repaired" for path in outputs)
    assert source.read_bytes() == b"source"
    assert list(source.parent.iterdir()) == [source]
    assert not list(cfg.temp_dir.iterdir())


@pytest.mark.parametrize("destination", ["source", "outside", "traversal", "existing", "symlink"])
def test_repair_rejects_unsafe_destinations(repair_paths, monkeypatch, destination):
    source, cfg = repair_paths
    cfg.outputs_dir.mkdir(parents=True)
    outside = source.parent / "repaired.mp4"
    paths = {
        "source": source,
        "outside": outside,
        "traversal": cfg.outputs_dir / ".." / "escape.mp4",
        "existing": cfg.outputs_dir / "existing.mp4",
        "symlink": cfg.outputs_dir / "link.mp4",
    }
    paths["existing"].write_bytes(b"existing")
    if destination == "symlink":
        try:
            paths["symlink"].symlink_to(source)
        except OSError:
            pytest.skip("symlink privilege unavailable")
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **kw: pytest.fail("ffmpeg must not start"))
    with pytest.raises((ValueError, FileExistsError)):
        worker.fix_mp4(source, paths[destination], runtime_settings=cfg)
    assert source.read_bytes() == b"source"
    assert paths["existing"].read_bytes() == b"existing"
    assert not outside.exists()


@pytest.mark.parametrize("failure", ["ffmpeg", "empty", "timeout", "cancel", "publish"])
def test_repair_failure_cleans_all_partial_files(repair_paths, monkeypatch, failure):
    source, cfg = repair_paths
    cancel = threading.Event()
    processes = fake_process(
        monkeypatch,
        failure=failure == "ffmpeg",
        empty=failure == "empty",
        running=failure in {"timeout", "cancel"},
        cancel_event=cancel if failure == "cancel" else None,
    )
    if failure == "timeout":
        ticks = iter([0.0, 31.0])
        monkeypatch.setattr(worker.time, "monotonic", lambda: next(ticks))
    if failure == "publish":
        def fail_replace(src, dst):
            raise OSError(errno.EACCES, "publish denied")
        monkeypatch.setattr(worker.os, "replace", fail_replace)
    with pytest.raises((RuntimeError, OSError)):
        worker.fix_mp4(source, runtime_settings=cfg, cancel_event=cancel)
    if failure in {"timeout", "cancel"}:
        assert processes[0].terminated
        assert processes[0].returncode is not None
    assert_clean(source, cfg)


def test_repair_checks_disk_before_starting_ffmpeg(repair_paths, monkeypatch):
    source, cfg = repair_paths
    monkeypatch.setattr(worker.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **kw: pytest.fail("ffmpeg must not start"))
    with pytest.raises(OSError) as error:
        worker.fix_mp4(source, runtime_settings=cfg)
    assert error.value.errno == errno.ENOSPC
    assert_clean(source, cfg)


def test_repair_checks_runtime_directory_confinement(repair_paths, monkeypatch):
    source, cfg = repair_paths
    cfg.temp_dir = source.parent / "temp"
    with pytest.raises(ValueError, match="DATA_ROOT"):
        worker.fix_mp4(source, runtime_settings=cfg)
    assert list(source.parent.iterdir()) == [source]


def test_repair_publication_rejects_output_created_during_ffmpeg(repair_paths, monkeypatch):
    source, cfg = repair_paths
    target = cfg.outputs_dir / "fixed.mp4"
    fake_process(monkeypatch)
    process_class = worker.subprocess.Popen

    def concurrent_process(*args, **kwargs):
        process = process_class(*args, **kwargs)
        target.write_bytes(b"other-output")
        return process

    monkeypatch.setattr(worker.subprocess, "Popen", concurrent_process)
    with pytest.raises(FileExistsError):
        worker.fix_mp4(source, target, runtime_settings=cfg)
    assert target.read_bytes() == b"other-output"
    assert source.read_bytes() == b"source"
    assert not list(cfg.temp_dir.iterdir())


@pytest.mark.parametrize("copy_failure", [False, True])
def test_repair_cross_device_publication_remains_atomic(repair_paths, monkeypatch, copy_failure):
    source, cfg = repair_paths
    fake_process(monkeypatch)
    real_replace = worker.os.replace

    def cross_device_replace(src, dst):
        if Path(src).is_relative_to(cfg.temp_dir):
            raise OSError(errno.EXDEV, "cross-device")
        return real_replace(src, dst)

    def failed_copy(src, dst):
        Path(dst).write_bytes(b"partial")
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(worker.os, "replace", cross_device_replace)
    if copy_failure:
        monkeypatch.setattr(worker.shutil, "copyfile", failed_copy)
        with pytest.raises(OSError):
            worker.fix_mp4(source, runtime_settings=cfg)
        assert_clean(source, cfg)
    else:
        result = worker.fix_mp4(source, runtime_settings=cfg)
        assert result.read_bytes() == b"repaired"
        assert list(cfg.outputs_dir.iterdir()) == [result]
        assert not list(cfg.temp_dir.iterdir())
        assert source.read_bytes() == b"source"


def test_repair_cancel_kills_child_that_ignores_terminate(repair_paths, monkeypatch):
    source, cfg = repair_paths
    cancel = threading.Event()
    processes = []

    class Process:
        returncode = None
        killed = False

        def __init__(self, cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"partial")
            cancel.set()
            processes.append(self)

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

        def wait(self, timeout=None):
            if not self.killed:
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            self.returncode = -9
            return -9

        def kill(self):
            self.killed = True

    monkeypatch.setattr(worker.subprocess, "Popen", Process)
    with pytest.raises(RuntimeError, match="cancelled"):
        worker.fix_mp4(source, runtime_settings=cfg, cancel_event=cancel)
    assert processes[0].killed
    assert processes[0].returncode == -9
    assert_clean(source, cfg)
