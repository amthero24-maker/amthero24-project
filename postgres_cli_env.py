"""Build a private libpq environment for PostgreSQL command-line tools.

Connection values remain in the child process environment only. They are never returned by
reports, printed, or placed in argv. Existing PG connection variables are cleared first so
runner or host defaults cannot override the explicit database URL.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from psycopg.conninfo import conninfo_to_dict


_ENV_MAP = {
    "host": "PGHOST",
    "hostaddr": "PGHOSTADDR",
    "port": "PGPORT",
    "user": "PGUSER",
    "password": "PGPASSWORD",
    "dbname": "PGDATABASE",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "client_encoding": "PGCLIENTENCODING",
    "options": "PGOPTIONS",
    "application_name": "PGAPPNAME",
    "sslmode": "PGSSLMODE",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcrl": "PGSSLCRL",
    "sslcrldir": "PGSSLCRLDIR",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
}


def postgres_cli_environment(
    database_url: str,
    *,
    base_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a deterministic child environment parsed by psycopg/libpq."""
    cleaned = str(database_url or "").strip()
    if not cleaned.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")
    try:
        values = conninfo_to_dict(cleaned)
    except Exception as exc:
        raise ValueError("DATABASE_URL is not a valid PostgreSQL connection URL") from exc

    environment = dict(base_environment or os.environ)
    environment.pop("DATABASE_URL", None)
    for variable in _ENV_MAP.values():
        environment.pop(variable, None)
    for key, variable in _ENV_MAP.items():
        value = values.get(key)
        if value is not None and str(value) != "":
            environment[variable] = str(value)

    if not environment.get("PGDATABASE"):
        raise ValueError("DATABASE_URL must identify a PostgreSQL database")
    return environment
