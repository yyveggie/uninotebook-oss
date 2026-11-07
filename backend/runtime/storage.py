"""Portable local originals. Paths are derived from owned records, never user paths."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .paths import data_root


class LocalFileStorage:
    def resolve(self, key: str) -> Path:
        root = data_root().resolve()
        path = (root / key).resolve()
        if root not in path.parents:
            raise ValueError("非法存储路径")
        return path

    def document_key(self, user_id: int, document_id: int, filename: str) -> str:
        suffix = Path(filename).suffix.lower()
        if not suffix or not suffix[1:].isalnum():
            suffix = ".bin"
        return f"documents/{int(user_id)}/{int(document_id)}/original{suffix}"

    def original_path(self, document) -> Path:
        # Renaming a document must not change its stored original's location.
        filename = f"original.{document.file_type}" if str(document.file_type).isalnum() else document.filename
        return self.resolve(self.document_key(document.user_id, document.id, filename))

    def write(self, key: str, content: bytes) -> Path:
        target = self.resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return target

    def save_document(self, user_id: int, document_id: int, filename: str, content: bytes) -> Path:
        return self.write(self.document_key(user_id, document_id, filename), content)

    def save_source(self, user_id: int, source_id: int, content: str) -> None:
        self.write(f"sources/{int(user_id)}/{int(source_id)}/original.txt", content.encode("utf-8"))

    def source_text(self, user_id: int, source_id: int) -> str | None:
        path = self.resolve(f"sources/{int(user_id)}/{int(source_id)}/original.txt")
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def delete_source(self, user_id: int, source_id: int) -> None:
        shutil.rmtree(self.resolve(f"sources/{int(user_id)}/{int(source_id)}"), ignore_errors=True)

    def delete_document(self, user_id: int, document_id: int) -> None:
        path = self.resolve(f"documents/{int(user_id)}/{int(document_id)}")
        shutil.rmtree(path, ignore_errors=True)


local_storage = LocalFileStorage()
