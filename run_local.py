from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_REDIS_URL = "redis://localhost:6380/0"
DEFAULT_DATA_ROOT = "./data"
DEFAULT_MEDIA_MOUNTS = (
    f"Videos={ (Path.home() / 'Videos').as_posix() };Documents={ (Path.home() / 'Documents').as_posix() }"
    if os.name == "nt"
    else "Movies=./media/movies;Series=./media/series"
)
DEFAULT_STORAGE = "local"
REQUIRED_MODULES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn[standard]",
    "redis": "redis",
    "pydantic": "pydantic",
    "jwt": "PyJWT",
}


class LauncherError(RuntimeError):
    """Raised when local startup cannot continue safely."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Video Converter API and worker locally without Docker."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--api-only", action="store_true", help="Start only the FastAPI server.")
    mode.add_argument(
        "--worker-only", action="store_true", help="Start only the Redis/FFmpeg worker."
    )
    parser.add_argument("--host", default="0.0.0.0", help="API host bind address. Default: 0.0.0.0")
    parser.add_argument("--port", type=int, default=8765, help="API port. Default: 8765")
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not open the local UI in a browser."
    )
    parser.add_argument(
        "--storage",
        choices=("local", "redis"),
        default=None,
        help="Storage/queue backend. Default: local SQLite file store; redis requires a running Redis server.",
    )
    parser.add_argument(
        "--skip-redis-check",
        action="store_true",
        help="Skip the Redis ping check before launching child processes when --storage redis is used.",
    )
    return parser.parse_args()


def load_dotenv_file(path: Path) -> None:
    """Load simple KEY=VALUE lines from .env without adding a dependency."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def apply_default_environment() -> None:
    load_dotenv_file(PROJECT_ROOT / ".env")
    os.environ.setdefault("VIDEO_CONVERTER_STORAGE", DEFAULT_STORAGE)
    os.environ.setdefault("REDIS_URL", DEFAULT_REDIS_URL)
    os.environ.setdefault("DATA_ROOT", DEFAULT_DATA_ROOT)
    os.environ.setdefault("MEDIA_MOUNTS", DEFAULT_MEDIA_MOUNTS)


def create_runtime_directories() -> None:
    data_root = Path(os.environ["DATA_ROOT"])
    if not data_root.is_absolute():
        data_root = PROJECT_ROOT / data_root

    for path in [
        data_root,
        data_root / "input",
        data_root / "outputs",
        data_root / "temp",
        data_root / "logs",
        data_root / "data",
        PROJECT_ROOT / "media" / "movies",
        PROJECT_ROOT / "media" / "series",
    ]:
        path.mkdir(parents=True, exist_ok=True)


def check_python_packages() -> None:
    missing: list[str] = []
    for module_name, package_name in REQUIRED_MODULES.items():
        if importlib.util.find_spec(module_name) is None:
            missing.append(package_name)

    if missing:
        guidance = [
            "Missing required Python package(s): " + ", ".join(missing),
            "Install runtime dependencies before starting locally:",
            "  python -m venv venv",
            r"  venv\Scripts\activate",
            "  pip install -r requirements.txt",
        ]
        raise LauncherError("\n".join(guidance))


