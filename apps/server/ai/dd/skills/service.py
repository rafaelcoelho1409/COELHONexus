from . import domain, params

from deepagents.backends.utils import create_file_data


def files_for(agent_name: str) -> dict[str, dict[str, str]]:
    local_root = params.LOCAL_ROOT / domain.folder_for(agent_name)
    source = domain.source_for(agent_name)
    if not local_root.is_dir():
        raise FileNotFoundError(f"Docs Distiller skill folder not found: {local_root}")

    files = {}
    for path in sorted(local_root.rglob("*.md")):
        relative_path = path.relative_to(local_root).as_posix()
        files[f"{source}{relative_path}"] = create_file_data(
            path.read_text(encoding = "utf-8"),
        )

    if not any(path.endswith("/SKILL.md") for path in files):
        raise FileNotFoundError(f"no SKILL.md found for Docs Distiller role: {agent_name}")
    return files
