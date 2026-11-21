#!/usr/bin/env python3
"""Create short-lived, permission-restricted libpq configuration for Docker tools."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.strip()):
            raise ValueError(f"Invalid environment assignment on line {line_number}")
        value = raw_value.strip()
        if value.startswith(("'", '"')):
            try:
                parsed = shlex.split(value, comments=True, posix=True)
            except ValueError as exc:
                raise ValueError(f"Invalid quoted value on line {line_number}") from exc
            if len(parsed) != 1:
                raise ValueError(f"Expected one value on line {line_number}")
            value = parsed[0]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key.strip()] = value
    return values


def parse_database_url(raw_url: str, ca_path: str) -> tuple[dict[str, str], str]:
    url = urlsplit(raw_url)
    if url.scheme != "postgresql+psycopg2" or not url.hostname or not url.username or not url.password:
        raise ValueError("Database URL must be a complete postgresql+psycopg2 URL")
    query = dict(part.split("=", 1) for part in url.query.split("&") if "=" in part)
    if query.get("sslmode") != "verify-full":
        raise ValueError("PostgreSQL client requires sslmode=verify-full")
    if not Path(ca_path).is_file():
        raise ValueError("Trusted PostgreSQL CA bundle is missing")
    database = unquote(url.path.lstrip("/"))
    username = unquote(url.username)
    password = unquote(url.password)
    if any("\n" in item or "\r" in item for item in (database, username, password)):
        raise ValueError("Newline characters are not supported in PostgreSQL connection values")
    settings = {
        "PGHOST": url.hostname,
        "PGPORT": str(url.port or 5432),
        "PGUSER": username,
        "PGDATABASE": database,
        "PGSSLMODE": "verify-full",
        "PGSSLROOTCERT": "/run/secrets/azure-postgres-root-ca.pem",
        "PGPASSFILE": "/run/secrets/pgpass",
    }
    def escape_pgpass_field(value: str) -> str:
        return value.replace("\\", "\\\\").replace(":", "\\:")

    passfile = ":".join(
        escape_pgpass_field(value)
        for value in (url.hostname, str(url.port or 5432), database, username, password)
    )
    return settings, passfile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--url-key", default="DATABASE_URL")
    parser.add_argument("--compare-url-key")
    parser.add_argument("--output-env", required=True, type=Path)
    parser.add_argument("--output-passfile", required=True, type=Path)
    args = parser.parse_args()
    try:
        values = read_env(args.env_file)
        raw_url = values[args.url_key]
        ca_path = values.get("AZURE_PG_ROOT_CERT_PATH")
        if not ca_path:
            raise ValueError("AZURE_PG_ROOT_CERT_PATH is required")
        settings, passfile = parse_database_url(raw_url, ca_path)
        if args.compare_url_key:
            compare = urlsplit(values[args.compare_url_key])
            target = urlsplit(raw_url)
            if not compare.hostname or not target.hostname or compare.hostname.casefold() == target.hostname.casefold():
                raise ValueError("Restore target must use a different PostgreSQL server from production")
        args.output_env.write_text("".join(f"{key}={value}\n" for key, value in settings.items()), encoding="utf-8")
        os.chmod(args.output_env, 0o600)
        args.output_passfile.write_text(passfile + "\n", encoding="utf-8")
        os.chmod(args.output_passfile, 0o600)
        return 0
    except (KeyError, OSError, ValueError) as exc:
        print(f"PostgreSQL client configuration failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
