"""Start the local Next.js frontend and FastAPI backend together."""
import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true", help="Use Next.js development mode")
    args = parser.parse_args()
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise SystemExit("Install Node.js, then run npm install inside frontend/.")
    if not (ROOT / "frontend/node_modules").is_dir():
        raise SystemExit("Run npm install inside frontend/ first.")
    if not args.dev and not (ROOT / "frontend/.next/BUILD_ID").is_file():
        raise SystemExit("Run npm run build inside frontend/, or use --dev.")
    for port in (8000, 3000):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(f"Port {port} is in use. Stop the existing service before starting CareerAI.")
    environment = {**os.environ, "CAREERAI_API_URL": "http://127.0.0.1:8000", "NEXT_TELEMETRY_DISABLED": "1"}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    children = []
    try:
        children.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "src.api.main:app", "--host", "127.0.0.1", "--port", "8000"], cwd=ROOT, env=environment, creationflags=flags))
        children.append(subprocess.Popen([npm, "run", "dev" if args.dev else "start"], cwd=ROOT / "frontend", env=environment, creationflags=flags))
        print("CareerAI: http://127.0.0.1:3000\nAPI documentation: http://127.0.0.1:8000/docs\nPress Ctrl+C to stop both services.", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(1)
        return next((child.returncode for child in children if child.returncode), 0)
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True, creationflags=flags)
                else:
                    child.terminate()
                child.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
