"""LanceDB index contracts through the Rust pipeline."""

import asyncio
import json
import subprocess
import sys

import pytest

pytest.importorskip("lancedb")


class _FailingEmbedder:
    """Embedding provider whose batch call fails."""

    name = "fake"
    model = "fake-embeddings"
    dims = 8

    async def embed(self, texts):
        raise RuntimeError("embed failed")


def _coordinator(tmp_path, embedder=_FailingEmbedder()):
    from chunkhound.core.config.database_config import DatabaseConfig
    from chunkhound.core.types.common import Language
    from chunkhound.parsers.parser_factory import create_parser_for_language
    from chunkhound.providers.database.lancedb_provider import LanceDBProvider
    from chunkhound.services.indexing_coordinator import IndexingCoordinator

    root = tmp_path / "repo"
    root.mkdir()
    config = DatabaseConfig(path=tmp_path / "db", provider="lancedb")
    provider = LanceDBProvider(
        str(config.get_db_path()), base_directory=root, config=config
    )
    provider.connect()
    parser = create_parser_for_language(Language.PYTHON)
    coordinator = IndexingCoordinator(
        provider, root, embedder, {Language.PYTHON: parser}
    )
    return provider, coordinator, config.get_db_path(), root


def _paths(db_dir) -> list[str]:
    import lancedb

    table = lancedb.connect(str(db_dir)).open_table("files")
    return sorted(row["path"] for row in table.to_arrow().to_pylist())


