"""Register YCS Ask discovery resources."""
from __future__ import annotations
from . import resources

from fastmcp import FastMCP


def register_resources(mcp: FastMCP) -> None:
    handlers = resources.AdaptiveAskResources()

    @mcp.resource(
        "ycs://ask/capabilities",
        name="ycs_ask_capabilities",
        title="YCS Ask Capabilities",
        description="Adaptive YCS Ask modes, corpus, citations, and channel-scoping behavior.",
        mime_type="application/json",
        tags={"ycs", "ask", "capabilities"},
    )
    def capabilities() -> dict:
        """Return supported modes and citation/scope behavior."""
        return handlers.capabilities()

    @mcp.resource(
        "ycs://ask/channels",
        name="ycs_ask_channels",
        title="Indexed YCS Channels",
        description="Up to 100 indexed YouTube channels ordered by video count, for scoping YCS Ask.",
        mime_type="application/json",
        tags={"ycs", "ask", "channels"},
    )
    async def channels() -> dict:
        """Return indexed channel IDs that can scope a YCS Ask call."""
        return await handlers.channels()
