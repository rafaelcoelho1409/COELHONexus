# NOTE: Use the command "uv tool install click" before using this new file.
"""Convert a .env file into a Kubernetes Secret. Cross-platform port of upload_env_to_k3d.sh.

Usage: python upload_env_to_k3d.py [env-file] [namespace] [secret-name]
   or: ./upload_env_to_k3d.py [env-file] [namespace] [secret-name]   (Linux/macOS)

Requires only `kubectl` on PATH — no bash, no jq, no coreutils base64.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import click

DEFAULT_ENV_FILE = ".env"
DEFAULT_NAMESPACE = "coelhonexus"
DEFAULT_SECRET_NAME = "coelhonexus-secret"
BASE64_PROBE_MIN_LEN = 20


def strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def maybe_decode_base64(value: str) -> str:
    """Mirrors the shell script's heuristic: if a value looks like base64
    and decodes to printable text, store the decoded form instead."""
    if len(value) <= BASE64_PROBE_MIN_LEN:
        return value
    try:
        decoded_bytes = base64.b64decode(value, validate=True)
    except Exception:
        return value
    if not decoded_bytes:
        return value
    try:
        decoded_text = decoded_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return value
    if decoded_text and all(c.isprintable() or c.isspace() for c in decoded_text):
        print(f"Decoded base64 value")
        return decoded_text
    return value


def to_secret_key(env_key: str) -> str:
    """EVAGPT_AGENT_ID -> evagpt-agent-id"""
    return env_key.lower().replace("_", "-")


def parse_env_file(env_path: Path) -> dict[str, str]:
    secret_data: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue
        if line.startswith("GITLAB_") or line.startswith("PROJECT_ID"):
            print(f"Skipping GitLab variable: {line.split('=', 1)[0]}")
            continue
        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = strip_quotes(value.strip())

        clean_value = value.replace("\x00", "").replace("\r", "")
        final_value = maybe_decode_base64(clean_value)

        secret_key = to_secret_key(key)
        secret_data[secret_key] = final_value

        preview = final_value if len(final_value) <= 20 else final_value[:20] + "..."
        print(f"{secret_key}: {preview}")

    return secret_data


def run(cmd: list[str], input_text: str | None = None, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        check=check,
    )


def ensure_namespace(namespace: str) -> None:
    dry_run = run(["kubectl", "create", "namespace", namespace, "--dry-run=client", "-o", "yaml"])
    if dry_run.returncode == 0:
        run(["kubectl", "apply", "-f", "-"], input_text=dry_run.stdout)


def delete_existing_secret(secret_name: str, namespace: str) -> None:
    run(["kubectl", "delete", "secret", secret_name, "-n", namespace])


def apply_secret(secret_name: str, namespace: str, secret_data: dict[str, str]) -> subprocess.CompletedProcess:
    manifest = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": secret_name, "namespace": namespace},
        "type": "Opaque",
        "data": {k: base64.b64encode(v.encode("utf-8")).decode("ascii") for k, v in secret_data.items()},
    }
    return run(["kubectl", "apply", "-f", "-"], input_text=json.dumps(manifest))


def get_secret_key_count(secret_name: str, namespace: str) -> str:
    result = run(["kubectl", "get", "secret", secret_name, "-n", namespace, "-o", "json"])
    if result.returncode != 0:
        return "?"
    try:
        data = json.loads(result.stdout).get("data", {})
        return str(len(data))
    except json.JSONDecodeError:
        return "?"


@click.command(help="Convert a .env file into a Kubernetes Secret. Cross-platform port of upload_env_to_k3d.sh.")
@click.argument("env_file", default=DEFAULT_ENV_FILE, required=False, type=click.Path())
@click.argument("namespace", default=DEFAULT_NAMESPACE, required=False)
@click.argument("secret_name", default=DEFAULT_SECRET_NAME, required=False)
def main(env_file: str, namespace: str, secret_name: str) -> None:
    env_path = Path(env_file)

    print(f"Converting {env_file} to Kubernetes secret...")
    print(f"Namespace: {namespace}")
    print(f"Secret name: {secret_name}")
    print()

    if not env_path.is_file():
        print(f"File '{env_file}' not found!")
        print("Create a .env file with your variables:")
        print("   AWS_ACCESS_KEY_ID=your-key")
        print("   OPENAI_API_KEY=your-key")
        print("   etc...")
        sys.exit(1)

    ensure_namespace(namespace)
    delete_existing_secret(secret_name, namespace)

    print("Processing environment variables...")
    secret_data = parse_env_file(env_path)

    print()
    print("Creating secret...")
    apply_result = apply_secret(secret_name, namespace, secret_data)

    if apply_result.returncode == 0:
        print(f"Secret '{secret_name}' created successfully!")
        key_count = get_secret_key_count(secret_name, namespace)
        print(f"Secret contains {key_count} environment variables")
        print()
        print("Your secret is ready for use in:")
        print(f"   - Namespace: {namespace}")
        print(f"   - Secret name: {secret_name}")
        print()
        print(f"Verify with: kubectl get secret {secret_name} -n {namespace} -o yaml")
        print("Start development with: skaffold dev")
        sys.exit(0)
    else:
        print("Failed to create secret. Debug output:")
        print(apply_result.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
