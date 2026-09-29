"""Paged missing-embedding reads and fragment optimize on LanceDB."""

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("lancedb")


def _provider(tmp_path: Path, *, threshold: int, index_type: str | None):
    from chunkhound.core.config.database_config import DatabaseConfig
    from chunkhound.core.types.common import Language
    from chunkhound.embeddings import EmbeddingManager
    from chunkhound.parsers.parser_factory import create_parser_for_language
    from chunkhound.providers.database.lancedb_provider import LanceDBProvider
    from chunkhound.services.indexing_coordinator import IndexingCoordinator
    from tests.fixtures.fake_providers import ConstantEmbeddingProvider

    root = tmp_path / "repo"
    root.mkdir()
    embedder = ConstantEmbeddingProvider(dims=8)
    manager = EmbeddingManager()
    manager.register_provider(embedder, set_default=True)
    config = DatabaseConfig(
        path=tmp_path / "db",
        provider="lancedb",
        lancedb_index_type=index_type,
        lancedb_optimize_fragment_threshold=threshold,
    )
    provider = LanceDBProvider(
        str(config.get_db_path()),
        base_directory=root,
        embedding_manager=manager,
        config=config,
    )
    provider.connect()
    parser = create_parser_for_language(Language.PYTHON)
    coordinator = IndexingCoordinator(
        provider, root, embedder, {Language.PYTHON: parser}
    )
    return root, provider, coordinator, embedder


def test_missing_embedding_walk_is_paged(tmp_path, monkeypatch):
    monkeypatch.setenv("CHUNKHOUND_USE_RUST", "1")
    from chunkhound.core.config.database_config import DatabaseConfig
    from chunkhound.core.types.common import Language
    from chunkhound.embeddings import EmbeddingManager
    from chunkhound.parsers.parser_factory import create_parser_for_language
    from chunkhound.providers.database.lancedb_provider import LanceDBProvider
    from chunkhound.services.embedding_service import EmbeddingService
    from chunkhound.services.indexing_coordinator import IndexingCoordinator
    from tests.fixtures.fake_providers import ConstantEmbeddingProvider

    bare = tmp_path / "bare"
    bare.mkdir()
    for name in ("one.py", "two.py", "three.py"):
        (bare / name).write_text(
            f"def {name[:-3]}():\n    return 1\n", encoding="utf-8"
        )
    embedder = ConstantEmbeddingProvider(dims=8)
    manager = EmbeddingManager()
    manager.register_provider(embedder, set_default=True)
    config = DatabaseConfig(path=tmp_path / "bare-db", provider="lancedb")
    bare_provider = LanceDBProvider(
        str(config.get_db_path()),
        base_directory=bare,
        embedding_manager=manager,
        config=config,
    )
    bare_provider.connect()
    parser = create_parser_for_language(Language.PYTHON)
    bare_coordinator = IndexingCoordinator(
        bare_provider, bare, None, {Language.PYTHON: parser}
    )
    try:
        indexed = asyncio.run(
            bare_coordinator.process_directory(bare, patterns=["**/*.py"])
        )
        assert indexed["pipeline"] == "rust"
        table = bare_provider._chunks_table

        def _boom(*_args, **_kwargs):
            raise AssertionError("full table load")

        table.to_pandas = _boom
        table.head = _boom
        pages = bare_provider.list_chunk_ids_without_embeddings(
            "fake", "fake-embeddings", page_size=1
        )
        assert bare_provider._missing_embedding_pages > 1
        assert len(pages) > 1
        bare_provider.get_all_chunks_with_metadata = _boom
        service = EmbeddingService(bare_provider, embedder)
        ids = service._get_chunk_ids_without_embeddings("fake", "fake-embeddings")
        assert len(ids) == len(pages)
        generated = asyncio.run(service.generate_missing_embeddings())
        assert generated.get("generated", 0) > 0, generated
    finally:
        bare_provider.disconnect()


