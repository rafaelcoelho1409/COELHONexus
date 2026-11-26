"""Operational settings for Book Distiller embedding migrations."""

STATE_TTL_S = 30 * 24 * 60 * 60
TASK_NAME = "domains.bd.rag.embedding_migration.task.reembed_books"
BOOK_CONCURRENCY = 8
