//! LanceDB store backend.
//!
//! Index deletes, inserts, the vector index, optimize, and file-state reads
//! run in the `lance` crate (`lance_native`) without taking the GIL. Search
//! and research open the same tables with installed `lancedb`.

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
        crate::db::lance_native::drop_vector_indexes(&self.db_path).map_err(DbError::Other)
    }

    fn ensure_all_hnsw_indexes(&mut self) -> Result<(), DbError> {
        // Clear first so a failed build is not trained again from close().
        self.restore_index = false;
        crate::db::lance_native::ensure_vector_index(&self.db_path, &self.index_type)
            .map_err(DbError::Other)
    }

    fn needs_compaction(&self) -> Result<bool, DbError> {
        // DatabaseConfig documents 0 as "always optimize". should_optimize
        // returns true in that case because the fragment count is never < 0.
        if self.fragment_threshold == 0 {
            return Ok(true);
        }
        let count =
            crate::db::lance_native::chunk_fragment_count(&self.db_path).map_err(DbError::Other)?;
        Ok(count >= i64::from(self.fragment_threshold))
    }

    fn run_compaction(&mut self) -> Result<(), DbError> {
        crate::db::lance_native::optimize_database(&self.db_path, &self.index_type)
            .map_err(DbError::Other)?;
        // optimize builds the index. Failure leaves restore_index set so
        // close() builds it; success must not build it again.
        self.restore_index = false;
        Ok(())
    }

    fn read_file_states(&self) -> Result<Vec<DbFileEntry>, DbError> {
        crate::db::lance_native::read_file_states(&self.db_path).map_err(DbError::Other)
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
