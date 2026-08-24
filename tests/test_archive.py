import gzip

import pytest

from fsa_pipeline.archive import AlreadyArchived, day_dir, write_gzip_atomic


def test_write_gzip_atomic_roundtrip(tmp_path):
    dest = tmp_path / "857_bath-and-north-east-somerset.xml.gz"
    write_gzip_atomic(b"<xml>hello</xml>", dest)

    assert dest.exists()
    assert not dest.with_suffix(dest.suffix + ".tmp").exists()
    with gzip.open(dest, "rb") as f:
        assert f.read() == b"<xml>hello</xml>"


def test_write_gzip_atomic_never_overwrites(tmp_path):
    dest = tmp_path / "857_bath-and-north-east-somerset.xml.gz"
    write_gzip_atomic(b"first", dest)

    with pytest.raises(AlreadyArchived):
        write_gzip_atomic(b"second", dest)

    with gzip.open(dest, "rb") as f:
        assert f.read() == b"first"


def test_day_dir_creates_dated_directory(tmp_path):
    path = day_dir(tmp_path, "2026-08-20")

    assert path == tmp_path / "2026-08-20"
    assert path.is_dir()
