"""Pure formatters used in home sections.

`_fmt_int` — locale-style thousand separators ("1,052" not "1052").
`_fmt_bytes` — auto-scaling B/KB/MB/GB/TB, one decimal except for raw bytes."""


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _fmt_bytes(n: int) -> str:
    if not n:
        return "0 B"
    units = ["B", "KB", "MB", "GB"]
    f = float(n)
    for u in units:
        if f < 1024:
            return f"{f:.1f} {u}" if u != "B" else f"{int(f)} B"
        f /= 1024
    return f"{f:.1f} TB"
