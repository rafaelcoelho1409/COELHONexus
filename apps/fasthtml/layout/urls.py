"""Top-level navigation URL table.

Single source of truth for the (key, label, url) tuples driving the
primary nav. `_Shell` reads this to render nav pills + decide which
pill is active. Adding a new feature = one line here."""


FEATURES = [
    ("home", "Home", "/"),
    ("docs-distiller", "Docs Distiller", "/docs-distiller"),
    ("youtube-content-search", "YouTube Content Search", "/youtube-content-search"),
    ("coming-soon", "Coming Soon", "/coming-soon"),
]
