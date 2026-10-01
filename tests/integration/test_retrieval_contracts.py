"""Retrieval contracts shared by DuckDB and LanceDB."""

import asyncio

import pytest

from chunkhound.core.models import Chunk, File
from chunkhound.core.types.common import ChunkType, Language


class _QueryEmbedder:
    """Returns one fixed vector so stored-vector scores are predictable."""

    name = "test"
    model = "test-model"
    dims = 2

    async def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def _provider(tmp_path, database: str):
    from chunkhound.core.config.database_config import DatabaseConfig

    root = tmp_path / "repo"
    root.mkdir()
    config = DatabaseConfig(path=tmp_path / "db", provider=database)
    db_path = config.get_db_path()
    if database == "lancedb":
        pytest.importorskip("lancedb")
        from chunkhound.providers.database.lancedb_provider import LanceDBProvider

        provider = LanceDBProvider(str(db_path), base_directory=root, config=config)
    else:
        from chunkhound.providers.database.duckdb_provider import DuckDBProvider

        provider = DuckDBProvider(db_path, base_directory=root, config=config)
    provider.connect()
    return provider


def _store_pair(provider):
    file_id = provider.insert_file(
        File(path="markers.py", mtime=1.0, language=Language.PYTHON, size_bytes=40)
    )
    chunk_ids = provider.insert_chunks_batch(
        [
            Chunk(
                file_id=file_id,
                code="alpha_marker = 1",
                start_line=1,
                end_line=1,
                chunk_type=ChunkType.FUNCTION,
                language=Language.PYTHON,
                symbol="alpha_marker",
                metadata={"constants": [{"name": "MAX_VALUE", "value": "100"}]},
            ),
            Chunk(
                file_id=file_id,
                code="beta_marker = 2",
                start_line=2,
                end_line=2,
                chunk_type=ChunkType.FUNCTION,
                language=Language.PYTHON,
                symbol="beta_marker",
            ),
        ]
    )
    provider.insert_embeddings_batch(
        [
            {
                "chunk_id": chunk_ids[0],
                "provider": "test",
                "model": "test-model",
                "dims": 2,
                "embedding": [1.0, 0.0],
            },
            {
                "chunk_id": chunk_ids[1],
                "provider": "test",
                "model": "test-model",
                "dims": 2,
                "embedding": [0.0, 1.0],
            },
        ]
    )
    return file_id, chunk_ids


@pytest.mark.parametrize("database", ["duckdb", "lancedb"])
def test_file_chunks_expose_metadata_dict(tmp_path, database):
    """Chunks loaded for a file expose metadata as a dict."""
    provider = _provider(tmp_path, database)
    try:
        file_id, chunk_ids = _store_pair(provider)
        rows = provider.get_chunks_by_file_id(file_id, as_model=False)
        assert rows
        found = False
        for row in rows:
            assert isinstance(row, dict)
            assert isinstance(row["metadata"], dict)
            constants = row["metadata"].get("constants") or []
            if any(item.get("name") == "MAX_VALUE" for item in constants):
                found = True
        assert found
        one = provider.get_chunk_by_id(chunk_ids[0], as_model=False)
        assert isinstance(one, dict)
        assert one["metadata"].get("constants")[0]["name"] == "MAX_VALUE"
    finally:
        provider.disconnect()


@pytest.mark.parametrize("database", ["duckdb", "lancedb"])
def test_regex_query_scores_matching_vectors(tmp_path, database):
    """A regex query scores hits by cosine similarity to stored vectors."""
    from chunkhound.services.search_service import SearchService

    provider = _provider(tmp_path, database)
    try:
        _store_pair(provider)
        service = SearchService(provider, _QueryEmbedder())
        results, _pagination = asyncio.run(
            service.search_regex_async("marker", page_size=10, query="alpha")
        )
        by_content = {row["content"]: row["similarity"] for row in results}
        assert by_content["alpha_marker = 1"] == pytest.approx(1.0)
        assert by_content["beta_marker = 2"] == pytest.approx(0.0, abs=1e-6)
    finally:
        provider.disconnect()


@pytest.mark.parametrize("database", ["duckdb", "lancedb"])
def test_single_hop_total_counts_stored_embeddings(tmp_path, database):
    """Single-hop reports how many embeddings exist, not the page size."""
    from chunkhound.services.search_service import SearchService

    provider = _provider(tmp_path, database)
    try:
        _store_pair(provider)
        service = SearchService(provider, _QueryEmbedder())
        results, pagination = asyncio.run(
            service.search_semantic("alpha", page_size=1, force_strategy="single_hop")
        )
        assert len(results) == 1
        assert pagination["total"] == 2
        assert pagination["has_more"] is True
    finally:
        provider.disconnect()
