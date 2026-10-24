"""postgres domain — pure connection-string helpers (no I/O, deterministic)."""
from __future__ import annotations

from urllib.parse import quote, urlparse


def build_url(user: str, password: str, host: str, port: str | int, database: str) -> str:
    """`postgresql://user[:password]@host:port/database`. User and password are
    percent-encoded because the password may contain `%`, `&`, `#`, `!`, `^`,
    which would break URL parsing as raw chars."""
    user = quote(user, safe = "")
    password = quote(password, safe = "")
    auth = f"{user}:{password}@" if password else f"{user}@"
    return f"postgresql://{auth}{host}:{port}/{database}"


def target_database(url: str) -> str:
    """Database name the URL points at ('' when absent)."""
    return (urlparse(url).path or "/").lstrip("/")


def admin_url(url: str) -> str:
    """Same URL pointed at the `postgres` admin database (user/password/host/port preserved)."""
    return url.rsplit("/", 1)[0] + "/postgres"


def quote_identifier(name: str) -> str:
    """Double-quoted SQL identifier — `CREATE DATABASE` can't bind params."""
    return '"' + name.replace('"', '""') + '"'
