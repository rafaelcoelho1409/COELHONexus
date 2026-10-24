# Themes Seen — Cross-Scan Memory

This file accumulates the **themes emitted by past synthesis runs**. The
synthesis subagent reads it BEFORE producing new themes so it can:

1. Mark new themes that DON'T appear here as **emerging** (high value)
2. De-prioritize themes that DO appear here repeatedly (signal saturation)
3. Notice when a previously-emerging theme is now dominant (the operator
   has covered it enough to skip)

Format: one line per `<theme name> · <last seen scan date> · <occurrence count>`.

The agent appends to this file at the end of each scan; manual edits OK.

## Seen themes

(Empty on first run. Synthesis appends here.)
