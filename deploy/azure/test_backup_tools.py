"""Unit tests for protected PostgreSQL backup/restore client configuration."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
HELPER = SCRIPT_DIR / "postgres-client-env.py"


def run_helper(
    env_file: Path,
    output_env: Path,
    output_passfile: Path,
    *,
    compare_same_host: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(HELPER),
        "--env-file",
        str(env_file),
        "--output-env",
        str(output_env),
        "--output-passfile",
        str(output_passfile),
    ]
    if compare_same_host:
        command += ["--url-key", "RESTORE_DATABASE_URL", "--compare-url-key", "DATABASE_URL"]
    return subprocess.run(command, check=False, capture_output=True, text=True)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="preco-bao-backup-test-") as temp:
        root = Path(temp)
        ca = root / "ca.pem"
        ca.write_text("test ca", encoding="utf-8")
        env_file = root / "env"
        env_file.write_text(
            "\n".join(
                [
                    "DATABASE_URL='postgresql+psycopg2://backup%3Auser:p%40ss%3Aword%5C%23@db.internal:5433/appdb?sslmode=verify-full&sslrootcert=/ignored/in/container.pem'",
                    "RESTORE_DATABASE_URL='postgresql+psycopg2://restore:restore-pass@restore.internal:5432/appdb?sslmode=verify-full&sslrootcert=/ignored/in/container.pem'",
                    f"AZURE_PG_ROOT_CERT_PATH={ca}",
                    "SMTP_FROM_NAME=Preço Bão",
                    "BACKUP_GPG_RECIPIENT=Backup Key",
                    "BACKUP_GPG_FINGERPRINT=0123456789ABCDEF",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        output_env, passfile = root / "postgres.env", root / "pgpass"
        result = run_helper(env_file, output_env, passfile)
        assert result.returncode == 0, result.stderr
        env_text = output_env.read_text(encoding="utf-8")
        pass_text = passfile.read_text(encoding="utf-8")
        assert "PGHOST=db.internal" in env_text
        assert "PGPORT=5433" in env_text
        assert "PGSSLMODE=verify-full" in env_text
        assert "PGSSLROOTCERT=/run/secrets/azure-postgres-root-ca.pem" in env_text
        assert "p@ss\\:word\\\\#" in pass_text
        assert "p@ss" not in env_text
        if os.name != "nt":
            assert output_env.stat().st_mode & 0o777 == 0o600
            assert passfile.stat().st_mode & 0o777 == 0o600

        same_host_env = root / "same-host-env"
        same_host_env.write_text(
            env_file.read_text(encoding="utf-8").replace("restore.internal", "db.internal"),
            encoding="utf-8",
        )
        rejected = run_helper(
            same_host_env,
            root / "restore.env",
            root / "restore.pgpass",
            compare_same_host=True,
        )
        assert rejected.returncode != 0
        assert "different PostgreSQL server" in rejected.stderr
        assert "p@ss" not in output_env.read_text(encoding="utf-8")
    print("Backup client configuration tests passed.")


if __name__ == "__main__":
    main()
