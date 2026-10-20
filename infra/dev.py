"""Local process supervisor. Stops only child processes created by this command."""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixtures",
        action="store_true",
        help="Explicitly load isolated development fixture",
    )
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if len(os.getenv("AUTH_SECRET", "")) < 32:
        raise SystemExit("먼저 .env의 AUTH_SECRET을 설정하세요.")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "migrations/alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "services.gateway.seed"] + (["--fixtures"] if args.fixtures else []),
        cwd=ROOT,
        check=True,
    )
    commands = [
        [
            sys.executable,
            "-m",
            "uvicorn",
            "services.gateway.main:app_factory",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        [
            sys.executable,
            "-m",
            "uvicorn",
            "services.agent.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8001",
        ],
        [sys.executable, "-m", "services.business_mcp.server"],
        [sys.executable, "-m", "services.rag_mcp.server"],
        [sys.executable, "-m", "services.gateway.outbox.publisher"],
        [sys.executable, "-m", "services.report_worker.main"],
        ["npm", "--prefix", "web", "run", "dev"],
    ]
    children = []

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        for command in commands:
            children.append(subprocess.Popen(command, cwd=ROOT, start_new_session=True))
        print("Day Zero: http://127.0.0.1:5173 (Ctrl+C로 앱 프로세스 종료)", flush=True)
        while True:
            for child in children:
                if child.poll() is not None:
                    raise RuntimeError(f"Child process exited: {child.args}")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)


if __name__ == "__main__":
    main()
