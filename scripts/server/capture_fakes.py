"""In-memory stand-in for the part of MinIO a tier's capture uses, shared by the offline ingestion tests."""


class CaptureMinio:
    """Writes, reads back, lists and clears objects by key prefix. Text and bytes are kept as written."""

    def __init__(self):
        self.objects: dict[str, str | bytes] = {}

    async def write(self, key, content, content_type=""):
        self.objects[key] = content
        return len(content)

    async def read_text(self, key, encoding="utf-8"):
        value = self.objects[key]
        return value.decode(encoding) if isinstance(value, bytes) else value

    async def read_bytes(self, key):
        value = self.objects[key]
        return value if isinstance(value, bytes) else value.encode("utf-8")

    async def list(self, prefix):
        return sorted(k for k in self.objects if k.startswith(prefix))

    async def delete(self, key):
        self.objects.pop(key, None)

    async def delete_prefix(self, prefix):
        doomed = [k for k in self.objects if k.startswith(prefix)]
        for key in doomed:
            del self.objects[key]
        return len(doomed)

    async def write_many(self, items):
        for key, text, _content_type in items:
            self.objects[key] = text
