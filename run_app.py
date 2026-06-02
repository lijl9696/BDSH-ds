from pathlib import Path
import multiprocessing
import sys


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from tg_reporter.app_tk import main


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
