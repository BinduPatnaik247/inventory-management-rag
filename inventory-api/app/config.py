"""Small dependency-free settings loader for local development."""
import os
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE pairs without overwriting environment variables."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


def settings() -> tuple[str, str]:
    load_dotenv(Path.cwd() / ".env")
    key = os.getenv("INVENTORY_API_KEY", "local-dev-key-change-me")
    database = os.getenv("INVENTORY_DB_PATH", "data/inventory.sqlite3")
    return key, database
