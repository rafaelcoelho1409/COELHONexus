"""Guard: every Markdown file under `ai/` (prompts' SYSTEM.md, skills' SKILL.md and references) is read at runtime, so the server image must carry it.
`.dockerignore` drops all `*.md` and whitelists the runtime ones; a new `prompts/<tab>/` or `skills/<tab>/` folder that is not whitelisted builds fine and then
crashes the container on import (FileNotFoundError: .../SYSTEM.md).

    cd apps/server && .venv/bin/python ../../scripts/server/test_docker_context.py
"""
import sys
from pathlib import Path, PurePosixPath

SERVER = Path(__file__).resolve().parents[2] / "apps" / "server"
patterns = [line.strip() for line in (SERVER / ".dockerignore").read_text().splitlines() if line.strip() and not line.startswith("#")]
ignored = [p for p in patterns if not p.startswith("!") and p.endswith(".md")]
whitelisted = [p[1:] for p in patterns if p.startswith("!")]

missing = []
for path in sorted((SERVER / "ai").rglob("*.md")):
    relative = PurePosixPath(path.relative_to(SERVER).as_posix())
    if any(relative.full_match(p) for p in ignored) and not any(relative.full_match(p) for p in whitelisted):
        missing.append(str(relative))

if missing:
    print(f"FAIL: {len(missing)} runtime Markdown file(s) are dropped by .dockerignore:")
    print("\n".join(f"  {m}" for m in missing))
    sys.exit(1)
print(f"ok  every Markdown file under ai/ reaches the image ({sum(1 for _ in (SERVER / 'ai').rglob('*.md'))} files)")
