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
    ensure_chunk_byte_columns,
    get_chunks_schema,
    get_files_schema,
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


def _add_rows(db: Any, name: str, rows: list[dict[str, Any]], schema: Any) -> Any:
    import pyarrow as pa

    table = _table(db, name)
    if table is None:
        # Schema-only create. Passing an all-null embedding column through
        # create_table makes lancedb infer a list size and raise.
        db.create_table(name, schema=schema)
        table = db.open_table(name)
    elif name == "chunks":
        ensure_chunk_byte_columns(table)
    if rows:
        table.add(pa.Table.from_pylist(rows, schema=schema))
    return table


def apply_deletes(directory: str, payload: str) -> str:
    """Delete ``delete_paths`` and any chunks for files that will be replaced."""
    batch = json.loads(payload)
    db = _connect(directory)
    files = _table(db, "files")
    chunks = _table(db, "chunks")
    removed = 0

    def ids_for(path: str) -> list[int]:
        if files is None:
            return []
        rows = files.to_lance().to_table(columns=["id", "path"]).to_pylist()
        return [int(row["id"]) for row in rows if row["path"] == path]

    for path in batch.get("delete_paths") or []:
        for file_id in ids_for(str(path)):
            _delete_where(chunks, f"file_id = {file_id}")
            _delete_where(files, f"id = {file_id}")
            removed += 1
    for file in batch.get("files") or []:
        existing = file.get("existing_file_id")
        if existing is None:
            continue
        file_id = int(existing)
        _delete_where(chunks, f"file_id = {file_id}")
        _delete_where(files, f"id = {file_id}")
        removed += 1
    return json.dumps({"removed": removed})


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _file_row(file: dict[str, Any], file_id: int) -> dict[str, Any]:
    return {
        "id": file_id,
        "path": file.get("path") or "",
        "size": int(file.get("size_bytes") or 0),
        "modified_time": float(file.get("mtime") or 0.0),
        "content_hash": file.get("content_hash") or "",
        "indexed_time": time.time(),
        "language": file.get("language") or "",
        "encoding": "utf-8",
        "line_count": 0,
        "skip_reason": file.get("skip_reason"),
    }


def write_batch(directory: str, payload: str) -> str:
    """Apply one ``DbWriterBatch`` JSON document. Returns result JSON."""
    batch = json.loads(payload)
    # Seed before deletes. Otherwise a deleted max id becomes the next id.
    _seed_file_id_counter(directory, _table(_connect(directory), "files"))
    apply_deletes(directory, payload)
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
