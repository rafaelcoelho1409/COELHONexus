"""ycs/agents/byok — Redis key for the persisted user LLM config.

Single source of truth. The PUT `/agents/config` endpoint writes here
and `get_byok_config()` reads it back. The key shape matches the
deprecated strict-port name."""
from __future__ import annotations


CONFIG_REDIS_KEY = "coelhonexus:youtube:agents:config"
