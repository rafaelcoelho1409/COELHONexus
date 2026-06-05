"""`/settings` — global BYOK page reachable from the topbar gear."""
from layout.shell import _Shell

from .page import SettingsBody


def register(rt) -> None:
    @rt("/settings")
    def settings_page():
        # active_key = "settings" is not in FEATURES, so no nav pill
        # highlights — correct for a global gear-reached page.
        return _Shell("settings", "Settings", body = SettingsBody())
