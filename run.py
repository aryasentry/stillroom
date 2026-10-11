#!/usr/bin/env python3
"""Local launcher. All dependencies live inside this project."""
import argparse, importlib.util, os, shutil, sqlite3, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
VENV_PY=ROOT/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')

def command(args,**kwargs):
    result=subprocess.run([str(a) for a in args],cwd=ROOT,**kwargs)
    if result.returncode:raise SystemExit(result.returncode)

def main():
    parser=argparse.ArgumentParser(description='Stillroom — local room memories')
    parser.add_argument('--setup',action='store_true',help='Install locked dependencies locally and build frontend (internet needed)')
    parser.add_argument('--setup-reconstruction',action='store_true',help='Prepare pinned local photo-to-3D tools (internet needed once)')
    parser.add_argument('--check',action='store_true',help='Run preflight checks without starting server')
    parser.add_argument('--port',type=int,default=8848)
    parser.add_argument('--data-dir',type=Path,help='Managed local data directory; default .stillroom')
    args=parser.parse_args()
    if not (3,11)<=sys.version_info[:2]<=(3,13):raise SystemExit('Stillroom requires Python 3.11–3.13. This build was tested on Python 3.13 / macOS.')
    if not 1024<=args.port<=65535:raise SystemExit('Choose a port from 1024 to 65535.')
    if args.setup:
        npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
        node=shutil.which('node')
        if not npm or not node:raise SystemExit('Install Node.js 22.12+ (or 24 LTS) and Python 3.11–3.13, then rerun setup.')
        if not VENV_PY.exists():command([sys.executable,'-m','venv',ROOT/'.venv'])
        command([VENV_PY,'-m','pip','install','-r',ROOT/'requirements-lock.txt'])
        command([npm,'ci','--prefix','frontend'])
        command([VENV_PY,ROOT/'scripts'/'generate_demo.py'])
        command([npm,'run','build','--prefix','frontend'])
        if args.setup_reconstruction:command([VENV_PY,ROOT/'scripts'/'setup_reconstruction.py'])
        print('\nSetup complete. Start with python run.py (macOS: python3 run.py).')
        return
    if VENV_PY.exists() and Path(sys.prefix).resolve()!=(ROOT/'.venv').resolve():
        command([VENV_PY,Path(__file__).resolve(),*sys.argv[1:]])
        return
    required=['fastapi','uvicorn','multipart','PIL','av']
    absent=[m for m in required if importlib.util.find_spec(m) is None]
    if absent or not (ROOT/'frontend'/'dist'/'index.html').is_file():raise SystemExit('Run setup first: python3 run.py --setup (Windows: py run.py --setup). Missing: '+(', '.join(absent) or 'compiled frontend'))
    with sqlite3.connect(':memory:') as db:db.execute('CREATE VIRTUAL TABLE check_fts USING fts5(text)')
    if args.setup_reconstruction:
        command([sys.executable,ROOT/'scripts'/'setup_reconstruction.py'])
        return
    print(f'Python {sys.version.split()[0]} · SQLite {sqlite3.sqlite_version} with FTS5 · frontend built',flush=True)
    if args.check:
        from backend.reconstruction_runtime import runtime_status
        status=runtime_status()
        import av
        print('Local video decoding: PyAV '+av.__version__,flush=True)
        print('Photo reconstruction: '+('tools ready' if status['available'] else status['message']),flush=True)
        return
    if args.data_dir:os.environ['STILLROOM_DATA_DIR']=str(args.data_dir.resolve())
    print(f'Open http://127.0.0.1:{args.port} — local files only. Ctrl+C stops the server.',flush=True)
    import uvicorn
    os.chdir(ROOT)
    uvicorn.run('backend.app:create_app',factory=True,host='127.0.0.1',port=args.port,log_level='warning')

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:pass
