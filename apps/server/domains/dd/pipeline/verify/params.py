# How far a block got, lowest to highest: nothing checked, it parses, its imports resolve, it ran.
LEVELS = ("none", "parsed", "resolved", "ran")
# Languages whose blocks are configuration files, parsed with the standard library.
CONFIG_PARSERS = {"json": "json", "toml": "tomllib", "yaml": "yaml", "yml": "yaml"}
# How long a run may take, and how much of its output a report keeps.
RUN_TIMEOUT_S = 20.0
MAX_OUTPUT_CHARS = 1_500
# Blocks shorter than this are not worth a run.
MIN_RUN_LINES = 3
# Whether the worker runs code at all. Off: a block's level stops at `resolved`. Turn it on only where the worker (or a sandbox pod) has no network and no
# secrets in reach: the executor limits time, memory and files but isolates nothing else, and most framework examples need a key to run.
RUN_ENABLED = False
# Limits of a run: address space (bytes), open files, processes, and the seconds of CPU beyond the wall-clock timeout.
RUN_MEMORY_BYTES = 512 * 1024 * 1024
RUN_OPEN_FILES = 64
RUN_PROCESSES = 32
RUN_CPU_GRACE_S = 5
