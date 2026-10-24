"""Env handling of `infra.langfuse` and `infra.opentelemetry`: the `LANGFUSE_*` / `OTEL_*` variables are read once in each
module's `params.py` and the services use `params.X`. Each case runs in a fresh interpreter, as a pod does: env first, import after.

    cd apps/server && .venv/bin/python ../../scripts/server/test_observability_env.py"""
import os
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "apps" / "server"
CLEAN = {k: v for k, v in os.environ.items() if not k.startswith(("LANGFUSE_", "OTEL_")) and k not in (
    "DEPLOYMENT_ENVIRONMENT", "GIT_SHA", "HELM_CHART_VERSION")}
CLEAN.update(REDIS_HOST="localhost", REDIS_PORT="6379", REDIS_PASSWORD="", ENVIRONMENT="local", PYTHONPATH=str(SERVER))

CONFIGURED = '''
import infra
from opentelemetry.sdk.trace import TracerProvider
lf, ot = infra.langfuse, infra.opentelemetry
assert lf.service._resolve_host() == "http://lf:3000", lf.service._resolve_host()
assert lf.service.is_available() is True
assert lf.params.OTLP_TIMEOUT_S == 7 and ot.params.OTLP_TIMEOUT_S == 11
assert lf.domain.host_from_otlp_endpoint("http://h:3000/api/public/otel/") == "http://h:3000"
assert lf.domain.host_from_otlp_endpoint("http://h:3000") == "http://h:3000"
attrs = ot.service.build_resource().attributes
assert attrs["service.name"] == "svc-x" and attrs["service.git_sha"] == "abc123", dict(attrs)
assert attrs["team"] == "a" and attrs["env"] == "b" and attrs["service.namespace"] == "coelhonexus", dict(attrs)
assert "service.helm_chart_version" not in attrs
assert ot.service._bsp_kwargs() == {"max_queue_size": 999, "max_export_batch_size": 256, "schedule_delay_millis": 10000, "export_timeout_millis": 30000}
assert lf.service.add_exporter(TracerProvider()) is True
assert ot.service.add_alloy_exporter(TracerProvider()) is True
print("configured ok")
'''

UNSET = '''
import infra
from opentelemetry.sdk.trace import TracerProvider
lf, ot = infra.langfuse, infra.opentelemetry
assert lf.service._resolve_host() is None and lf.service.is_available() is False and lf.service.get_client() is None
assert lf.service.add_exporter(TracerProvider()) is False and ot.service.add_alloy_exporter(TracerProvider()) is False
assert ot.service.build_resource().attributes["service.name"] == "coelhonexus-server"
assert lf.params.OTLP_TIMEOUT_S == 30 and ot.params.OTLP_TIMEOUT_S == 30 and ot.params.BSP_MAX_QUEUE_SIZE == 6144
print("unset ok")
'''

CASES = [
    ("configured", CONFIGURED, {
        "LANGFUSE_OTLP_ENDPOINT": "http://lf:3000/api/public/otel/v1/traces", "LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk",
        "LANGFUSE_OTLP_TIMEOUT": "7", "OTEL_SERVICE_NAME": "svc-x", "OTEL_GIT_SHA": "abc123", "OTEL_BSP_MAX_QUEUE_SIZE": "999",
        "OTEL_RESOURCE_ATTRIBUTES": "team=a,env=b", "OTEL_EXPORTER_OTLP_TIMEOUT": "11", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://alloy:4317",
    }),
    ("unset: every entry point fails soft, defaults apply", UNSET, {}),
]

for label, code, env in CASES:
    done = subprocess.run([sys.executable, "-c", code], cwd=SERVER, env={**CLEAN, **env}, capture_output=True, text=True)
    assert done.returncode == 0, f"FAIL {label}\n{done.stdout}\n{done.stderr[-1500:]}"
    print(f"ok  {label}")
print("ALL OK")