def test_lance_rust_index_reindex_delete_skip_and_null_embedding(tmp_path, monkeypatch):
    monkeypatch.setenv("CHUNKHOUND_USE_RUST", "1")
    provider, coordinator, db_dir, root = _coordinator(tmp_path)
    (root / "main.py").write_text("def hello():\n    return 1\n")
    (root / "blob.bin").write_bytes(b"\x00binary")

    try:
        first = asyncio.run(coordinator.process_directory(root, patterns=["**/*"]))
        assert first["status"] == "success", first
        assert first["pipeline"] == "rust"
        assert first.get("errors", 0) >= 1, first
        chunk_table = provider._chunks_table.to_lance()
        chunk_rows = chunk_table.to_table(columns=["id", "embedding"]).to_pylist()
        assert chunk_rows
        assert all(row["embedding"] is None for row in chunk_rows), chunk_rows
        ids = [row["id"] for row in chunk_rows]
        assert len(ids) == len(set(ids))
        second = asyncio.run(coordinator.process_directory(root, patterns=["**/*"]))
        assert second["status"] == "success", second
        again = provider._chunks_table.to_lance().to_table(columns=["id"]).to_pylist()
        assert len(again) == len(chunk_rows)

        skipped = (
            provider._files_table.to_lance()
            .to_table(columns=["path", "skip_reason"], filter="path = 'blob.bin'")
            .to_pylist()
        )
        assert skipped and skipped[0]["skip_reason"]
    finally:
        provider.disconnect()

    child = tmp_path / "search_child.py"
    child.write_text(
        "import sys\n"
        "from chunkhound.providers.database.lancedb_provider import LanceDBProvider\n"
        "provider = LanceDBProvider(sys.argv[1], base_directory=sys.argv[2])\n"
        "provider.connect()\n"
        "try:\n"
        "    rows, _info = provider.search_regex('hello')\n"
        "    paths = sorted({row.get('file_path') or '' for row in rows})\n"
        "    print('\\n'.join(paths))\n"
        "finally:\n"
        "    provider.disconnect()\n",
        encoding="utf-8",
    )
    launches = []
    for _ in range(2):
        proc = subprocess.run(
            [sys.executable, str(child), str(db_dir), str(root)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        launches.append(proc.stdout.strip())
    assert launches[0] == launches[1]
    assert "main.py" in launches[0].splitlines()

    from chunkhound.core.types.common import Language
    from chunkhound.parsers.parser_factory import create_parser_for_language
    from chunkhound.providers.database.lancedb_provider import LanceDBProvider
    from chunkhound.services.indexing_coordinator import IndexingCoordinator

    reopened = LanceDBProvider(str(db_dir), base_directory=root)
    reopened.connect()
    parser = create_parser_for_language(Language.PYTHON)
    deleter = IndexingCoordinator(
        reopened, root, _FailingEmbedder(), {Language.PYTHON: parser}
    )
    try:
        (root / "main.py").unlink()
        third = asyncio.run(deleter.process_directory(root, patterns=["**/*"]))
        assert third["status"] == "success", third
    finally:
        reopened.disconnect()

    assert "main.py" not in _paths(db_dir)
    assert "blob.bin" in _paths(db_dir)


def test_identical_text_at_different_lines_gets_distinct_chunk_ids(tmp_path):
    from chunkhound.providers.database.lance_store import write_batch

    chunk = {
        "chunk_type": "function",
        "code": "return 1",
        "start_byte": None,
        "end_byte": None,
        "language": "python",
        "metadata": None,
        "embedding": None,
        "provider": None,
        "model": None,
    }
    payload = json.dumps(
        {
            "files": [
                {
                    "existing_file_id": None,
                    "path": "dup.py",
                    "mtime": 1.0,
                    "size_bytes": 10,
                    "content_hash": "abc",
                    "language": "python",
                    "skip_reason": None,
                    "chunks": [
                        {**chunk, "symbol": "a", "start_line": 1, "end_line": 2},
                        {**chunk, "symbol": "b", "start_line": 4, "end_line": 5},
                    ],
                }
            ],
            "delete_paths": [],
        }
    )
    db_dir = tmp_path / "lancedb.lancedb"
    write_batch(str(db_dir), payload)
    import lancedb

    rows = lancedb.connect(str(db_dir)).open_table("chunks").to_arrow().to_pylist()
    ids = [row["id"] for row in rows]
    assert len(ids) == 2
    assert ids[0] != ids[1]


def _bare_file(path: str, existing: int | None = None) -> dict:
    return {
        "existing_file_id": existing,
        "path": path,
        "mtime": 1.0,
        "size_bytes": 1,
        "content_hash": "h",
        "language": "python",
        "skip_reason": None,
        "chunks": [],
    }


def test_one_batch_of_file_rows_is_one_fragment_and_ids_are_not_reused(tmp_path):
    from chunkhound.providers.database.lance_store import write_batch
    from chunkhound.providers.database.lancedb_provider import LanceDBProvider

    db_dir = tmp_path / "lancedb.lancedb"
    first = json.dumps(
        {
            "files": [_bare_file("a.py"), _bare_file("b.py"), _bare_file("c.py")],
            "delete_paths": [],
        }
    )
    write_batch(str(db_dir), first)
    import lancedb

    files = lancedb.connect(str(db_dir)).open_table("files")
    rows = files.search().to_list()
    assert LanceDBProvider._fragment_count(files) == 1
    assert sorted(row["id"] for row in rows) == [1, 2, 3]

    second = json.dumps(
        {
            "files": [_bare_file("c.py", existing=3), _bare_file("d.py")],
            "delete_paths": [],
        }
    )
    write_batch(str(db_dir), second)
    files = lancedb.connect(str(db_dir)).open_table("files")
    rows = files.search().to_list()
    by_path = {row["path"]: int(row["id"]) for row in rows}
    assert by_path == {"a.py": 1, "b.py": 2, "c.py": 3, "d.py": 4}
    assert len(rows) == 4
    assert LanceDBProvider._fragment_count(files) == 2


def _offset_chunk(
    symbol: str,
    start_line: int,
    end_line: int,
    code: str,
    start_byte: int | None,
    end_byte: int | None,
) -> dict:
    return {
        "chunk_type": "function",
        "symbol": symbol,
        "code": code,
        "start_line": start_line,
        "end_line": end_line,
        "start_byte": start_byte,
        "end_byte": end_byte,
        "language": "python",
        "metadata": None,
        "embedding": None,
        "provider": None,
        "model": None,
    }


def _span(chunk) -> tuple:
    return (
        chunk.symbol,
        int(chunk.start_line),
        int(chunk.end_line),
        chunk.code,
        None if chunk.start_byte is None else int(chunk.start_byte),
        None if chunk.end_byte is None else int(chunk.end_byte),
    )


def test_indexed_chunks_keep_parser_byte_offsets(tmp_path, monkeypatch):
    """Rust indexing stores the same byte span the parser computed."""
    from chunkhound.core.types.common import FileId, Language
    from chunkhound.parsers.parser_factory import create_parser_for_language

    monkeypatch.setenv("CHUNKHOUND_USE_RUST", "1")
    provider, coordinator, _db_dir, root = _coordinator(tmp_path)
    source = "x = 1\n\ndef hello():\n    return 1\n"
    file_path = root / "main.py"
    file_path.write_text(source)
    parsed = create_parser_for_language(Language.PYTHON).parse_file(
        file_path, FileId(0)
    )
    assert any(chunk.start_byte is not None for chunk in parsed)
    try:
        result = asyncio.run(coordinator.process_directory(root, patterns=["**/*"]))
        assert result["status"] == "success", result
        file_record = provider.get_file_by_path("main.py")
        assert file_record is not None
        file_id = file_record["id"] if isinstance(file_record, dict) else file_record.id
        stored = provider.get_chunks_by_file_id(file_id, as_model=True)
        assert sorted(_span(chunk) for chunk in stored) == sorted(
            _span(chunk) for chunk in parsed
        )
    finally:
        provider.disconnect()


def test_same_line_chunks_are_ordered_by_start_byte(tmp_path):
    """A later chunk written first is still returned in start_byte order."""
    from chunkhound.providers.database.lance_store import write_batch
    from chunkhound.providers.database.lancedb_provider import LanceDBProvider

    db_dir = tmp_path / "lancedb.lancedb"
    payload = json.dumps(
        {
            "files": [
                {
                    **_bare_file("same.py"),
                    "chunks": [
                        _offset_chunk("later", 1, 1, "later", 30, 35),
                        _offset_chunk("earlier", 1, 1, "earlier", 5, 12),
                        _offset_chunk("third", 3, 3, "third", None, None),
                    ],
                }
            ],
            "delete_paths": [],
        }
    )
    file_id = json.loads(write_batch(str(db_dir), payload))["file_ids"][0]
    provider = LanceDBProvider(str(db_dir), base_directory=tmp_path)
    provider.connect()
    try:
        chunks = provider.get_chunks_by_file_id(file_id, as_model=True)
        assert [
            (chunk.symbol, chunk.start_byte, chunk.end_byte) for chunk in chunks
        ] == [
            ("earlier", 5, 12),
            ("later", 30, 35),
            ("third", None, None),
        ]
        ranged = provider.get_chunks_in_range(file_id, 1, 1)
        assert {
            (row["symbol"], row["start_byte"], row["end_byte"]) for row in ranged
        } == {
            ("earlier", 5, 12),
            ("later", 30, 35),
        }
        one = provider.get_chunk_by_id(chunks[0].id, as_model=True)
        assert one is not None
        assert (one.start_byte, one.end_byte) == (5, 12)
    finally:
        provider.disconnect()


def test_existing_chunks_table_gains_byte_columns(tmp_path):
    """A table created without byte columns accepts a later write."""
    import lancedb
    import pyarrow as pa

    from chunkhound.providers.database.lance_store import write_batch
    from chunkhound.providers.database.lancedb_provider import get_chunks_schema

    old_schema = pa.schema(
        [
            field
            for field in get_chunks_schema()
            if field.name not in {"start_byte", "end_byte"}
        ]
    )
    db_dir = tmp_path / "lancedb.lancedb"
    db = lancedb.connect(str(db_dir))
    kept = {
        "id": 1,
        "file_id": 7,
        "content": "kept",
        "start_line": 1,
        "end_line": 1,
        "chunk_type": "function",
        "language": "python",
        "name": "kept",
        "embedding": None,
        "provider": "",
        "model": "",
        "created_time": 1.0,
        "metadata": "{}",
    }
    db.create_table("chunks", schema=old_schema)
    db.open_table("chunks").add(pa.Table.from_pylist([kept], schema=old_schema))

    payload = json.dumps(
        {
            "files": [
                {
                    **_bare_file("new.py"),
                    "chunks": [_offset_chunk("added", 1, 1, "added", 4, 9)],
                }
            ],
            "delete_paths": [],
        }
    )
    write_batch(str(db_dir), payload)
    table = lancedb.connect(str(db_dir)).open_table("chunks")
    rows = {row["name"]: row for row in table.to_arrow().to_pylist()}
    assert rows["kept"]["start_byte"] is None
    assert rows["kept"]["end_byte"] is None
    assert rows["kept"]["content"] == "kept"
    assert (rows["added"]["start_byte"], rows["added"]["end_byte"]) == (4, 9)


def test_embedding_update_keeps_byte_offsets(tmp_path):
    """Rewriting a chunk to store its vector keeps the byte span."""
    from chunkhound.core.models import Chunk
    from chunkhound.core.types.common import ChunkType, Language
    from chunkhound.providers.database.lancedb_provider import LanceDBProvider

    provider = LanceDBProvider(tmp_path / "lancedb.lancedb", base_directory=tmp_path)
    provider.connect()
    try:
        chunk_id = provider.insert_chunk(
            Chunk(
                symbol="hello",
                start_line=2,
                end_line=3,
                code="return 1",
                chunk_type=ChunkType.FUNCTION,
                file_id=1,
                language=Language.PYTHON,
                start_byte=11,
                end_byte=19,
            )
        )
        updated = provider.insert_embeddings_batch(
            [
                {
                    "chunk_id": chunk_id,
                    "embedding": [0.1] * 8,
                    "provider": "fake",
                    "model": "fake-embeddings",
                }
            ]
        )
        assert updated == 1
        loaded = provider.get_chunk_by_id(chunk_id, as_model=True)
        assert loaded is not None
        assert (loaded.start_byte, loaded.end_byte) == (11, 19)
    finally:
        provider.disconnect()
