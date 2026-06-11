import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_main_import_does_not_require_live_credentials():
    env = os.environ.copy()
    for key in (
        "PACIFICA_ACCOUNT",
        "PACIFICA_AGENT_PRIVATE_KEY",
        "PACIFICA_API_KEY",
        "EXTENDED_API_KEY",
        "EXTENDED_PRIVATE_KEY",
        "EXTENDED_PUBLIC_KEY",
        "EXTENDED_VAULT_ID",
    ):
        env.pop(key, None)

    result = subprocess.run(
        [sys.executable, "-c", "import main; print('import ok')"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "import ok" in result.stdout
