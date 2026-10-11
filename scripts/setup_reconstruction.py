"""Install pinned local photo reconstruction tools, without uploading media."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.reconstruction_runtime import install_runtime, runtime_status

if __name__ == '__main__':
    try:
        install_runtime(lambda message: print(message, flush=True))
        print(runtime_status())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
