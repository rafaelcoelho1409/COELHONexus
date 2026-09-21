from __future__ import annotations


class UnmanagedKeyEnv(ValueError):
    """key_env outside keys.MANAGED_KEY_ENVS — blocks env-var exfiltration."""
