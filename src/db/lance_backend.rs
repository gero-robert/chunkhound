//! LanceDB store backend. Writes go through the installed `lancedb` package
//! (`chunkhound.providers.database.lance_store`) because the matching native
//! `lance` crate does not build without `protoc`.

use pyo3::prelude::*;
use serde::Deserialize;

use crate::db::{DbBackend, DbConfig};
use crate::error::DbError;
use crate::types::{BatchResult, DbFileEntry, DbWriterBatch};

pub(crate) struct LanceCallbackBackend {
    db_path: String,
    fragment_threshold: u32,
    index_type: String,
    /// Set by the pre-write drop. `close` rebuilds while this is set so a
    /// failed run does not leave the ANN index missing. Cleared once a build
    /// has been attempted, so `close` does not train it a second time.
    restore_index: bool,
}

impl LanceCallbackBackend {
    pub(crate) fn new(cfg: DbConfig) -> Self {
        Self {
            db_path: cfg.db_path,
            fragment_threshold: cfg.lance_optimize_fragment_threshold,
            index_type: cfg.lance_index_type,
            restore_index: false,
        }
    }

    fn call(&self, method: &str, payload: Option<&str>) -> Result<String, DbError> {
        Python::with_gil(|py| {
            let module = py
                .import_bound("chunkhound.providers.database.lance_store")
                .map_err(|err| DbError::Other(err.to_string()))?;
            let result = match payload {
                Some(body) => module.call_method1(method, (self.db_path.as_str(), body)),
                None => module.call_method1(method, (self.db_path.as_str(),)),
            }
            .map_err(|err| DbError::Other(err.to_string()))?;
            result
                .extract()
                .map_err(|err| DbError::Other(err.to_string()))
        })
    }
}

#[derive(Deserialize)]
struct FileStateRow {
    id: i64,
    path: String,
    mtime: Option<f64>,
    size_bytes: Option<i64>,
    content_hash: Option<String>,
}

#[derive(Deserialize)]
struct WriteOutcome {
    file_ids: Vec<i64>,
    chunks_written: u64,
    embeddings_written: u64,
}

impl DbBackend for LanceCallbackBackend {
    fn open(&mut self) -> Result<(), DbError> {
        Ok(())
    }

    fn close(&mut self) -> Result<(), DbError> {
        if self.restore_index {
            self.ensure_all_hnsw_indexes()?;
        }
        Ok(())
    }

    fn prepare_write(&mut self, batch: &DbWriterBatch) -> Result<(), DbError> {
        // Orphan removal is applied only through prepare_write. A data batch
        // also passes through here before write_batch, which deletes again.
        let payload = serde_json::to_string(batch)?;
        self.call("apply_deletes", Some(&payload))?;
        Ok(())
    }

    fn write_batch(&mut self, batch: &DbWriterBatch) -> Result<BatchResult, DbError> {
        let payload = serde_json::to_string(batch)?;
        let raw = self.call("write_batch", Some(&payload))?;
        let outcome: WriteOutcome = serde_json::from_str(&raw)?;
        Ok(BatchResult {
            file_ids: outcome.file_ids,
            chunks_written: outcome.chunks_written,
            embeddings_written: outcome.embeddings_written,
        })
    }

    fn drop_all_hnsw_indexes(&mut self) -> Result<(), DbError> {
        // Before the drop, so a mid-drop failure still rebuilds from close().
        // Adds must not maintain the ANN index.
        self.restore_index = true;
        self.call("drop_vector_indexes", None)?;
        Ok(())
    }

    fn ensure_all_hnsw_indexes(&mut self) -> Result<(), DbError> {
        // Clear first so a failed build is not trained again from close().
        self.restore_index = false;
        let payload = serde_json::json!({ "index_type": self.index_type }).to_string();
        self.call("ensure_vector_index", Some(&payload))?;
        Ok(())
    }

    fn needs_compaction(&self) -> Result<bool, DbError> {
        // DatabaseConfig documents 0 as "always optimize". should_optimize
        // returns true in that case because the fragment count is never < 0.
        if self.fragment_threshold == 0 {
            return Ok(true);
        }
        let raw = self.call("chunk_fragment_count", None)?;
        let count: i64 = raw.trim().parse().map_err(|err| {
            DbError::Other(format!(
                "Lance fragment count '{raw}' is not an integer: {err}"
            ))
        })?;
        Ok(count >= i64::from(self.fragment_threshold))
    }

    fn run_compaction(&mut self) -> Result<(), DbError> {
        let payload = serde_json::json!({ "index_type": self.index_type }).to_string();
        self.call("optimize_database", Some(&payload))?;
        // optimize builds the index. Failure leaves restore_index set so
        // close() builds it; success must not build it again.
        self.restore_index = false;
        Ok(())
    }

    fn read_file_states(&self) -> Result<Vec<DbFileEntry>, DbError> {
        let raw = self.call("read_file_states", None)?;
        let rows: Vec<FileStateRow> = serde_json::from_str(&raw)?;
        Ok(rows
            .into_iter()
            .map(|row| DbFileEntry {
                id: row.id,
                path: row.path,
                mtime: row.mtime,
                size_bytes: row.size_bytes,
                content_hash: row.content_hash,
            })
            .collect())
    }
}
