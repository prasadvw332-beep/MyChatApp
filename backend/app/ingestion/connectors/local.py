"""Files under a local folder, searched recursively."""

from pathlib import Path

from app.ingestion.connectors import SourceFile


def list_files(source: str) -> list[SourceFile]:
    folder = Path(source).resolve()
    if not folder.is_dir():
        raise ValueError(f"not a folder: {source}")
    return [
        SourceFile(p.relative_to(folder).as_posix(), p.stat().st_size, p.read_bytes)
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    ]
