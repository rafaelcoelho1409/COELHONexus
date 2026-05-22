"""Module-level constants for the progress subsystem."""

_TTL_S = 7200
_LOCK_TTL_S = 3900        # 65 min; Celery soft_time_limit is 3600 s
_THROTTLE_S = 1.0
_CANCEL_POLL_THROTTLE_S = 1.0

# Compare-and-delete via Lua so we never release someone else's lock.
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""
