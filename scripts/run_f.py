"""Launch the shared f agent with explicit local runtime and API settings."""
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / 'variants/astra_io_250hz_task_first_e_merged_20261105'

if __name__ == '__main__':
    if '--help' not in sys.argv and '-h' not in sys.argv:
        subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/check_f.py')], check=True)
    sys.path.insert(0, str(VARIANT))
    runpy.run_path(str(VARIANT / 'run_local.py'), run_name='__main__')