def _describe_port_occupier(port: int) -> str | None:
    """Try to identify the process that is listening on *port*.

    Returns a short human-readable description or ``None`` when the
    information cannot be determined.  On Windows the ``netstat -ano``
    output is parsed; on POSIX ``lsof`` is used as a best-effort probe.
    """
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            target_suffix = f":{port} "
            pid: str | None = None
            for line in result.stdout.splitlines():
                if "LISTENING" in line and target_suffix in line:
                    parts = line.split()
                    if parts:
                        pid = parts[-1]
                        break
            if pid and pid.isdigit() and int(pid) != 0:
                task = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "LIST"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                image = None
                for tline in task.stdout.splitlines():
                    if tline.lower().startswith("image name:"):
                        image = tline.split(":", 1)[1].strip()
                        break
                if image:
                    return f"PID {pid} ({image})"
                return f"PID {pid}"
        else:
            result = subprocess.run(
                ["lsof", "-i", f":{port}", "-sTCP:LISTEN", "-t"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            pids = result.stdout.strip().split()
            if pids:
                return f"PID {pids[0]}"
    except Exception:  # noqa: BLE001 - best-effort diagnostics
        pass
    return None


def check_port_available(host: str, port: int) -> None:
    """Raise :class:`LauncherError` if *port* is already in use on *host*."""
    # Always probe 127.0.0.1 regardless of bind address; 0.0.0.0 is not
    # a valid *connection* target on all platforms but localhost is.
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        if sock.connect_ex((probe_host, port)) == 0:
            occupier = _describe_port_occupier(port)
            lines = [
                f"Port {port} is already in use on {host}.",
            ]
            if occupier:
                lines.append(f"Occupying process: {occupier}")
            lines.extend(
                [
                    "Another application is already listening on this port.",
                    "Options:",
                    "  - Stop the other process and retry",
                    "  - Use a different port:  python run_local.py --port 8001",
                ]
            )
            raise LauncherError("\n".join(lines))


def warn_for_missing_binaries(names: Iterable[str]) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if not missing:
        print("[ok] ffmpeg and ffprobe are available on PATH.")
        return

    print("[warning] Missing executable(s) on PATH: " + ", ".join(missing))
    print("          Conversions and subtitle probing require FFmpeg tools.")
    print("          Install FFmpeg and add its bin directory to PATH, then restart this terminal.")


def build_frontend() -> None:
    frontend_dir = PROJECT_ROOT / "frontend"
    npm_executable = shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm")
    if npm_executable is None:
        raise LauncherError(
            "npm was not found on PATH. Install Node.js/npm before running the local launcher."
        )

    commands = [
        [npm_executable, "ci"],
        [npm_executable, "run", "build"],
    ]
    for command in commands:
        display_command = " ".join(["npm", *command[1:]])
        print(f"[build] Frontend: {display_command}")
        try:
            subprocess.run(command, cwd=frontend_dir, check=True)
        except subprocess.CalledProcessError as exc:
            raise LauncherError(
                f"Frontend command failed with exit code {exc.returncode}: {display_command}"
            ) from exc

    print("[ok] Fresh frontend build completed at frontend/dist.")


def warn_for_missing_frontend_build() -> None:
    index_file = PROJECT_ROOT / "frontend" / "dist" / "index.html"
    if index_file.exists():
        print("[ok] Built frontend is available at frontend/dist/index.html.")
        return

    print("[warning] Built frontend was not found at frontend/dist/index.html.")
    print(
        "          The API and worker can still start, but http://localhost:8765/ will return 404 until the UI is built."
    )
    print("          Build it locally with: cd frontend && npm install && npm run build")
    print(
        "          For frontend development, run Vite separately with: cd frontend && npm run dev"
    )


def check_redis(redis_url: str) -> None:
    try:
        import redis

        client = redis.Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
    except (
        Exception
    ) as exc:  # noqa: BLE001 - startup diagnostics should show any Redis connection problem.
        parsed = urlparse(redis_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 6379
        guidance = [
            f"Redis is not reachable at {redis_url} ({exc}).",
            "Docker is not being used by this launcher, so a local Redis-compatible server must already be running.",
            "Options on Windows include:",
            "  - an installed Redis-compatible service such as Memurai",
            "  - Redis running inside WSL and exposed on localhost",
            "  - another local Redis server listening on the configured REDIS_URL",
            f"Expected host/port from REDIS_URL: {host}:{port}",
        ]
        raise LauncherError("\n".join(guidance)) from exc

    print(f"[ok] Redis is reachable at {redis_url}.")


def child_environment() -> dict[str, str]:
    env = os.environ.copy()
    src_path = PROJECT_ROOT / "src"
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        env["PYTHONPATH"] = str(src_path) + os.pathsep + existing_pythonpath
    else:
        env["PYTHONPATH"] = str(src_path)
    return env


def start_processes(args: argparse.Namespace) -> list[subprocess.Popen[bytes]]:
    env = child_environment()
    processes: list[subprocess.Popen[bytes]] = []

    if not args.worker_only:
        api_cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "video_converter.api.main:app",
            "--host",
            args.host,
            "--port",
            str(args.port),
        ]
        print("[start] API: " + " ".join(api_cmd))
        processes.append(subprocess.Popen(api_cmd, cwd=PROJECT_ROOT, env=env))

    if not args.api_only:
        worker_cmd = [sys.executable, "-m", "video_converter.worker.main"]
        print("[start] Worker: " + " ".join(worker_cmd))
        processes.append(subprocess.Popen(worker_cmd, cwd=PROJECT_ROOT, env=env))

    return processes


def terminate_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    for proc in processes:
        if proc.poll() is None:
            proc.terminate()

    deadline = time.monotonic() + 8
    for proc in processes:
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)

    for proc in processes:
        if proc.poll() is None:
            proc.kill()


def wait_for_processes(processes: list[subprocess.Popen[bytes]]) -> int:
    try:
        while True:
            for proc in processes:
                return_code = proc.poll()
                if return_code is not None:
                    print(f"[exit] Child process {proc.pid} exited with code {return_code}.")
                    terminate_processes(processes)
                    return return_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[stop] Ctrl+C received. Stopping local services...")
        terminate_processes(processes)
        return 130


def print_configuration(args: argparse.Namespace) -> None:
    print("[config] VIDEO_CONVERTER_STORAGE=" + os.environ["VIDEO_CONVERTER_STORAGE"])
    if os.environ["VIDEO_CONVERTER_STORAGE"] == "redis":
        print("[config] REDIS_URL=" + os.environ["REDIS_URL"])
    print("[config] DATA_ROOT=" + os.environ["DATA_ROOT"])
    print("[config] MEDIA_MOUNTS=" + os.environ["MEDIA_MOUNTS"])
    if not args.worker_only:
        print(f"[config] UI URL=http://localhost:{args.port}/")


def maybe_open_browser(args: argparse.Namespace) -> None:
    if args.no_browser or args.worker_only:
        return
    url = f"http://localhost:{args.port}/"
    time.sleep(1.5)
    print(f"[open] {url}")
    webbrowser.open(url)


def main() -> int:
    args = parse_args()
    os.chdir(PROJECT_ROOT)
    apply_default_environment()
    if args.storage:
        os.environ["VIDEO_CONVERTER_STORAGE"] = args.storage
    os.environ["VIDEO_CONVERTER_STORAGE"] = os.environ["VIDEO_CONVERTER_STORAGE"].strip().lower()

    try:
        build_frontend()
        create_runtime_directories()
        check_python_packages()
        warn_for_missing_binaries(["ffmpeg", "ffprobe"])
        if not args.worker_only:
            warn_for_missing_frontend_build()
            check_port_available(args.host, args.port)
        if os.environ["VIDEO_CONVERTER_STORAGE"] == "redis" and not args.skip_redis_check:
            check_redis(os.environ["REDIS_URL"])
        elif os.environ["VIDEO_CONVERTER_STORAGE"] == "local":
            print("[ok] Using local SQLite-backed queue/store; Redis is not required.")
        else:
            raise LauncherError("VIDEO_CONVERTER_STORAGE must be either 'local' or 'redis'.")
    except LauncherError as exc:
        print("[error] " + str(exc), file=sys.stderr)
        return 2

    print_configuration(args)
    processes = start_processes(args)
    maybe_open_browser(args)
    return wait_for_processes(processes)


if __name__ == "__main__":
    if os.name == "nt":
        signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(130))
    raise SystemExit(main())
