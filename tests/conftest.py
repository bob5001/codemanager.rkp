"""Load .env / .env.local into os.environ before the test modules build their DSNs.

The test modules read DB_* straight from os.environ, but pydantic-settings reads
.env files without exporting them, so without this DB_PASSWORD is empty and every
DB-backed test fails with InvalidPasswordError. .env.local wins over .env (it
points host-side processes at localhost:5433 instead of the compose-network
`db:5432`), and anything already exported in the shell wins over both.
"""
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(_ROOT / ".env.local", override=False)
load_dotenv(_ROOT / ".env", override=False)
