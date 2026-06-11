"""Hacker News (Algolia) search tool — wraps `hn.algolia.com/api/v1/search`
as an MCP tool, with HN-unique signals (points · num_comments) plus
URL→arxiv_id extraction for cross-source dedup with the arxiv and
huggingface_daily_papers tools.

This is the news / community-traction tier of the radar — papers that are
gaining real-world attention TODAY, paired with the academic core (arxiv,
S2, HF). The cross-tier correlation pattern: "this paper from 3 months ago
is now blowing up on HN with a working GitHub repo" → maximal signal.
"""
