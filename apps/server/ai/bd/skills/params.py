from pathlib import Path


LOCAL_ROOT = Path(__file__).parent / "ingestion"
VIRTUAL_ROOT = "/skills/ingestion"
ALLOWED_TOOL_NAMES = frozenset({"read_file"})
