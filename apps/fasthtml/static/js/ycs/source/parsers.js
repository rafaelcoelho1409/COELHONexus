/* YouTube URL / ID parsers — shared by tabs.js (smart-paste mode
 * detection) and channel.js / playlist.js / videos.js (commit-time
 * preview). Each parser returns the parsed shape (with `display`
 * field for the preview UI) or null.
 *
 * Precedence when a string could match multiple kinds:
 *   playlist > channel > video
 * Mirrors yt-dlp's own disambiguation (Tube Archivist #299). */

export function parsePlaylist(text) {
    const t = (text ?? "").trim();
    if (!t) return null;
    let m = t.match(/[?&]list=([A-Za-z0-9_-]+)/);
    if (m) return { kind: "playlist", id: m[1], display: m[1] };
    m = t.match(/^((?:PL|UU|LL|RD|OL)[A-Za-z0-9_-]{10,})$/);
    if (m) return { kind: "playlist", id: m[1], display: m[1] };
    return null;
}

export function parseChannel(text) {
    const t = (text ?? "").trim();
    if (!t) return null;
    let m = t.match(/youtube\.com\/(@[\w.-]+)/);
    if (m) return { kind: "channel", handle: m[1], display: m[1] };
    m = t.match(/youtube\.com\/channel\/(UC[\w-]{22})/);
    if (m) return { kind: "channel", id: m[1], display: m[1] };
    m = t.match(/^(@[\w.-]+)$/);
    if (m) return { kind: "channel", handle: m[1], display: m[1] };
    m = t.match(/^(UC[A-Za-z0-9_-]{22})$/);
    if (m) return { kind: "channel", id: m[1], display: m[1] };
    return null;
}

export function parseVideo(text) {
    const t = (text ?? "").trim();
    if (!t) return null;
    let m = t.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/)([A-Za-z0-9_-]{11})/);
    if (m) return { kind: "video", id: m[1], display: m[1] };
    m = t.match(/^([A-Za-z0-9_-]{11})$/);
    if (m) return { kind: "video", id: m[1], display: m[1] };
    return null;
}

/* Which mode does this text look like? Precedence: playlist > channel
 * > video. Used by tabs.js for smart-paste detection. */
export function detectMode(text) {
    if (parsePlaylist(text)) return "playlist";
    if (parseChannel(text))  return "channel";
    if (parseVideo(text))    return "videos";
    return null;
}