def test_optimize_past_fragment_threshold_still_answers_semantic_search(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CHUNKHOUND_USE_RUST", "1")
    root, provider, coordinator, embedder = _provider(
        tmp_path, threshold=1, index_type="ivf_hnsw_sq"
    )
    try:
        for name in ("alpha.py", "beta.py"):
            (root / name).write_text(
                f"def {name[:-3]}_fn():\n    return '{name}'\n", encoding="utf-8"
            )
            result = asyncio.run(
                coordinator.process_directory(root, patterns=["**/*.py"])
            )
            assert result["status"] == "success", result
        assert provider.index_type == "ivf_hnsw_sq"
        indexes = [
            idx
            for idx in provider._chunks_table.list_indices()
            if "embedding" in list(idx.columns)
            and str(idx.index_type).replace("_", "").lower() == "ivfhnswsq"
        ]
        assert indexes, list(provider._chunks_table.list_indices())
        stats = provider._chunks_table.index_stats(indexes[0].name)
        assert stats is not None and stats.num_indexed_rows > 0, stats
        assert str(stats.index_type).replace("_", "").lower() == "ivfhnswsq"
        vector = asyncio.run(embedder.embed_single("alpha_fn"))
        plan = (
            provider._chunks_table.search(vector, vector_column_name="embedding")
            .where(
                f"provider = '{embedder.name}' AND model = '{embedder.model}' "
                "AND embedding IS NOT NULL"
            )
            .limit(5)
            .explain_plan(True)
        )
        assert "ANNIvfPartition" in plan or "ANNSubIndex" in plan, plan
        rows, _ = provider.search_semantic(
            vector, embedder.name, embedder.model, page_size=5
        )
        assert any(row.get("file_path") == "alpha.py" for row in rows), rows
    finally:
        provider.disconnect()


def test_default_settings_build_an_ann_index_when_threshold_is_always(
    tmp_path, monkeypatch
):
    """Unset index type is auto, and threshold 0 always optimizes."""
    monkeypatch.setenv("CHUNKHOUND_USE_RUST", "1")
    root, provider, coordinator, embedder = _provider(
        tmp_path, threshold=0, index_type=None
    )
    try:
        for i in range(256):
            (root / f"f{i}.py").write_text(
                f"def f{i}():\n    return {i}\n", encoding="utf-8"
            )
        result = asyncio.run(coordinator.process_directory(root, patterns=["**/*.py"]))
        assert result["status"] == "success", result
        assert provider.index_type is None
        scalar = {"btree", "bitmap", "labellist", "fts", "inverted"}
        indexes = [
            idx
            for idx in provider._chunks_table.list_indices()
            if "embedding" in list(idx.columns)
            and str(idx.index_type).replace("_", "").lower() not in scalar
        ]
        assert indexes, list(provider._chunks_table.list_indices())
        stats = provider._chunks_table.index_stats(indexes[0].name)
        assert stats is not None and stats.num_indexed_rows > 0, stats
        vector = asyncio.run(embedder.embed_single("f0"))
        plan = (
            provider._chunks_table.search(vector, vector_column_name="embedding")
            .limit(5)
            .explain_plan(True)
        )
        assert "ANNIvfPartition" in plan or "ANNSubIndex" in plan, plan
        rows, _ = provider.search_semantic(
            vector, embedder.name, embedder.model, page_size=5
        )
        assert rows
    finally:
        provider.disconnect()


def test_broad_regex_does_not_load_embeddings(tmp_path, monkeypatch):
    monkeypatch.setenv("CHUNKHOUND_USE_RUST", "1")
    root, provider, coordinator, _embedder = _provider(
        tmp_path, threshold=100, index_type="ivf_hnsw_sq"
    )
    try:
        for name in ("one.py", "two.py", "three.py"):
            (root / name).write_text(
                f"def {name[:-3]}_fn():\n    return '{name}'\n", encoding="utf-8"
            )
        indexed = asyncio.run(coordinator.process_directory(root, patterns=["**/*.py"]))
        assert indexed["status"] == "success", indexed
        table = provider._chunks_table

        def _boom(*_args, **_kwargs):
            raise AssertionError("full table load")

        table.to_pandas = _boom
        table.head = _boom
        real_search = table.search

        def _search(*args, **kwargs):
            query = real_search(*args, **kwargs)
            real_to_list = query.to_list

            def _to_list():
                columns = getattr(query, "_columns", None)
                if not columns or "embedding" in list(columns):
                    raise AssertionError(f"unprojected to_list {columns}")
                return real_to_list()

            query.to_list = _to_list
            return query

        table.search = _search
        rows, _info = provider.search_regex("return")
        assert len(rows) > 1
        assert all(row.get("content") for row in rows)
        assert all("embedding" not in row for row in rows)
    finally:
        provider.disconnect()
