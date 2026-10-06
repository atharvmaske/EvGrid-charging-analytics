import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "fastapi" 

sys.path.insert(0, str(APP))
sys.path.append(str(ROOT))