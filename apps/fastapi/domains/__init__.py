"""domains — business domains (bounded contexts): docs_distiller, llm, youtube.

Each domain owns its router, schemas, service/graph code, tasks and exceptions.
A domain may import `core`/`shared`; domains should not import each other except
through documented, one-way edges (e.g. docs_distiller → llm rotator).
"""
