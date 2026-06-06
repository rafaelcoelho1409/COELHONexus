/* YCS · Step 1 · Source — entry. Mirrors the Python split in
 * features/ycs/source/{search,videos,channel,playlist}.py — one
 * module per mode, plus shared helpers (shared.js) and tab/
 * smart-paste wiring (tabs.js).
 *
 * Each module is side-effect — it attaches its own submit / click /
 * paste listeners on import. Importing this entry from main.js loads
 * them all in one round-trip. */
import "./source/tabs.js";
import "./source/search.js";
import "./source/videos.js";
import "./source/channel.js";
import "./source/playlist.js";
// parsers.js + unfurl.js are pulled in transitively by the modules
// above — no side-effect-only imports needed.
