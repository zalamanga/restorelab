"""Generate a local secret without replacing credentials for existing lab volumes."""
from pathlib import Path
import secrets
import subprocess


def main():
    root = Path(__file__).resolve().parent.parent
    destination = root / ".secrets" / "postgres_password"
    if destination.is_symlink():
        raise SystemExit("Refusing a symbolic secret path.")
    if destination.exists():
        value = destination.read_text(encoding="utf-8").strip()
        if len(value) < 32 or any(character.isspace() for character in value):
            raise SystemExit("The local PostgreSQL secret is invalid; it was not changed.")
        return
    result = subprocess.run(
        ["docker", "volume", "ls", "--filter", "label=com.docker.compose.project=restorelab", "--quiet"],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode:
        raise SystemExit("Docker is unavailable; no secret was generated.")
    if result.stdout.strip():
        raise SystemExit("Existing RestoreLab volumes require the original secret; no replacement was generated.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(secrets.token_urlsafe(36) + "\n")
    try:
        destination.chmod(0o600)
    except OSError:
        pass
    print("RestoreLab local secret is ready.")


if __name__ == "__main__":
    main()
