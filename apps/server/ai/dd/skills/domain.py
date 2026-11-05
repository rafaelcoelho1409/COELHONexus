from . import keys, params


def folder_for(agent_name: str) -> str:
    try:
        return keys.AGENT_SKILL_FOLDERS[agent_name]
    except KeyError as error:
        raise ValueError(f"unknown Docs Distiller skill role: {agent_name}") from error


def source_for(agent_name: str) -> str:
    return f"{params.VIRTUAL_ROOT}/{folder_for(agent_name)}/"
