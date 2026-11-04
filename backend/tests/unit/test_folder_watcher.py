import time
from pathlib import Path

import pytest

from app.ingestion import folder_watcher
from app.ingestion.folder_watcher import Debouncer, _FolderEventHandler, apply_changes, is_ingestible


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeQueue:
    """queue + doc_store + knowledge_base in memory: documents by content hash, like the unique sha256."""

    def __init__(self) -> None:
        self.batches: list[int] = []
        self.sealed: list[int] = []
        self.docs: dict[str, bytes] = {}  # filename -> content of the indexed version
        self.deleted: list[str] = []

    def create_batch(self, source: str) -> int:
        self.batches.append(len(self.batches) + 1)
        return self.batches[-1]

    def add(self, batch_id: int, filename: str, data: bytes) -> str | None:
        if data in self.docs.values():
            return None
        self.docs[filename] = data
        return filename

    def ids_for_filename(self, filename: str) -> set[str]:
        return {filename} if filename in self.docs else set()

    def delete_document(self, doc_id: str) -> None:
        del self.docs[doc_id]
        self.deleted.append(doc_id)


@pytest.fixture
def fake(monkeypatch) -> FakeQueue:
    q = FakeQueue()
    monkeypatch.setattr(folder_watcher.queue, "create_batch", q.create_batch)
    monkeypatch.setattr(folder_watcher.queue, "add", q.add)
    monkeypatch.setattr(folder_watcher.queue, "seal", q.sealed.append)
    monkeypatch.setattr(folder_watcher.doc_store, "ids_for_filename", q.ids_for_filename)
    monkeypatch.setattr(folder_watcher.knowledge_base, "delete_document", q.delete_document)
    return q


def test_debouncer_waits_until_quiet():
    clock = FakeClock()
    debouncer = Debouncer(quiet_seconds=1.0, clock=clock)
    debouncer.touch(Path("a.pdf"))
    clock.now = 0.8
    debouncer.touch(Path("a.pdf"))  # still being written

    clock.now = 1.5
    assert debouncer.due() == []
    clock.now = 1.8
    assert debouncer.due() == [Path("a.pdf")]
    assert debouncer.due() == []  # released once


@pytest.mark.parametrize("name, expected", [
    ("policy.pdf", True), ("notes.MD", True), ("photo.jpg", False),
    ("~$policy.docx", False), (".policy.txt.swp", False),
])
def test_wanted(name, expected):
    assert is_ingestible(Path(name)) is expected


def test_new_files_are_queued_in_one_sealed_batch(tmp_path, fake):
    (tmp_path / "hr").mkdir()
    (tmp_path / "hr" / "leave.txt").write_text("20 days of leave")
    (tmp_path / "refund.txt").write_text("refund within 30 days")

    result = apply_changes(tmp_path, [tmp_path / "hr" / "leave.txt", tmp_path / "refund.txt"])

    assert result == {"queued": 2, "skipped": 0, "deleted": 0}
    assert set(fake.docs) == {"hr/leave.txt", "refund.txt"}  # relative names, like `cli <folder>`
    assert fake.batches == fake.sealed == [1]


def test_unchanged_file_is_skipped_without_a_batch_being_left_open(tmp_path, fake):
    doc = tmp_path / "refund.txt"
    doc.write_text("refund within 30 days")
    apply_changes(tmp_path, [doc])

    assert apply_changes(tmp_path, [doc]) == {"queued": 0, "skipped": 1, "deleted": 0}
    assert fake.batches == fake.sealed


def test_deleted_file_is_removed_from_the_knowledge_base(tmp_path, fake):
    doc = tmp_path / "refund.txt"
    doc.write_text("refund within 30 days")
    apply_changes(tmp_path, [doc])
    doc.unlink()

    assert apply_changes(tmp_path, [doc]) == {"queued": 0, "skipped": 0, "deleted": 1}
    assert apply_changes(tmp_path, [doc])["deleted"] == 0  # a late duplicate event does nothing
    assert len(fake.batches) == 1  # no empty batch for a delete-only burst


def test_batch_is_sealed_even_when_queueing_fails(tmp_path, fake, monkeypatch):
    doc = tmp_path / "refund.txt"
    doc.write_text("refund within 30 days")
    monkeypatch.setattr(folder_watcher.queue, "add", lambda *_: (_ for _ in ()).throw(ConnectionError("db down")))

    with pytest.raises(ConnectionError):
        apply_changes(tmp_path, [doc])
    assert fake.sealed == [1]


def test_os_events_reach_the_debouncer(tmp_path):
    from watchdog.observers import Observer

    debouncer = Debouncer(quiet_seconds=0)
    observer = Observer()
    observer.schedule(_FolderEventHandler(debouncer), str(tmp_path), recursive=True)
    observer.start()
    try:
        (tmp_path / "policy.txt").write_text("hello")
        (tmp_path / "~$policy.docx").write_text("lock file")
        deadline, seen = time.monotonic() + 5, set()
        while time.monotonic() < deadline and not seen:
            time.sleep(0.05)
            seen.update(p.name for p in debouncer.due())
    finally:
        observer.stop()
        observer.join()
    assert seen == {"policy.txt"}
