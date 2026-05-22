"""Exception and data classes for the progress subsystem."""


class IngestCancelled(Exception):
    """Raised by tier modules when the user-triggered cancel flag is set.
    Dispatch catches this and runs the cleanup pass (delete partial MinIO
    content + release lock + mark progress.status='cancelled')."""
