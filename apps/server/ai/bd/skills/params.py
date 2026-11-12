import pathlib


LOCAL_ROOT = pathlib.Path(__file__).parent / "ingestion"
VIRTUAL_ROOT = "/skills/ingestion"
ALLOWED_TOOL_NAMES = frozenset({"read_file"})
