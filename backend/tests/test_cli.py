import subprocess
import sys

COMMANDS = ["create-admin", "download-history", "research", "engine", "lab-worker", "check-engine"]


def test_every_documented_command_is_registered() -> None:
    """Los Dockerfile/compose invocan estos comandos: si falta uno, el servicio no arranca."""
    out = subprocess.run(
        [sys.executable, "-m", "app.cli", "--help"], capture_output=True, text=True, check=True
    ).stdout
    missing = [c for c in COMMANDS if c not in out]
    assert missing == [], f"comandos no registrados en la CLI: {missing}"
