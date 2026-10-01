"""Indexing stores the vectors the embedder returned.

The provider may declare a different width. DuckDB and LanceDB both keep the
returned vectors.
"""

import asyncio

import pytest

DECLARED_DIMS = 4
RETURNED_DIMS = 8
RETURNED_VECTOR = [0.25] * RETURNED_DIMS


class _MismatchedEmbedder:
    """Declares one width and returns vectors of another."""

    name = "fake"
    model = "declared-width"
    dims = DECLARED_DIMS

    async def embed(self, texts):
        return [list(RETURNED_VECTOR) for _ in texts]


def _file_id(record):
    return record["id"] if isinstance(record, dict) else record.id


def _chunk_id(chunk):
    return chunk["id"] if isinstance(chunk, dict) else chunk.id


def _provider(tmp_path, database: str, embedder):
    from chunkhound.core.config.database_config import DatabaseConfig
    from chunkhound.embeddings import EmbeddingManager

    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("def hello():\n    return 1\n")
    manager = EmbeddingManager()
    manager.register_provider(embedder, set_default=True)
    config = DatabaseConfig(path=tmp_path / "db", provider=database)
    db_path = config.get_db_path()
    if database == "lancedb":
        pytest.importorskip("lancedb")
        from chunkhound.providers.database.lancedb_provider import LanceDBProvider

        provider = LanceDBProvider(
            str(db_path),
            base_directory=root,
            embedding_manager=manager,
            config=config,
        )
    else:
        from chunkhound.providers.database.duckdb_provider import DuckDBProvider

        provider = DuckDBProvider(
            db_path,
            base_directory=root,
            embedding_manager=manager,
            config=config,
        )
    provider.connect()
    return provider, root


@pytest.mark.parametrize("database", ["duckdb", "lancedb"])
def test_index_stores_returned_vectors_when_declared_width_differs(
    tmp_path, monkeypatch, database
):
    """A provider that declares 4 dimensions and returns 8 still stores 8."""
    from chunkhound.core.types.common import Language
    from chunkhound.parsers.parser_factory import create_parser_for_language
    from chunkhound.services.indexing_coordinator import IndexingCoordinator

    monkeypatch.setenv("CHUNKHOUND_USE_RUST", "1")
    monkeypatch.setenv("CHUNKHOUND_NO_PROMPTS", "1")
    embedder = _MismatchedEmbedder()
    provider, root = _provider(tmp_path, database, embedder)
    coordinator = IndexingCoordinator(
        provider,
        root,
        embedder,
        {Language.PYTHON: create_parser_for_language(Language.PYTHON)},
    )
    try:
        result = asyncio.run(coordinator.process_directory(root, patterns=["**/*"]))
        assert result["status"] == "success", result
        assert result.get("pipeline") == "rust"
        assert not result.get("errors"), result
        file_record = provider.get_file_by_path("main.py")
        assert file_record is not None
        chunks = provider.get_chunks_by_file_id(_file_id(file_record), as_model=True)
        assert chunks
        for chunk in chunks:
            stored = provider.get_embedding_by_chunk_id(
                _chunk_id(chunk), embedder.name, embedder.model
            )
            assert stored is not None
            assert [float(value) for value in stored.vector] == RETURNED_VECTOR
            assert stored.dims == RETURNED_DIMS
    finally:
        provider.disconnect()
