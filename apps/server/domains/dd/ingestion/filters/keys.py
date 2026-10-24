from __future__ import annotations


# Frameworks shipping multi-language docs — trigger language-specific path filtering.
POLYGLOT_FRAMEWORKS: frozenset[str] = frozenset({
    "opentelemetry",
    "grpc",
    "protobuf",
    "protocol buffers",
    "kubernetes",
    "prometheus",
    "apache kafka",
    "kafka",
    "rabbitmq",
    "elastic",
    "elasticsearch",
    "pulsar",
    "etcd",
})


# Bare "c" intentionally maps to "c-lang" only — bare "c" over-matches.
LANGUAGE_PATH_MAP: dict[str, list[str]] = {
    "python":     ["python", "py"],
    "javascript": ["javascript", "js", "nodejs", "node"],
    "typescript": ["typescript", "ts"],
    "go":         ["go", "golang"],
    "rust":       ["rust", "rs"],
    "java":       ["java"],
    "kotlin":     ["kotlin", "kt"],
    "csharp":     ["csharp", "cs", "dotnet", "net"],
    "ruby":       ["ruby", "rb"],
    "php":        ["php"],
    "swift":      ["swift"],
    "cpp":        ["cpp", "c-plus-plus", "c++"],
    "c":          ["c-lang"],
    "elixir":     ["elixir", "ex"],
    "erlang":     ["erlang"],
    "scala":      ["scala"],
    "haskell":    ["haskell", "hs"],
}
