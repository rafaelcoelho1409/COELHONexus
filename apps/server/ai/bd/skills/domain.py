from . import params


AGENT_SKILL_FOLDERS = {
    "page_auditor": "page-auditor",
    "text_repair": "text-repair",
    "structure_repair": "structure-repair",
    "metadata_normalizer": "metadata-normalizer",
}


def folder_for(agent_name: str) -> str:
    try:
        return AGENT_SKILL_FOLDERS[agent_name]
    except KeyError as error:
        raise ValueError(f"unknown Book Distiller skill role: {agent_name}") from error


def source_for(agent_name: str) -> str:
    return f"{params.VIRTUAL_ROOT}/{folder_for(agent_name)}/"
