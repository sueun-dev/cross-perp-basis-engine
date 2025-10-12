from __future__ import annotations

import os
from pathlib import Path

_ENV_LOADED = False


def load_env(path: str | None = None) -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return

    env_path = Path(path) if path else Path(".env")
    if not env_path.is_file():
        _ENV_LOADED = True
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("export ", "export\t")):
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            line = parts[1].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

    _ENV_LOADED = True
