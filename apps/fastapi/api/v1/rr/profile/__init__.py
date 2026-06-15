"""Profile-scoped endpoints — currently only the reset-seen action.
The profile catalog (list/create) hasn't been built; today every scan
uses `profile_id='default'`."""
from .router import router
