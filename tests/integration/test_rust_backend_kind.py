"""Backend kind handed to the Rust pipeline.

DuckDB still receives the directory that contains ``chunks.db``. LanceDB
receives the ``.lancedb`` directory. Lance does not declare
``supports_rust_pipeline``, so a Rust-flagged Lance index still writes
through Python and stays readable.
"""

import asyncio
from pathlib import Path


def test_lance_storage_target_is_the_lancedb_directory(lancedb_provider):
    from chunkhound.services.rust_pipeline_runner import rust_storage_target

    path, kind = rust_storage_target(lancedb_provider)

    assert kind == "lancedb"
    assert path == Path(lancedb_provider.db_path)
    assert path.suffix == ".lancedb"


def test_duckdb_storage_target_is_the_parent_of_chunks_db(tmp_path):
    from chunkhound.core.config.database_config import DatabaseConfig
    from chunkhound.providers.database.duckdb_provider import DuckDBProvider
    from chunkhound.services.rust_pipeline_runner import rust_storage_target

    config = DatabaseConfig(path=tmp_path / "db", provider="duckdb")
    db_file = config.get_db_path()
    provider = DuckDBProvider(db_file, base_directory=tmp_path)
    provider.connect()
    try:
        path, kind = rust_storage_target(provider)
    finally:
        provider.disconnect()

    assert kind == "duckdb"
    assert db_file.name == "chunks.db"
    assert path == db_file.parent


def test_duckdb_with_rust_flag_writes_chunks_db(tmp_path, monkeypatch):
    """DuckDB with the Rust flag writes chunks.db through the Rust pipeline."""
    from chunkhound.core.config.database_config import DatabaseConfig
    from chunkhound.core.types.common import Language
    from chunkhound.parsers.parser_factory import create_parser_for_language
    from chunkhound.providers.database.duckdb_provider import DuckDBProvider
    from chunkhound.services.indexing_coordinator import IndexingCoordinator

    monkeypatch.setenv("CHUNKHOUND_USE_RUST", "1")

    config = DatabaseConfig(path=tmp_path / "db", provider="duckdb")
    db_file = config.get_db_path()
    provider = DuckDBProvider(db_file, base_directory=tmp_path, config=config)
    provider.connect()
    parser = create_parser_for_language(Language.PYTHON)
    coordinator = IndexingCoordinator(
        provider, tmp_path, None, {Language.PYTHON: parser}
    )
    (tmp_path / "main.py").write_text("def hello():\n    return 1\n")

    try:
        result = asyncio.run(
            coordinator.process_directory(tmp_path, patterns=["**/*.py"])
        )
        rows = provider.execute_query("SELECT path FROM files")
    finally:
        provider.disconnect()

    assert result["status"] == "success", result
    assert result["pipeline"] == "rust"
    assert result["files_processed"] >= 1
    assert result["total_chunks"] >= 1
    assert db_file.is_file()
    assert rows, "Expected the indexed file to be recorded in chunks.db"
