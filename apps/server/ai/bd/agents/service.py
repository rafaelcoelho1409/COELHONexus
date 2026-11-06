"""What every Book Distiller deep agent shares when it is built."""
from __future__ import annotations

from deepagents import (
    GeneralPurposeSubagentProfile, 
    HarnessProfile, 
    register_harness_profile
)
from deepagents.profiles.harness import harness_profiles


def isolate_harness_profile(model_name: str) -> None:
    """Make the profile of the Rotator model the same for every role: no base prompt, no general-purpose subagent, no excluded tool.

    Deep Agents keys the profile by the model, registrations are additive, and their excluded tools union. Every role (and the
    Docs Distiller roles in the same worker) uses the same Rotator model, so an exclusion registered once would silently hide those
    tools from every agent built after it. The key is cleared before it is set, and what a role may use is limited by its own
    `middleware.service.ToolAllowlistMiddleware`. The profile is read when `create_deep_agent` runs, so call this right before."""
    key = f"openai:{model_name}"
    harness_profiles._HARNESS_PROFILES.pop(key, None)
    register_harness_profile(
        key,
        HarnessProfile(
            base_system_prompt = "",
            general_purpose_subagent = GeneralPurposeSubagentProfile(enabled = False),
        ),
    )
