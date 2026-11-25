from importlib import import_module

graph_extractor = import_module(".graph_extractor", __name__)

__all__ = ["graph_extractor"]
