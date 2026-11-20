YCS_CYPHER_BROWSE = """
MATCH (n)
WHERE  n:Document OR n:Video OR n:Channel OR n:__Entity__
WITH   n,
       labels(n)[0] AS label,
       coalesce(n.id, n.video_id, toString(elementId(n))) AS key
RETURN label,
       key,
       coalesce(n.title, n.name, n.id, key)              AS title,
       coalesce(n.description, n.text, '')               AS snippet,
       coalesce(n.webpage_url, '')                       AS url,
       properties(n)                                     AS properties
ORDER BY label, title
LIMIT  $limit
"""

YCS_CYPHER_SEARCH = """
MATCH (n)
WHERE  (n:Document OR n:Video OR n:Channel OR n:__Entity__)
  AND  (
        toLower(toString(coalesce(n.title, '')))        CONTAINS $needle
     OR toLower(toString(coalesce(n.name, '')))         CONTAINS $needle
     OR toLower(toString(coalesce(n.id, '')))           CONTAINS $needle
     OR toLower(toString(coalesce(n.video_id, '')))     CONTAINS $needle
     OR toLower(toString(coalesce(n.description, '')))  CONTAINS $needle
     OR toLower(toString(coalesce(n.text, '')))         CONTAINS $needle
  )
WITH   n,
       labels(n)[0] AS label,
       coalesce(n.id, n.video_id, toString(elementId(n))) AS key
RETURN label,
       key,
       coalesce(n.title, n.name, n.id, key)              AS title,
       coalesce(n.description, n.text, '')               AS snippet,
       coalesce(n.webpage_url, '')                       AS url,
       properties(n)                                     AS properties
LIMIT  $limit
"""

# All read procedures only — no APOC dep.
SCHEMA_CYPHER_LABELS = "CALL db.labels() YIELD label RETURN collect(label) AS labels"
SCHEMA_CYPHER_RELS   = "CALL db.relationshipTypes() YIELD relationshipType RETURN collect(relationshipType) AS rels"
SCHEMA_CYPHER_PROPS  = (
    "CALL db.schema.nodeTypeProperties() "
    "YIELD nodeLabels, propertyName, propertyTypes "
    "RETURN nodeLabels, propertyName, propertyTypes "
    "ORDER BY nodeLabels, propertyName"
)
# db.schema.visualization() would be cheaper but is APOC-only.
SCHEMA_CYPHER_REL_PATTERNS = """
MATCH (a)-[r]->(b)
WITH labels(a)[0] AS src, type(r) AS rel, labels(b)[0] AS dst, count(*) AS n
WHERE src IS NOT NULL AND dst IS NOT NULL
RETURN src, rel, dst, n
ORDER BY n DESC
LIMIT 50
"""
SCHEMA_CYPHER_LABEL_SAMPLES = """
MATCH (n)
WHERE labels(n)[0] = $label
RETURN n
LIMIT 3
"""
