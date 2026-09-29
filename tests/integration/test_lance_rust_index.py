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
