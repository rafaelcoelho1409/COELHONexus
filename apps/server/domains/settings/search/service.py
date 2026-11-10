"""Safe persistence and bounded connection checks for configured Search services."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import domains
from langchain_mcp_adapters.client import MultiServerMCPClient

from . import params


def _provider(provider_id: str) -> params.SearchProvider:
    for provider in params.PROVIDERS:
        if provider.provider_id == provider_id:
            return provider
    raise ValueError("unsupported search connection")


def _endpoint(provider: params.SearchProvider) -> str | None:
    if provider.provider_id == "coelho_search":
        value = os.environ.get("COELHO_SEARCH_MCP_URL", "").strip()
        if not value:
            return None
        try:
            parsed = urlsplit(value)
            host = parsed.hostname or ""
            trusted_internal_http = host.casefold() == "localhost"
            if host:
                try:
                    trusted_internal_http = trusted_internal_http or ipaddress.ip_address(host).is_loopback
                except ValueError:
                    trusted_internal_http = trusted_internal_http or host.casefold().endswith((".svc", ".svc.cluster.local"))
            secure = parsed.scheme == "https" or (parsed.scheme == "http" and trusted_internal_http)
            if not secure or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
                return None
            parsed.port  # Validate malformed ports before returning the URL.
        except ValueError:
            return None
        return value.rstrip("/")
    return provider.endpoint


def _safe_endpoint(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return "Configured endpoint"
    # Never return query parameters or embedded credentials from a
    # deployment-configured endpoint to the browser.
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    host = f"{hostname}:{parsed.port}" if parsed.port is not None else hostname
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def endpoint_fingerprint(provider_id: str) -> str | None:
    endpoint = _endpoint(_provider(provider_id))
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest() if endpoint else None


def credential_fingerprint(provider_id: str) -> str | None:
    definition = _provider(provider_id)
    credential = domains.settings.credentials.service.resolve_key(definition.key_env)
    if not credential:
        return None
    return hashlib.sha256(f"{provider_id}\0{credential}".encode("utf-8")).hexdigest()


def _records(settings: dict[str, Any]) -> dict[str, Any]:
    value = settings.get("search_mcp")
    return value if isinstance(value, dict) else {}


def _provider_records(settings: dict[str, Any]) -> dict[str, Any]:
    value = _records(settings).get("providers")
    return value if isinstance(value, dict) else {}


def read_settings_view() -> dict[str, Any]:
    store = domains.settings.credentials.service.get_store()
    settings = store.read_settings() or {}
    records = _provider_records(settings)
    providers = []
    for definition in params.PROVIDERS:
        record = records.get(definition.provider_id)
        record = record if isinstance(record, dict) else {}
        status = store.key_status(definition.key_env)
        endpoint = _endpoint(definition)
        tools = record.get("tools") if isinstance(record.get("tools"), list) else []
        expected_fingerprint = endpoint_fingerprint(definition.provider_id)
        expected_credential_fingerprint = credential_fingerprint(definition.provider_id)
        checked_at = record.get("last_checked_at")
        if type(checked_at) not in {int, float}:
            checked_at = None
        last_error = record.get("last_test_error")
        providers.append({
            "provider_id": definition.provider_id,
            "name": definition.name,
            "endpoint": _safe_endpoint(endpoint),
            "account_url": definition.account_url,
            "available": endpoint is not None,
            "key_required": definition.key_required,
            "has_key": status.has_key,
            "key_source": status.source,
            "key_last4": status.last4,
            "connected": (
                bool(record.get("last_test_ok"))
                and endpoint is not None
                and record.get("endpoint_fingerprint") == expected_fingerprint
                and record.get("credential_fingerprint") == expected_credential_fingerprint
                and (not definition.key_required or status.has_key)
            ),
            "last_checked_at": checked_at,
            "tools": [tool for tool in tools if isinstance(tool, str)][:params.MAX_DISCOVERED_TOOLS],
            "last_test_error": last_error[:200] if isinstance(last_error, str) else None,
        })
    default_provider = _records(settings).get("default_provider")
    connected_ids = {provider["provider_id"] for provider in providers if provider["connected"]}
    if default_provider not in connected_ids:
        default_provider = None
    return {"default_provider": default_provider, "providers": providers}


def save_provider_key(provider_id: str, api_key: str | None) -> dict[str, Any]:
    definition = _provider(provider_id)
    if _endpoint(definition) is None:
        raise ValueError("the COELHO Search MCP endpoint is not configured on this deployment")
    store = domains.settings.credentials.service.get_store()
    if api_key is not None:
        if api_key.strip():
            store.set_key(definition.key_env, api_key.strip())
        else:
            store.delete_key(definition.key_env)
    settings = store.read_settings() or {}
    search_settings = _records(settings)
    provider_records = _provider_records(settings)
    prior = provider_records.get(provider_id)
    provider_records[provider_id] = {
        **(prior if isinstance(prior, dict) else {}),
        "last_test_ok": False,
        "last_checked_at": None,
        "tools": [],
        "last_test_error": None,
    }
    search_settings["providers"] = provider_records
    search_settings.setdefault("default_provider", None)
    settings["search_mcp"] = search_settings
    store.write_settings(settings)
    return read_settings_view()


def set_default_provider(provider_id: str | None) -> dict[str, Any]:
    if provider_id is not None:
        _provider(provider_id)
    store = domains.settings.credentials.service.get_store()
    settings = store.read_settings() or {}
    search_settings = _records(settings)
    if provider_id is not None:
        record = _provider_records(settings).get(provider_id)
        if (
            not isinstance(record, dict)
            or not record.get("last_test_ok")
            or _endpoint(_provider(provider_id)) is None
            or record.get("endpoint_fingerprint") != endpoint_fingerprint(provider_id)
            or record.get("credential_fingerprint") != credential_fingerprint(provider_id)
        ):
            raise ValueError("test this available search connection successfully before selecting it")
    search_settings["default_provider"] = provider_id
    settings["search_mcp"] = search_settings
    store.write_settings(settings)
    return read_settings_view()


def record_test(
    provider_id: str,
    result: dict[str, Any],
    fingerprint: str | None,
    credential_hash: str | None,
) -> None:
    store = domains.settings.credentials.service.get_store()
    settings = store.read_settings() or {}
    search_settings = _records(settings)
    provider_records = _provider_records(settings)
    prior = provider_records.get(provider_id)
    provider_records[provider_id] = {
        **(prior if isinstance(prior, dict) else {}),
        "last_test_ok": bool(result.get("ok")),
        "last_checked_at": time.time(),
        "endpoint_fingerprint": fingerprint,
        "credential_fingerprint": credential_hash,
        "tools": result.get("tools", []),
        "last_test_error": result.get("error"),
    }
    search_settings["providers"] = provider_records
    search_settings.setdefault("default_provider", None)
    settings["search_mcp"] = search_settings
    store.write_settings(settings)


async def test_provider(provider_id: str) -> dict[str, Any]:
    definition = _provider(provider_id)
    endpoint = _endpoint(definition)
    started_at = time.monotonic()
    if endpoint is None:
        return {
            "ok": False,
            "provider_id": provider_id,
            "latency_ms": 0,
            "tools": [],
            "error": "The COELHO Search MCP endpoint is not configured on this deployment.",
        }

    api_key = domains.settings.credentials.service.resolve_key(definition.key_env)
    if definition.key_required and not api_key:
        return {
            "ok": False,
            "provider_id": provider_id,
            "latency_ms": 0,
            "tools": [],
            "error": "Save an API key before testing this service.",
        }

    if provider_id == "coelho_search" or provider_id == "parallel":
        return await _test_mcp_provider(
            provider_id,
            endpoint,
            api_key,
            started_at,
        )

    try:
        request = _provider_probe(provider_id, endpoint, api_key)
        async with httpx.AsyncClient(timeout = params.SEARCH_REQUEST_TIMEOUT_S) as client:
            response = await client.request(**request)
        ok, error = _provider_probe_result(provider_id, response)
        return {
            "ok": ok,
            "provider_id": provider_id,
            "latency_ms": int((time.monotonic() - started_at) * 1000),
            "tools": [],
            "error": error,
        }
    except Exception as error:  # noqa: BLE001 — report only the failure class; never expose request headers.
        return {
            "ok": False,
            "provider_id": provider_id,
            "latency_ms": int((time.monotonic() - started_at) * 1000),
            "tools": [],
            "error": f"Connection failed ({type(error).__name__}).",
        }


async def _test_mcp_provider(
    provider_id: str,
    endpoint: str,
    api_key: str,
    started_at: float,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        client = MultiServerMCPClient({
            provider_id: {
                "url": endpoint,
                "transport": "streamable_http",
                **({"headers": headers} if headers else {}),
            },
        })
        discovered = await asyncio.wait_for(client.get_tools(), timeout = params.MCP_CONNECT_TIMEOUT_S)
        tool_names = sorted({tool.name for tool in discovered if isinstance(tool.name, str)})
        definition = _provider(provider_id)
        if provider_id == "coelho_search":
            has_search = any("search" in name.casefold() for name in tool_names)
        else:
            has_search = any(name in tool_names for name in definition.required_search_tools)
        if not tool_names or not has_search:
            return {
                "ok": False,
                "provider_id": provider_id,
                "latency_ms": int((time.monotonic() - started_at) * 1000),
                "tools": tool_names[:params.MAX_DISCOVERED_TOOLS],
                "error": "The connection responded, but no supported search tool was found.",
            }
        return {
            "ok": True,
            "provider_id": provider_id,
            "latency_ms": int((time.monotonic() - started_at) * 1000),
            "tools": tool_names[:params.MAX_DISCOVERED_TOOLS],
            "error": None,
        }
    except Exception as error:  # noqa: BLE001 — report only the failure class; never expose request headers.
        return {
            "ok": False,
            "provider_id": provider_id,
            "latency_ms": int((time.monotonic() - started_at) * 1000),
            "tools": [],
            "error": f"Connection failed ({type(error).__name__}).",
        }


def _provider_probe(provider_id: str, endpoint: str, api_key: str) -> dict[str, Any]:
    headers: dict[str, str] = {"Accept": "application/json"}
    request: dict[str, Any] = {"method": "GET", "url": endpoint, "headers": headers}

    if provider_id == "linkup":
        request["url"] = "https://api.linkup.so/v1/credits/balance"
        headers["Authorization"] = f"Bearer {api_key}"
    elif provider_id == "you":
        if api_key:
            request["url"] = "https://api.you.com/v1/billing/account_balance"
            headers["X-API-Key"] = api_key
        else:
            request.update({
                "method": "POST",
                "json": {"query": "COELHO Nexus connection check", "count": 1},
            })
            headers["Content-Type"] = "application/json"
    elif provider_id == "exa":
        request.update({
            "method": "POST",
            "json": {
                "query": "COELHO Nexus connection check",
                "type": "auto",
                "numResults": 1,
            },
        })
        headers["Authorization"] = f"Bearer {api_key}"
        headers["Content-Type"] = "application/json"
    elif provider_id == "geekflare":
        request.update({
            "method": "POST",
            "json": {
                "query": "COELHO Nexus connection check",
                "limit": 1,
                "source": "web",
                "format": "json",
            },
        })
        headers["x-api-key"] = api_key
        headers["Content-Type"] = "application/json"
    elif provider_id == "tavily":
        if api_key:
            request["url"] = "https://api.tavily.com/usage"
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            request.update({
                "method": "POST",
                "json": {
                    "query": "COELHO Nexus connection check",
                    "search_depth": "basic",
                    "max_results": 1,
                    "include_answer": False,
                },
            })
            headers["X-Tavily-Access-Mode"] = "keyless"
            headers["Content-Type"] = "application/json"
    elif provider_id == "tinyfish":
        request.update({
            "params": {"query": "COELHO Nexus connection check", "results": 1},
        })
        headers["X-API-Key"] = api_key
    elif provider_id == "firecrawl":
        request["url"] = "https://api.firecrawl.dev/v1/team/credit-usage"
        headers["Authorization"] = f"Bearer {api_key}"
    elif provider_id == "serper":
        request["url"] = "https://google.serper.dev/account"
        headers["X-API-KEY"] = api_key
    elif provider_id == "jina":
        request["url"] = "https://embeddings-dashboard-api.jina.ai/api/v1/api_key/user"
        request["params"] = {"api_key": api_key}
    elif provider_id == "serpapi":
        request["url"] = "https://serpapi.com/account.json"
        request["params"] = {"api_key": api_key}
    else:
        raise ValueError("unsupported search connection")

    return request


def _provider_probe_result(provider_id: str, response: httpx.Response) -> tuple[bool, str | None]:
    if not response.is_success:
        return False, f"Connection failed (HTTP {response.status_code})."
    try:
        payload = response.json()
    except ValueError:
        return False, "The service returned an unreadable response."
    if not isinstance(payload, dict):
        return False, "The service returned an unreadable response."
    if payload.get("error"):
        return False, "The service rejected the connection check."
    if provider_id == "geekflare" and str(payload.get("apiStatus", "")).casefold() != "success":
        return False, "The service rejected the connection check."
    if provider_id == "firecrawl" and payload.get("success") is False:
        return False, "The service rejected the connection check."
    return True, None
