//! LanceDB store backend.
//!
//! Index deletes and inserts run in the `lance` crate (`lance_native`),
//! without taking the GIL. The vector index, optimize, and file-state reads
//! still go through `chunkhound.providers.database.lance_store`. Search and
//! research open the same tables with installed `lancedb`.

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

/// True when the batch removes paths or replaces an existing file id.
fn has_deletes(batch: &DbWriterBatch) -> bool {
    !batch.delete_paths.is_empty()
        || batch
            .files
            .iter()
            .any(|file| file.existing_file_id.is_some())
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
        if !has_deletes(batch) {
            return Ok(());
        }
        crate::db::lance_native::apply_deletes(&self.db_path, batch).map_err(DbError::Other)
    }

    fn write_batch(&mut self, batch: &DbWriterBatch) -> Result<BatchResult, DbError> {
        crate::db::lance_native::write_index_batch(&self.db_path, batch).map_err(DbError::Other)
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

#[cfg(test)]
mod tests {
    use super::has_deletes;
    use crate::types::{ChunkRecord, DbWriterBatch, FileRecord};

    fn file_with_vector(existing_file_id: Option<i64>, code: &str) -> FileRecord {
        FileRecord {
            existing_file_id,
            path: "src/a.py".into(),
            mtime: Some(1.0),
            size_bytes: Some(4),
            content_hash: Some("hash".into()),
            language: Some("python".into()),
            skip_reason: None,
            chunks: vec![ChunkRecord {
                chunk_type: "function".into(),
                symbol: Some("f".into()),
                code: code.into(),
                start_line: Some(1),
                end_line: Some(2),
                start_byte: None,
                end_byte: None,
                language: Some("python".into()),
                metadata: None,
                embedding: Some(vec![0.125; 4]),
                provider: Some("fake".into()),
                model: Some("fake-embeddings".into()),
            }],
        }
    }

    #[test]
    fn has_deletes_sees_paths_and_existing_ids() {
        let with_path = DbWriterBatch {
            files: vec![file_with_vector(None, "new-chunk-text")],
            delete_paths: vec!["gone.py".into()],
        };
        assert!(has_deletes(&with_path));
        let with_id = DbWriterBatch {
            files: vec![file_with_vector(Some(7), "secret-chunk-text")],
            delete_paths: vec![],
        };
        assert!(has_deletes(&with_id));
    }

    #[test]
    fn has_deletes_skips_a_batch_that_deletes_nothing() {
        let batch = DbWriterBatch {
            files: vec![file_with_vector(None, "new-chunk-text")],
            delete_paths: vec![],
        };
        assert!(!has_deletes(&batch));
    }
}
