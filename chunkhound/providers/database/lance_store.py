"""Lance writes invoked from the Rust store thread.

The native ``lance`` crate that matches installed ``pylance`` 0.38.1 does not
build here (its ``prost-build`` step requires ``protoc``). The store thread
calls this module, and search/research open the same tables with ``lancedb``.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from loguru import logger

from chunkhound.providers.database.lancedb_provider import (
    _sql_literal,
    ensure_chunk_byte_columns,
    ensure_file_name_columns,
    file_name_and_extension,
    get_chunks_schema,
    get_files_schema,
    stored_byte,
)
from chunkhound.utils.chunk_hashing import generate_chunk_id


def write_format_probe(directory: str) -> None:
    """Write one ``files`` row the installed ``lancedb`` package can open."""
    import lancedb
    import pyarrow as pa

    db = lancedb.connect(directory)
    table = pa.table(
        {
            "id": pa.array([1], type=pa.int64()),
            "path": pa.array(["main.py"], type=pa.string()),
        }
    )
    db.create_table("files", table, mode="overwrite")


def _connect(directory: str) -> Any:
    import lancedb

    return lancedb.connect(directory)


def _table(db: Any, name: str) -> Any | None:
    if name not in set(db.table_names()):
        return None
    return db.open_table(name)


def read_file_states(directory: str) -> str:
    """JSON list of ``{id, path, mtime, size_bytes, content_hash}``."""
    db = _connect(directory)
    table = _table(db, "files")
    if table is None:
        return "[]"
    arrow = table.to_lance().to_table(
        columns=["id", "path", "modified_time", "size", "content_hash"]
    )
    rows = []
    for row in arrow.to_pylist():
        rows.append(
            {
                "id": int(row["id"]),
                "path": row["path"] or "",
                "mtime": row["modified_time"],
                "size_bytes": row["size"],
                "content_hash": row["content_hash"],
            }
        )
    return json.dumps(rows)


# Next file id per database directory. Seeded once from MAX(id) and only
# increased, including across deletes, so a removed id is not reused.
_file_id_counters: dict[str, int] = {}


def _db_key(directory: str) -> str:
    return os.path.normcase(os.path.abspath(directory))


def _next_file_id(files: Any | None) -> int:
    if files is None:
        return 1
    arrow = files.to_lance().to_table(columns=["id"])
    if arrow.num_rows == 0:
        return 1
    return int(max(arrow.column("id").to_pylist())) + 1


def _seed_file_id_counter(directory: str, files: Any | None) -> None:
    key = _db_key(directory)
    if key in _file_id_counters:
        return
    _file_id_counters[key] = _next_file_id(files)


def _allocate_file_id(directory: str) -> int:
    key = _db_key(directory)
    file_id = _file_id_counters[key]
    _file_id_counters[key] = file_id + 1
    return file_id


def _observe_file_id(directory: str, file_id: int) -> None:
    key = _db_key(directory)
    nxt = file_id + 1
    if nxt > _file_id_counters.get(key, 1):
        _file_id_counters[key] = nxt


def _delete_where(table: Any | None, predicate: str) -> None:
    if table is None:
        return
    table.delete(predicate)


# Split path lists so each Lance IN predicate stays bounded.
_DELETE_PATH_BATCH = 500


def _file_ids_for_paths(files: Any, paths: list[str]) -> list[int]:
    """Ids whose path is in ``paths``. Lance applies the filter."""
    dataset = files.to_lance()
    found: list[int] = []
    for offset in range(0, len(paths), _DELETE_PATH_BATCH):
        batch = paths[offset : offset + _DELETE_PATH_BATCH]
        literals = ", ".join(f"'{_sql_literal(path)}'" for path in batch)
        table = dataset.to_table(columns=["id"], filter=f"path IN ({literals})")
        found.extend(int(file_id) for file_id in table.column("id").to_pylist())
    return found


def _delete_file_ids(
    files: Any | None, chunks: Any | None, file_ids: list[int]
) -> int:
    """Delete chunks and file rows for ``file_ids``. Returns how many files."""
    removed = 0
    for offset in range(0, len(file_ids), _DELETE_PATH_BATCH):
        batch = file_ids[offset : offset + _DELETE_PATH_BATCH]
        id_list = ", ".join(str(int(file_id)) for file_id in batch)
        _delete_where(chunks, f"file_id IN ({id_list})")
        _delete_where(files, f"id IN ({id_list})")
        removed += len(batch)
    return removed


def _embedding_width(schema: Any) -> int | None:
    import pyarrow as pa

    if schema is None or "embedding" not in schema.names:
        return None
    field_type = schema.field("embedding").type
    if pa.types.is_fixed_size_list(field_type):
        return int(field_type.list_size)
    return None


def _vector_width(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return len(value)
    except TypeError:
        return None


def _restored_chunk(row: dict[str, Any], wanted: int) -> dict[str, Any]:
    """Keep a chunk when the embedding column changes width.

    A vector of the new width is kept. Any other vector is cleared, matching
    the Python provider's one-time schema migration.
    """
    embedding = row.get("embedding")
    if _vector_width(embedding) == wanted:
        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()
        provider = row.get("provider") or ""
        model = row.get("model") or ""
    else:
        embedding = None
        provider = ""
        model = ""
    return {
        "id": int(row["id"]),
        "file_id": int(row["file_id"]),
        "content": row.get("content") or "",
        "start_line": int(row.get("start_line") or 0),
        "end_line": int(row.get("end_line") or 0),
        "chunk_type": row.get("chunk_type") or "",
        "language": row.get("language") or "",
        "name": row.get("name") or "",
        "embedding": embedding,
        "provider": provider,
        "model": model,
        "created_time": float(row.get("created_time") or 0.0),
        "metadata": row.get("metadata"),
        "start_byte": stored_byte(row.get("start_byte")),
        "end_byte": stored_byte(row.get("end_byte")),
    }


def _align_chunks_table(db: Any, schema: Any) -> Any:
    """Open the chunks table, recreating it when the vector width differs.

    Connect creates the table from ``provider.dims``. An unknown Voyage model
    reports 1024 until a response arrives, while the Rust embedder stores the
    native width. Adding those vectors to the fallback column fails.
    """
    import pyarrow as pa

    table = _table(db, "chunks")
    if table is None:
        # Schema-only create. Passing an all-null embedding column through
        # create_table makes lancedb infer a list size and raise.
        db.create_table("chunks", schema=schema)
        return db.open_table("chunks")
    ensure_chunk_byte_columns(table)
    wanted = _embedding_width(schema)
    current = _embedding_width(table.schema)
    if wanted is None or current == wanted:
        return table
    rows = table.to_arrow().to_pylist() if table.count_rows() else []
    logger.info(
        "Recreating Lance chunks table at embedding width {} "
        "(was {}), keeping {} chunks",
        wanted,
        current,
        len(rows),
    )
    db.drop_table("chunks")
    db.create_table("chunks", schema=schema)
    if rows:
        restored = [_restored_chunk(row, wanted) for row in rows]
        db.open_table("chunks").add(pa.Table.from_pylist(restored, schema=schema))
    return db.open_table("chunks")


def _add_rows(db: Any, name: str, rows: list[dict[str, Any]], schema: Any) -> Any:
    import pyarrow as pa

    if name == "chunks":
        table = _align_chunks_table(db, schema)
    else:
        table = _table(db, name)
        if table is None:
            db.create_table(name, schema=schema)
            table = db.open_table(name)
        elif name == "files":
            ensure_file_name_columns(table)
    if rows:
        table.add(pa.Table.from_pylist(rows, schema=schema))
    return table


def apply_deletes(directory: str, payload: str) -> str:
    """Delete ``delete_paths`` and any chunks for files that will be replaced."""
    return _apply_deletes(directory, json.loads(payload))


def _apply_deletes(directory: str, batch: dict[str, Any]) -> str:
    """Use the parsed batch so a write does not decode its embeddings twice."""
    paths = [str(path) for path in batch.get("delete_paths") or []]
    file_ids = [
        int(file["existing_file_id"])
        for file in batch.get("files") or []
        if file.get("existing_file_id") is not None
    ]
    if not paths and not file_ids:
        return json.dumps({"removed": 0})

    db = _connect(directory)
    files = _table(db, "files")
    chunks = _table(db, "chunks")
    removed = 0

    if paths and files is not None:
        removed += _delete_file_ids(files, chunks, _file_ids_for_paths(files, paths))
    for file_id in file_ids:
        _delete_where(chunks, f"file_id = {file_id}")
        _delete_where(files, f"id = {file_id}")
        removed += 1
    return json.dumps({"removed": removed})


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _file_row(file: dict[str, Any], file_id: int) -> dict[str, Any]:
    name, extension = file_name_and_extension(file.get("path") or "")
    return {
        "id": file_id,
        "path": file.get("path") or "",
        "size": int(file.get("size_bytes") or 0),
        "modified_time": float(file.get("mtime") or 0.0),
        "content_hash": file.get("content_hash") or "",
        "indexed_time": time.time(),
        "language": file.get("language") or "",
        "skip_reason": file.get("skip_reason"),
        "name": name,
        "extension": extension,
    }


def write_batch(directory: str, payload: str) -> str:
    """Apply one ``DbWriterBatch`` JSON document. Returns result JSON."""
    batch = json.loads(payload)
    # Seed before deletes. Otherwise a deleted max id becomes the next id.
    _seed_file_id_counter(directory, _table(_connect(directory), "files"))
    _apply_deletes(directory, batch)
    db = _connect(directory)

    file_ids: list[int] = []
    file_rows: list[dict[str, Any]] = []
    chunk_rows: list[dict[str, Any]] = []
    embeddings_written = 0
    dims: int | None = None
    files = batch.get("files") or []
    for file in files:
        existing = file.get("existing_file_id")
        if existing is not None:
            _observe_file_id(directory, int(existing))
    for file in files:
        for chunk in file.get("chunks") or []:
            embedding = chunk.get("embedding")
            if embedding:
                dims = len(embedding)
                break
        if dims is not None:
            break

    for file in files:
        existing = file.get("existing_file_id")
        if existing is not None:
            file_id = int(existing)
        else:
            file_id = _allocate_file_id(directory)
        file_ids.append(file_id)
        file_rows.append(_file_row(file, file_id))
        for chunk in file.get("chunks") or []:
            start_line = chunk.get("start_line")
            end_line = chunk.get("end_line")
            code = chunk.get("code") or ""
            chunk_type = chunk.get("chunk_type") or ""
            embedding = chunk.get("embedding") or None
            if embedding:
                embeddings_written += 1
            chunk_rows.append(
                {
                    "id": generate_chunk_id(
                        file_id,
                        code,
                        concept=chunk_type or None,
                        start_line=start_line,
                        end_line=end_line,
                    ),
                    "file_id": file_id,
                    "content": code,
                    "start_line": int(start_line or 0),
                    "end_line": int(end_line or 0),
                    "chunk_type": chunk_type,
                    "language": chunk.get("language") or "",
                    "name": chunk.get("symbol") or "",
                    "embedding": embedding,
                    "provider": chunk.get("provider") or "",
                    "model": chunk.get("model") or "",
                    "created_time": time.time(),
                    "metadata": chunk.get("metadata"),
                    "start_byte": _optional_int(chunk.get("start_byte")),
                    "end_byte": _optional_int(chunk.get("end_byte")),
                }
            )
    if file_rows:
        _add_rows(db, "files", file_rows, get_files_schema())
    if chunk_rows:
        _add_rows(db, "chunks", chunk_rows, get_chunks_schema(dims))
    return json.dumps(
        {
            "file_ids": file_ids,
            "chunks_written": len(chunk_rows),
            "embeddings_written": embeddings_written,
        }
    )


def chunk_fragment_count(db_path: str) -> str:
    """Chunk-table fragment count, as text for the Rust store thread."""
    db = _connect(db_path)
    if "chunks" not in set(db.table_names()):
        return "0"
    from chunkhound.providers.database.lancedb_provider import LanceDBProvider

    return str(LanceDBProvider._fragment_count(db.open_table("chunks")))


def _index_type_name(payload: str) -> str:
    spec = json.loads(payload or "{}")
    index_type = spec.get("index_type")
    if index_type in (None, "", "auto"):
        return "auto"
    return str(index_type)


def drop_vector_indexes(db_path: str) -> str:
    """Drop embedding ANN indexes so later adds do not maintain them."""
    db = _connect(db_path)
    if "chunks" not in set(db.table_names()):
        return "ok"
    table = db.open_table("chunks")
    scalar = {"btree", "bitmap", "labellist", "fts", "inverted"}
    for idx in table.list_indices():
        columns = list(getattr(idx, "columns", []) or [])
        if "embedding" not in columns:
            continue
        token = str(getattr(idx, "index_type", "")).replace("_", "").lower()
        if token in scalar:
            continue
        name = getattr(idx, "name", None)
        if name:
            table.drop_index(str(name))
    return "ok"


def ensure_vector_index(db_path: str, payload: str) -> str:
    """Build the configured ANN index. Does not compact."""
    db = _connect(db_path)
    if "chunks" not in set(db.table_names()):
        return "ok"
    _ensure_vector_index(db.open_table("chunks"), _index_type_name(payload))
    return "ok"


def optimize_database(db_path: str, payload: str) -> str:
    """Compact fragments, then build the configured vector index.

    Runs on a direct ``lancedb`` connection, not the provider executor.
    """
    from datetime import timedelta

    index_type = _index_type_name(payload)
    db = _connect(db_path)
    names = set(db.table_names())
    for name in ("chunks", "files"):
        if name not in names:
            continue
        db.open_table(name).optimize(
            cleanup_older_than=timedelta(minutes=1), delete_unverified=True
        )
    if "chunks" in names:
        _ensure_vector_index(db.open_table("chunks"), str(index_type))
    return "ok"


def _ensure_vector_index(table: Any, index_type: str) -> None:
    """Create the configured ANN index when the embedding column has vectors.

    ``auto`` (and a missing type) uses LanceDB's default vector index, the
    same branch as ``LanceDBProvider`` when ``lancedb_index_type`` is unset.
    """
    named = {
        "ivf_hnsw_sq": "IVF_HNSW_SQ",
        "ivf_rq": "IVF_RQ",
    }
    auto = index_type in ("", "auto")
    wanted = "" if auto else named.get(index_type, "").replace("_", "").lower()
    scalar = {"btree", "bitmap", "labellist", "fts", "inverted"}
    for idx in table.list_indices():
        columns = list(getattr(idx, "columns", []) or [])
        if "embedding" not in columns:
            continue
        token = str(getattr(idx, "index_type", "")).replace("_", "").lower()
        if token in scalar:
            continue
        if auto or token == wanted:
            return
    if table.count_rows("embedding IS NOT NULL") < 1:
        return
    lance_type = None if auto else named.get(index_type)
    try:
        if lance_type is None:
            # Same call as LanceDBProvider when lancedb_index_type is unset.
            # IVF_PQ training needs enough rows; a short table keeps the data
            # and leaves the index for a later optimize.
            table.create_index(vector_column_name="embedding", metric="cosine")
        else:
            table.create_index(
                vector_column_name="embedding",
                index_type=lance_type,
                metric="cosine",
            )
    except Exception as exc:
        # A short auto/IVF_PQ table cannot train. The pre-write drop already
        # removed any previous ANN index, so this is visible, but it must not
        # fail the run. Every other create error has to reach the store thread.
        if "not enough rows" in str(exc).lower():
            logger.warning("Lance vector index was not built: {}", exc)
            return
        raise
