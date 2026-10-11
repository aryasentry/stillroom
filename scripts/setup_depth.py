"""Run with the repository Python: python -m scripts.setup_depth."""
from backend.depth_runtime import install_runtime
if __name__=='__main__':
    install_runtime()
    print('Optional sparse-frame model is ready. All inference runs locally.')
