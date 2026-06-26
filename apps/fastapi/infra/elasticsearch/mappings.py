"""infra/elasticsearch — index mappings for the two deprecated indexes.

Module-level dict literals (pure data, no I/O) — `service.py:ensure_indexes`
reads them at startup and creates the indexes if missing.

DO NOT add fields without bumping a schema version and writing a reindex job — ES doesn't allow mapping changes on existing fields."""
from __future__ import annotations


METADATA_MAPPING: dict = {
    "mappings": {
        "properties": {
            # Core fields
            "id":              {"type": "keyword"},
            "title":           {"type": "text", "analyzer": "standard"},
            "fulltitle":       {"type": "text"},
            "description":     {"type": "text", "analyzer": "standard"},
            # URLs
            "webpage_url":     {"type": "keyword"},
            "thumbnail_url":   {"type": "keyword"},
            # Channel
            "channel":                 {"type": "text"},
            "channel_id":              {"type": "keyword"},
            "channel_url":             {"type": "keyword"},
            "channel_follower_count":  {"type": "long"},
            "channel_is_verified":     {"type": "boolean"},
            "uploader":                {"type": "text"},
            "uploader_id":             {"type": "keyword"},
            # Playlist context (when extracted from a playlist)
            "playlist_id":    {"type": "keyword"},
            "playlist_title": {"type": "text"},
            # Dates
            "upload_date":    {"type": "keyword"},
            "timestamp":      {"type": "date", "format": "epoch_second"},
            "release_date":   {"type": "keyword"},
            # Duration
            "duration":        {"type": "integer"},
            "duration_string": {"type": "keyword"},
            # Engagement
            "view_count":      {"type": "long"},
            "like_count":      {"type": "long"},
            "dislike_count":   {"type": "long"},
            "comment_count":   {"type": "long"},
            "average_rating":  {"type": "float"},
            # Classification
            "categories":   {"type": "keyword"},
            "tags":         {"type": "keyword"},
            "age_limit":    {"type": "integer"},
            "availability": {"type": "keyword"},
            # Live status
            "is_live":     {"type": "boolean"},
            "was_live":    {"type": "boolean"},
            "live_status": {"type": "keyword"},
            # Chapters (nested for per-chapter range queries)
            "chapters": {
                "type": "nested",
                "properties": {
                    "title":      {"type": "text"},
                    "start_time": {"type": "float"},
                    "end_time":   {"type": "float"},
                },
            },
            # Subtitle availability (lang codes from yt-dlp)
            "subtitles":          {"type": "keyword"},
            "automatic_captions": {"type": "keyword"},
            # Extraction provenance
            "_extracted_at": {"type": "date"},
        },
    },
    "settings": {
        "number_of_shards":   1,
        "number_of_replicas": 0,
    },
}


TRANSCRIPTIONS_MAPPING: dict = {
    "mappings": {
        "properties": {
            "id":            {"type": "keyword"},  # Composite: {video_id}_{lang}
            "video_id":      {"type": "keyword"},
            "lang":          {"type": "keyword"},
            "content":       {"type": "text", "analyzer": "standard"},
            "is_auto":       {"type": "boolean"},
            "method":        {"type": "keyword"},  # "get_panel" / "get_transcript" / "dom_scrape" / "direct_api"
            # Denormalized for filter-by-channel / filter-by-playlist
            "channel_id":    {"type": "keyword"},
            "playlist_id":   {"type": "keyword"},
            "_extracted_at": {"type": "date"},
        },
    },
    "settings": {
        "number_of_shards":   1,
        "number_of_replicas": 0,
    },
}
