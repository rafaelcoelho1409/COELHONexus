"""BD knowledge graph extraction errors."""


class GraphOutputLimitError(RuntimeError):
    """The provider truncated a graph response at its completion-token limit."""
