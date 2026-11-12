"""Settings in one place. Secrets come from environment variables (or a .env
file), never from the code."""
import os
from pathlib import Path

try:  # optional: loads a .env file next to this one if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", HERE / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "solutions.db"

CLASSIFY_MODEL = os.environ.get("CLASSIFY_MODEL", "claude-haiku-5-5")
PITCH_MODEL = os.environ.get("PITCH_MODEL", "claude-sonnet-5-5")

# ClaimBuster scores run from 0 to 1. At or above this, a claim is flagged as
# "check-worthy". It is a triage cutoff, not a verdict, so tune it.
CHECKWORTHY_THRESHOLD = float(os.environ.get("CHECKWORTHY_THRESHOLD", "0.5"))
CLAIMBUSTER_DELAY = float(os.environ.get("CLAIMBUSTER_DELAY", "1.0"))  # seconds


def claimbuster_key():
    return os.environ.get("CLAIMBUSTER_API_KEY")
