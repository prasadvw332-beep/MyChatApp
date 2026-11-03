"""Bulk-import sources: a local folder and an S3 bucket (with a fake client), and the CLI filter."""

from types import SimpleNamespace

import pytest

from app.ingestion import cli
from app.ingestion.connectors import local, s3


class FakeS3:
    def __init__(self, buckets: dict[str, dict[str, bytes]]):
        self.buckets = buckets

    def bucket_exists(self, bucket):
        return bucket in self.buckets

    def list_objects(self, bucket, prefix="", recursive=False):
        for key, data in sorted(self.buckets[bucket].items()):
            if key.startswith(prefix):
                yield SimpleNamespace(object_name=key, size=len(data), is_dir=key.endswith("/"))

    def get_object(self, bucket, key):
        data = self.buckets[bucket][key]
        return SimpleNamespace(read=lambda: data, close=lambda: None, release_conn=lambda: None)


@pytest.mark.parametrize("url, expected", [
    ("s3://docs", ("docs", "")),
    ("s3://docs/", ("docs", "")),
    ("s3://docs/hr", ("docs", "hr/")),
    ("s3://docs/hr/2026/", ("docs", "hr/2026/")),
])
def test_parse_s3_url(url, expected):
    assert s3.parse(url) == expected


def test_parse_rejects_missing_bucket():
    with pytest.raises(ValueError):
        s3.parse("s3://")


def test_s3_lists_under_prefix_with_relative_names():
    client = FakeS3({"docs": {
        "hr/policy.pdf": b"%PDF",
        "hr/2026/leave.docx": b"PK",
        "hr/": b"",  # folder marker some tools create
        "hrx/other.txt": b"not under hr/",
        "finance/budget.md": b"# Budget",
    }})

    files = s3.list_files("s3://docs/hr", client=client)

    assert [f.name for f in files] == ["2026/leave.docx", "policy.pdf"]
    assert files[1].size == 4
    assert files[1].read() == b"%PDF"


def test_s3_missing_bucket_is_a_clear_error():
    with pytest.raises(ValueError, match="bucket not found"):
        s3.list_files("s3://nope", client=FakeS3({}))


def test_local_lists_relative_posix_names(tmp_path):
    (tmp_path / "hr").mkdir()
    (tmp_path / "hr" / "policy.md").write_bytes(b"# Leave")
    (tmp_path / "readme.txt").write_bytes(b"hi")

    files = local.list_files(str(tmp_path))

    assert [f.name for f in files] == ["hr/policy.md", "readme.txt"]
    assert files[0].read() == b"# Leave"


def test_local_missing_folder_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="not a folder"):
        local.list_files(str(tmp_path / "missing"))


def test_find_files_keeps_supported_and_small_files(monkeypatch):
    client = FakeS3({"docs": {
        "a.pdf": b"x", "b.docx": b"x", "c.md": b"x", "d.txt": b"x",
        "e.xlsx": b"x", "big.pdf": b"x" * (cli.MAX_UPLOAD_MB * 1024 * 1024 + 1),
    }})
    monkeypatch.setattr(s3, "_client", lambda: client)

    assert [f.name for f in cli.find_files("s3://docs")] == ["a.pdf", "b.docx", "c.md", "d.txt"]


def test_wait_polls_until_the_batches_are_done(monkeypatch, capsys):
    states = iter([
        (False, {"queued": 2, "processing": 1, "ready": 0, "failed": 0}),
        (True, {"queued": 0, "processing": 0, "ready": 2, "failed": 1}),
    ])
    monkeypatch.setattr(cli.queue, "summary", lambda ids: next(states))
    monkeypatch.setattr(cli.queue, "failures", lambda b: [{"filename": "scan.pdf", "error": "no text"}] if b == 7 else [])
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)

    cli.wait([6, 7])

    out = capsys.readouterr().out
    assert out.count("waiting for workers") == 2
    assert "failed: scan.pdf: no text" in out
