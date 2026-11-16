"""Load project-local settings for terminal and IDE launches."""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_settings(root: Path = PROJECT_ROOT):
    # Explicit process environment remains authoritative for CI or temporary overrides.
    load_dotenv(root / ".env", override=False)
    credential = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if credential:
        path = Path(credential).expanduser()
        if not path.is_absolute():
            path = root / path
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(path.resolve())
