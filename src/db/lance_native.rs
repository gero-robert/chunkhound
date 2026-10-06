//! Lance dataset access for the store thread.
//!
//! `write_format_probe` checks that this `lance` crate writes a table the
//! installed `lancedb` package can open. Index deletes and inserts use the
//! same crate and match `lance_store.py`: file ids, chunk hashes, schemas,
//! and the one-time embedding-width migration. The vector index, optimize,
//! and file-state reads stay in that Python module. Search opens the tables
//! with `lancedb`.

use std::collections::{HashMap, HashSet};
use std::future::Future;
use std::path::{Path, PathBuf};
use std::sync::{Arc, LazyLock, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use arrow_array::builder::{Float32Builder, LargeListBuilder, ListBuilder, NullBufferBuilder};
use arrow_array::{
    Array, ArrayRef, FixedSizeListArray, Float32Array, Float64Array, Int64Array, LargeListArray,
    LargeStringArray, ListArray, RecordBatch, RecordBatchIterator, StringArray,
};
use arrow_schema::{DataType, Field, Schema as ArrowSchema};
use lance::dataset::{Dataset, NewColumnTransform, WriteMode, WriteParams};
use xxhash_rust::xxh3::Xxh3;

use crate::types::{BatchResult, ChunkRecord, DbWriterBatch, FileRecord};

const DELETE_BATCH: usize = 500;

const FILE_FIELDS: &[&str] = &[
    "id",
    "path",
    "size",
    "modified_time",
    "content_hash",
    "indexed_time",
    "language",
    "skip_reason",
    "name",
    "extension",
];

const CHUNK_FIELDS: &[&str] = &[
    "id",
    "file_id",
    "content",
    "start_line",
    "end_line",
    "chunk_type",
    "language",
    "name",
    "embedding",
    "provider",
    "model",
    "created_time",
    "metadata",
    "start_byte",
    "end_byte",
];

const CHUNK_FIELDS_WITHOUT_BYTES: &[&str] = &[
    "id",
    "file_id",
    "content",
    "start_line",
    "end_line",
    "chunk_type",
    "language",
    "name",
    "embedding",
    "provider",
    "model",
    "created_time",
    "metadata",
];

/// Next file id per database directory. Seeded once from `MAX(id)` and only
/// increased, including across deletes, so a removed id is not reused.
static FILE_IDS: LazyLock<Mutex<HashMap<String, i64>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

pub(crate) fn write_format_probe(directory: &str) -> Result<(), String> {
    std::fs::create_dir_all(directory).map_err(|err| err.to_string())?;
    let schema = Arc::new(ArrowSchema::new(vec![
        Field::new("id", DataType::Int64, false),
        Field::new("path", DataType::Utf8, false),
    ]));
    let batch = RecordBatch::try_new(
        schema.clone(),
        vec![
            Arc::new(Int64Array::from(vec![1_i64])),
            Arc::new(StringArray::from(vec!["main.py"])),
        ],
    )
    .map_err(|err| err.to_string())?;
    let reader = RecordBatchIterator::new(std::iter::once(Ok(batch)), schema);
    let dest = Path::new(directory).join("files.lance");
    // object_store accepts a forward-slash path. A Windows backslash is not a URI.
    let uri = dest.to_string_lossy().replace('\\', "/");
    let params = WriteParams {
        mode: WriteMode::Overwrite,
        ..Default::default()
    };
    block_on(async {
        Dataset::write(reader, uri.as_str(), Some(params))
            .await
            .map(|_| ())
            .map_err(|err| err.to_string())
    })
}

/// Delete `delete_paths` and rows that `existing_file_id` will replace.
pub(crate) fn apply_deletes(directory: &str, batch: &DbWriterBatch) -> Result<(), String> {
    block_on(apply_deletes_async(directory, batch))
}

/// Insert one store-thread batch. Deletes run again so a caller that skips
/// `prepare_write` still replaces existing rows. The second delete matches
/// nothing new.
pub(crate) fn write_index_batch(
    directory: &str,
    batch: &DbWriterBatch,
) -> Result<BatchResult, String> {
    block_on(write_batch_async(directory, batch))
}

fn block_on<T>(future: impl Future<Output = Result<T, String>>) -> Result<T, String> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|err| err.to_string())?;
    runtime.block_on(future)
}

fn file_ids() -> std::sync::MutexGuard<'static, HashMap<String, i64>> {
    FILE_IDS.lock().unwrap_or_else(|err| err.into_inner())
}

/// `os.path.normcase(os.path.abspath(...))`. Windows compares paths case-insensitively.
fn db_key(directory: &str) -> String {
    let absolute = std::path::absolute(directory).unwrap_or_else(|_| PathBuf::from(directory));
    let text = absolute.to_string_lossy();
    if cfg!(windows) {
        text.to_lowercase()
    } else {
        text.into_owned()
    }
}

fn unix_time() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs_f64())
        .unwrap_or(0.0)
}

fn table_uri(directory: &str, name: &str) -> String {
    Path::new(directory)
        .join(format!("{name}.lance"))
        .to_string_lossy()
        .replace('\\', "/")
}

async fn open_table(directory: &str, name: &str) -> Result<Option<Dataset>, String> {
    let path = Path::new(directory).join(format!("{name}.lance"));
    if !path.exists() {
        return Ok(None);
    }
    Dataset::open(&table_uri(directory, name))
        .await
        .map(Some)
        .map_err(|err| err.to_string())
}

async fn apply_deletes_async(directory: &str, batch: &DbWriterBatch) -> Result<(), String> {
    let existing: Vec<i64> = batch
        .files
        .iter()
        .filter_map(|file| file.existing_file_id)
        .collect();
    if batch.delete_paths.is_empty() && existing.is_empty() {
        return Ok(());
    }
    // Seed before the delete. prepare_write reaches this with no earlier
    // seed, and a deleted max id must not become the next id.
    seed_file_ids(directory).await?;
    let mut files = open_table(directory, "files").await?;
    let mut chunks = open_table(directory, "chunks").await?;
    let path_ids = if batch.delete_paths.is_empty() {
        Vec::new()
    } else if let Some(dataset) = files.as_ref() {
        file_ids_for_paths(dataset, &batch.delete_paths).await?
    } else {
        Vec::new()
    };
    delete_ids(&mut files, &mut chunks, &path_ids).await?;
    for file_id in existing {
        delete_where(&mut chunks, &format!("file_id = {file_id}")).await?;
        delete_where(&mut files, &format!("id = {file_id}")).await?;
    }
    Ok(())
}

async fn file_ids_for_paths(dataset: &Dataset, paths: &[String]) -> Result<Vec<i64>, String> {
    let mut found = Vec::new();
    for batch in paths.chunks(DELETE_BATCH) {
        let literals = batch
            .iter()
            .map(|path| format!("'{}'", path.replace('\'', "''")))
            .collect::<Vec<_>>()
            .join(", ");
        let filter = format!("path IN ({literals})");
        // An empty match makes `try_into_batch` fail on an empty concatenation.
        let matched = dataset
            .count_rows(Some(filter.clone()))
            .await
            .map_err(|err| err.to_string())?;
        if matched == 0 {
            continue;
        }
        let mut scan = dataset.scan();
        scan.project(&["id"]).map_err(|err| err.to_string())?;
        scan.filter(&filter).map_err(|err| err.to_string())?;
        let table = scan.try_into_batch().await.map_err(|err| err.to_string())?;
        let column = table
            .column_by_name("id")
            .ok_or_else(|| "files.id is missing from the path lookup".to_string())?;
        let ids = column
            .as_any()
            .downcast_ref::<Int64Array>()
            .ok_or_else(|| "files.id is not int64".to_string())?;
        for row in 0..ids.len() {
            if !ids.is_null(row) {
                found.push(ids.value(row));
            }
        }
    }
    Ok(found)
}

async fn delete_ids(
    files: &mut Option<Dataset>,
    chunks: &mut Option<Dataset>,
    ids: &[i64],
) -> Result<(), String> {
    for batch in ids.chunks(DELETE_BATCH) {
        if batch.is_empty() {
            continue;
        }
        let list = batch
            .iter()
            .map(|file_id| file_id.to_string())
            .collect::<Vec<_>>()
            .join(", ");
        delete_where(chunks, &format!("file_id IN ({list})")).await?;
        delete_where(files, &format!("id IN ({list})")).await?;
    }
    Ok(())
}

async fn delete_where(dataset: &mut Option<Dataset>, predicate: &str) -> Result<(), String> {
    if let Some(table) = dataset.as_mut() {
        table
            .delete(predicate)
            .await
            .map_err(|err| err.to_string())?;
    }
    Ok(())
}

/// Drop file rows inserted by a batch whose chunk write failed, plus any
/// chunks that committed for those ids. The differ skips a path whose mtime
/// and size still match, so the rows have to disappear for the next index
/// to rebuild the file.
async fn remove_written_files(directory: &str, ids: &[i64]) -> Result<(), String> {
    if ids.is_empty() {
        return Ok(());
    }
    let mut files = open_table(directory, "files").await?;
    let mut chunks = open_table(directory, "chunks").await?;
    delete_ids(&mut files, &mut chunks, ids).await
}

/// Null `modified_time` and `content_hash` on files whose embeddings were
/// cleared and that this batch does not rewrite. A null mtime is reprocessed
/// in place on the next index.
async fn mark_files_dirty(
    directory: &str,
    cleared_file_ids: &[i64],
    rewritten: &HashSet<i64>,
) -> Result<(), String> {
    let dirty: Vec<i64> = cleared_file_ids
        .iter()
        .copied()
        .filter(|file_id| !rewritten.contains(file_id))
        .collect::<HashSet<_>>()
        .into_iter()
        .collect();
    if dirty.is_empty() {
        return Ok(());
    }
    let Some(mut dataset) = open_table(directory, "files").await? else {
        return Ok(());
    };
    let mut rows = Vec::new();
    for batch in dirty.chunks(DELETE_BATCH) {
        let list = batch
            .iter()
            .map(|file_id| file_id.to_string())
            .collect::<Vec<_>>()
            .join(", ");
        let filter = format!("id IN ({list})");
        let matched = dataset
            .count_rows(Some(filter.clone()))
            .await
            .map_err(|err| err.to_string())?;
        if matched == 0 {
            continue;
        }
        let mut scan = dataset.scan();
        scan.filter(&filter).map_err(|err| err.to_string())?;
        let table = scan.try_into_batch().await.map_err(|err| err.to_string())?;
        for row in 0..table.num_rows() {
            let mut file = restore_file(&table, row, false)?;
            file.modified_time = None;
            file.content_hash = None;
            rows.push(file);
        }
    }
    if rows.is_empty() {
        return Ok(());
    }
    for batch in dirty.chunks(DELETE_BATCH) {
        let list = batch
            .iter()
            .map(|file_id| file_id.to_string())
            .collect::<Vec<_>>()
            .join(", ");
        dataset
            .delete(&format!("id IN ({list})"))
            .await
            .map_err(|err| err.to_string())?;
    }
    let schema = arrow_schema(&dataset);
    let batch = file_batch(&schema, &rows)?;
    append_batch(dataset, batch).await
}

async fn seed_file_ids(directory: &str) -> Result<(), String> {
    let key = db_key(directory);
    if file_ids().contains_key(&key) {
        return Ok(());
    }
    let next = match open_table(directory, "files").await? {
        None => 1,
        Some(dataset) => max_file_id(&dataset).await?.unwrap_or(0) + 1,
    };
    file_ids().entry(key).or_insert(next);
    Ok(())
}

async fn max_file_id(dataset: &Dataset) -> Result<Option<i64>, String> {
    let rows = dataset
        .count_rows(None)
        .await
        .map_err(|err| err.to_string())?;
    if rows == 0 {
        return Ok(None);
    }
    let mut scan = dataset.scan();
    scan.project(&["id"]).map_err(|err| err.to_string())?;
    let batch = scan.try_into_batch().await.map_err(|err| err.to_string())?;
    let column = batch
        .column_by_name("id")
        .ok_or_else(|| "files.id is missing".to_string())?;
    let ids = column
        .as_any()
        .downcast_ref::<Int64Array>()
        .ok_or_else(|| "files.id is not int64".to_string())?;
    Ok(ids.iter().flatten().max())
}

fn observe_file_id(directory: &str, file_id: i64) {
    let key = db_key(directory);
    let mut ids = file_ids();
    let slot = ids.entry(key).or_insert(1);
    let next = file_id.saturating_add(1);
    if next > *slot {
        *slot = next;
    }
}

fn allocate_file_id(directory: &str) -> i64 {
    let key = db_key(directory);
    let mut ids = file_ids();
    let slot = ids.entry(key).or_insert(1);
    let file_id = *slot;
    *slot = file_id.saturating_add(1);
    file_id
}

fn normalize_content(content: &str) -> String {
    content
        .replace("\r\n", "\n")
        .replace('\r', "\n")
        .trim()
        .to_string()
}

/// `chunkhound.utils.chunk_hashing.generate_chunk_id`.
fn generate_chunk_id(
    file_id: i64,
    content: &str,
    concept: Option<&str>,
    start_line: Option<i64>,
    end_line: Option<i64>,
) -> i64 {
    let normalized = normalize_content(content);
    let mut hasher = Xxh3::new();
    hasher.update(file_id.to_string().as_bytes());
    hasher.update(normalized.as_bytes());
    if let Some(concept) = concept {
        hasher.update(concept.as_bytes());
    }
    if let (Some(start), Some(end)) = (start_line, end_line) {
        hasher.update(format!("\0{start}:{end}").as_bytes());
    }
    hasher.digest() as i64
}

fn file_name_and_extension(path: &str) -> (String, Option<String>) {
    let normalized = path.replace('\\', "/");
    let mut name = normalized.rsplit('/').next().unwrap_or("").to_string();
    if name.is_empty() {
        name = path.to_string();
    }
    match name.rfind('.') {
        Some(dot) if dot > 0 => {
            let extension = name[dot + 1..].to_string();
            (name, Some(extension))
        }
        _ => (name, None),
    }
}

struct FileRow {
    id: i64,
    path: String,
    size: i64,
    modified_time: Option<f64>,
    content_hash: Option<String>,
    indexed_time: f64,
    language: String,
    skip_reason: Option<String>,
    name: String,
    extension: Option<String>,
}

struct ChunkRow {
    id: i64,
    file_id: i64,
    content: String,
    start_line: i64,
    end_line: i64,
    chunk_type: String,
    language: String,
    name: String,
    embedding: Option<Vec<f32>>,
    provider: String,
    model: String,
    created_time: f64,
    metadata: Option<String>,
    start_byte: Option<i64>,
    end_byte: Option<i64>,
}

fn build_file_row(file: &FileRecord, file_id: i64, now: f64) -> FileRow {
    let (name, extension) = file_name_and_extension(&file.path);
    FileRow {
        id: file_id,
        path: file.path.clone(),
        size: file.size_bytes.unwrap_or(0),
        modified_time: Some(file.mtime.unwrap_or(0.0)),
        content_hash: Some(file.content_hash.clone().unwrap_or_default()),
        indexed_time: now,
        language: file.language.clone().unwrap_or_default(),
        skip_reason: file.skip_reason.clone(),
        name,
        extension,
    }
}

fn build_chunk_row(file_id: i64, chunk: &ChunkRecord, now: f64) -> ChunkRow {
    let concept = if chunk.chunk_type.is_empty() {
        None
    } else {
        Some(chunk.chunk_type.as_str())
    };
    ChunkRow {
        id: generate_chunk_id(
            file_id,
            &chunk.code,
            concept,
            chunk.start_line,
            chunk.end_line,
        ),
        file_id,
        content: chunk.code.clone(),
        start_line: chunk.start_line.unwrap_or(0),
        end_line: chunk.end_line.unwrap_or(0),
        chunk_type: chunk.chunk_type.clone(),
        language: chunk.language.clone().unwrap_or_default(),
        name: chunk.symbol.clone().unwrap_or_default(),
        embedding: chunk
            .embedding
            .as_ref()
            .filter(|vector| !vector.is_empty())
            .cloned(),
        provider: chunk.provider.clone().unwrap_or_default(),
        model: chunk.model.clone().unwrap_or_default(),
        created_time: now,
        metadata: chunk.metadata.clone(),
        start_byte: chunk.start_byte,
        end_byte: chunk.end_byte,
    }
}

fn batch_dims(batch: &DbWriterBatch) -> Result<Option<i32>, String> {
    for file in &batch.files {
        for chunk in &file.chunks {
            if let Some(embedding) = chunk.embedding.as_ref().filter(|vector| !vector.is_empty()) {
                return i32::try_from(embedding.len())
                    .map(Some)
                    .map_err(|err| err.to_string());
            }
        }
    }
    Ok(None)
}

async fn write_batch_async(directory: &str, batch: &DbWriterBatch) -> Result<BatchResult, String> {
    // Seed before deletes. Otherwise a deleted max id becomes the next id.
    seed_file_ids(directory).await?;
    apply_deletes_async(directory, batch).await?;
    for file in &batch.files {
        if let Some(file_id) = file.existing_file_id {
            observe_file_id(directory, file_id);
        }
    }
    let dims = batch_dims(batch)?;
    let now = unix_time();
    let mut file_ids = Vec::with_capacity(batch.files.len());
    let mut file_rows = Vec::with_capacity(batch.files.len());
    let mut chunk_rows = Vec::new();
    let mut embeddings_written = 0u64;
    for file in &batch.files {
        let file_id = match file.existing_file_id {
            Some(file_id) => file_id,
            None => allocate_file_id(directory),
        };
        file_ids.push(file_id);
        file_rows.push(build_file_row(file, file_id, now));
        for chunk in &file.chunks {
            let row = build_chunk_row(file_id, chunk, now);
            if row.embedding.is_some() {
                embeddings_written += 1;
            }
            chunk_rows.push(row);
        }
    }
    write_files(directory, &file_rows).await?;
    if let Err(err) = write_chunks(directory, &chunk_rows, dims).await {
        // File rows commit before chunks. Drop this batch's rows so the next
        // index treats the paths as new instead of skipping them.
        if let Err(cleanup) = remove_written_files(directory, &file_ids).await {
            return Err(format!(
                "{err} (failed to drop the file rows written in this batch: {cleanup})"
            ));
        }
        return Err(err);
    }
    Ok(BatchResult {
        file_ids,
        chunks_written: chunk_rows.len() as u64,
        embeddings_written,
    })
}

fn same_names(schema: &ArrowSchema, expected: &[&str]) -> bool {
    schema.fields().len() == expected.len()
        && schema
            .fields()
            .iter()
            .zip(expected)
            .all(|(field, name)| field.name() == *name)
}

fn embedding_width(schema: &ArrowSchema) -> Option<i32> {
    let field = schema.field_with_name("embedding").ok()?;
    match field.data_type() {
        DataType::FixedSizeList(_, size) => Some(*size),
        _ => None,
    }
}

fn arrow_schema(dataset: &Dataset) -> Arc<ArrowSchema> {
    Arc::new(ArrowSchema::from(dataset.schema()))
}

fn files_schema() -> Arc<ArrowSchema> {
    Arc::new(ArrowSchema::new(vec![
        Field::new("id", DataType::Int64, true),
        Field::new("path", DataType::Utf8, true),
        Field::new("size", DataType::Int64, true),
        Field::new("modified_time", DataType::Float64, true),
        Field::new("content_hash", DataType::Utf8, true),
        Field::new("indexed_time", DataType::Float64, true),
        Field::new("language", DataType::Utf8, true),
        Field::new("skip_reason", DataType::Utf8, true),
        Field::new("name", DataType::Utf8, true),
        Field::new("extension", DataType::Utf8, true),
    ]))
}

fn chunks_schema(dims: Option<i32>) -> Arc<ArrowSchema> {
    let item = Arc::new(Field::new("item", DataType::Float32, true));
    let embedding = match dims {
        Some(size) => Field::new("embedding", DataType::FixedSizeList(item, size), true),
        None => Field::new("embedding", DataType::List(item), true),
    };
    Arc::new(ArrowSchema::new(vec![
        Field::new("id", DataType::Int64, true),
        Field::new("file_id", DataType::Int64, true),
        Field::new("content", DataType::Utf8, true),
        Field::new("start_line", DataType::Int64, true),
        Field::new("end_line", DataType::Int64, true),
        Field::new("chunk_type", DataType::Utf8, true),
        Field::new("language", DataType::Utf8, true),
        Field::new("name", DataType::Utf8, true),
        embedding,
        Field::new("provider", DataType::Utf8, true),
        Field::new("model", DataType::Utf8, true),
        Field::new("created_time", DataType::Float64, true),
        Field::new("metadata", DataType::Utf8, true),
        Field::new("start_byte", DataType::Int64, true),
        Field::new("end_byte", DataType::Int64, true),
    ]))
}

async fn create_table(
    directory: &str,
    name: &str,
    schema: Arc<ArrowSchema>,
    batch: RecordBatch,
) -> Result<(), String> {
    std::fs::create_dir_all(directory).map_err(|err| err.to_string())?;
    let reader = RecordBatchIterator::new(std::iter::once(Ok(batch)), schema);
    let params = WriteParams {
        mode: WriteMode::Overwrite,
        ..Default::default()
    };
    Dataset::write(reader, table_uri(directory, name).as_str(), Some(params))
        .await
        .map(|_| ())
        .map_err(|err| err.to_string())
}

async fn append_batch(mut dataset: Dataset, batch: RecordBatch) -> Result<(), String> {
    let schema = batch.schema();
    let reader = RecordBatchIterator::new(std::iter::once(Ok(batch)), schema);
    dataset
        .append(reader, None)
        .await
        .map_err(|err| err.to_string())
}

async fn write_files(directory: &str, rows: &[FileRow]) -> Result<(), String> {
    if rows.is_empty() {
        return Ok(());
    }
    let Some(dataset) = open_table(directory, "files").await? else {
        let schema = files_schema();
        let batch = file_batch(&schema, rows)?;
        return create_table(directory, "files", schema, batch).await;
    };
    let live = arrow_schema(&dataset);
    if same_names(&live, FILE_FIELDS) {
        let batch = file_batch(&live, rows)?;
        return append_batch(dataset, batch).await;
    }
    let old = read_files(&dataset).await?;
    drop(dataset);
    let schema = files_schema();
    if old.is_empty() {
        let batch = file_batch(&schema, rows)?;
        return create_table(directory, "files", schema, batch).await;
    }
    let old_batch = file_batch(&schema, &old)?;
    create_table(directory, "files", schema, old_batch).await?;
    let dataset = open_table(directory, "files")
        .await?
        .ok_or_else(|| "files table missing after schema rewrite".to_string())?;
    let live = arrow_schema(&dataset);
    let batch = file_batch(&live, rows)?;
    append_batch(dataset, batch).await
}

async fn write_chunks(directory: &str, rows: &[ChunkRow], dims: Option<i32>) -> Result<(), String> {
    if rows.is_empty() {
        return Ok(());
    }
    let Some(mut dataset) = open_table(directory, "chunks").await? else {
        let schema = chunks_schema(dims);
        let batch = chunk_batch(&schema, rows)?;
        return create_table(directory, "chunks", schema, batch).await;
    };
    let live = arrow_schema(&dataset);
    let width_ok = match dims {
        None => true,
        Some(wanted) => embedding_width(&live) == Some(wanted),
    };
    if same_names(&live, CHUNK_FIELDS) && width_ok {
        let batch = chunk_batch(&live, rows)?;
        return append_batch(dataset, batch).await;
    }
    if width_ok && same_names(&live, CHUNK_FIELDS_WITHOUT_BYTES) {
        let added = Arc::new(ArrowSchema::new(vec![
            Field::new("start_byte", DataType::Int64, true),
            Field::new("end_byte", DataType::Int64, true),
        ]));
        dataset
            .add_columns(NewColumnTransform::AllNulls(added), None, None)
            .await
            .map_err(|err| err.to_string())?;
        let live = arrow_schema(&dataset);
        let batch = chunk_batch(&live, rows)?;
        return append_batch(dataset, batch).await;
    }
    let target_dims = if width_ok {
        dims.or_else(|| embedding_width(&live))
    } else {
        dims
    };
    let (old, cleared_ids) = read_chunks_marked(&dataset, target_dims, !width_ok).await?;
    log::info!(
        "Recreating Lance chunks table at embedding width {} (was {}), keeping {} chunks",
        target_dims
            .map(|size| size.to_string())
            .unwrap_or_else(|| "None".to_string()),
        embedding_width(&live)
            .map(|size| size.to_string())
            .unwrap_or_else(|| "None".to_string()),
        old.len()
    );
    // Embeddings cleared by a width change belong to files this batch does
    // not rewrite. Null their mtime so the next index rebuilds them.
    let rewritten = rows.iter().map(|row| row.file_id).collect::<HashSet<_>>();
    mark_files_dirty(directory, &cleared_ids, &rewritten).await?;
    drop(dataset);
    let schema = chunks_schema(target_dims);
    if old.is_empty() {
        let batch = chunk_batch(&schema, rows)?;
        return create_table(directory, "chunks", schema, batch).await;
    }
    let old_batch = chunk_batch(&schema, &old)?;
    create_table(directory, "chunks", schema, old_batch).await?;
    let dataset = open_table(directory, "chunks")
        .await?
        .ok_or_else(|| "chunks table missing after schema rewrite".to_string())?;
    let live = arrow_schema(&dataset);
    let batch = chunk_batch(&live, rows)?;
    append_batch(dataset, batch).await
}

async fn read_files(dataset: &Dataset) -> Result<Vec<FileRow>, String> {
    let rows = dataset
        .count_rows(None)
        .await
        .map_err(|err| err.to_string())?;
    if rows == 0 {
        return Ok(Vec::new());
    }
    let scan = dataset.scan();
    let batch = scan.try_into_batch().await.map_err(|err| err.to_string())?;
    let recompute_all =
        batch.column_by_name("name").is_none() || batch.column_by_name("extension").is_none();
    let mut files = Vec::with_capacity(batch.num_rows());
    for row in 0..batch.num_rows() {
        files.push(restore_file(&batch, row, recompute_all)?);
    }
    Ok(files)
}

#[cfg(test)]
async fn read_chunks(
    dataset: &Dataset,
    dims: Option<i32>,
    clear_mismatched: bool,
) -> Result<Vec<ChunkRow>, String> {
    Ok(read_chunks_marked(dataset, dims, clear_mismatched).await?.0)
}

async fn read_chunks_marked(
    dataset: &Dataset,
    dims: Option<i32>,
    clear_mismatched: bool,
) -> Result<(Vec<ChunkRow>, Vec<i64>), String> {
    let rows = dataset
        .count_rows(None)
        .await
        .map_err(|err| err.to_string())?;
    if rows == 0 {
        return Ok((Vec::new(), Vec::new()));
    }
    let scan = dataset.scan();
    let batch = scan.try_into_batch().await.map_err(|err| err.to_string())?;
    let mut chunks = Vec::with_capacity(batch.num_rows());
    let mut cleared_file_ids = Vec::new();
    for row in 0..batch.num_rows() {
        let (chunk, lost_vector) = restore_chunk(&batch, row, dims, clear_mismatched)?;
        if lost_vector {
            cleared_file_ids.push(chunk.file_id);
        }
        chunks.push(chunk);
    }
    Ok((chunks, cleared_file_ids))
}

fn restore_file(batch: &RecordBatch, row: usize, recompute_all: bool) -> Result<FileRow, String> {
    let path = utf8_or_empty(batch, "path", row)?;
    let stored_name = optional_utf8(batch, "name", row)?;
    let stored_extension = optional_utf8(batch, "extension", row)?;
    let (name, extension) = if recompute_all || stored_name.is_none() {
        file_name_and_extension(&path)
    } else {
        (stored_name.unwrap_or_default(), stored_extension)
    };
    Ok(FileRow {
        id: required_i64(batch, "id", row)?,
        path,
        size: optional_i64(batch, "size", row)?.unwrap_or(0),
        modified_time: optional_f64(batch, "modified_time", row)?,
        content_hash: optional_utf8(batch, "content_hash", row)?,
        indexed_time: optional_f64(batch, "indexed_time", row)?.unwrap_or(0.0),
        language: utf8_or_empty(batch, "language", row)?,
        skip_reason: optional_utf8(batch, "skip_reason", row)?,
        name,
        extension,
    })
}

fn restore_chunk(
    batch: &RecordBatch,
    row: usize,
    dims: Option<i32>,
    clear_mismatched: bool,
) -> Result<(ChunkRow, bool), String> {
    let embedding = match batch.column_by_name("embedding") {
        None => None,
        Some(column) => embedding_value(column.as_ref(), row)?,
    };
    let had_vector = embedding.as_ref().is_some_and(|vector| !vector.is_empty());
    let (embedding, cleared) = accept_embedding(embedding, dims, clear_mismatched);
    let lost_vector = cleared && had_vector;
    Ok((
        ChunkRow {
            id: required_i64(batch, "id", row)?,
            file_id: required_i64(batch, "file_id", row)?,
            content: utf8_or_empty(batch, "content", row)?,
            start_line: optional_i64(batch, "start_line", row)?.unwrap_or(0),
            end_line: optional_i64(batch, "end_line", row)?.unwrap_or(0),
            chunk_type: utf8_or_empty(batch, "chunk_type", row)?,
            language: utf8_or_empty(batch, "language", row)?,
            name: utf8_or_empty(batch, "name", row)?,
            embedding,
            provider: if cleared {
                String::new()
            } else {
                utf8_or_empty(batch, "provider", row)?
            },
            model: if cleared {
                String::new()
            } else {
                utf8_or_empty(batch, "model", row)?
            },
            created_time: optional_f64(batch, "created_time", row)?.unwrap_or(0.0),
            metadata: optional_utf8(batch, "metadata", row)?,
            start_byte: optional_i64(batch, "start_byte", row)?,
            end_byte: optional_i64(batch, "end_byte", row)?,
        },
        lost_vector,
    ))
}

fn accept_embedding(
    value: Option<Vec<f32>>,
    dims: Option<i32>,
    clear_mismatched: bool,
) -> (Option<Vec<f32>>, bool) {
    if !clear_mismatched {
        return (value, false);
    }
    match (value, dims) {
        (Some(vector), Some(width)) if vector.len() == width as usize => (Some(vector), false),
        _ => (None, true),
    }
}

fn file_batch(schema: &Arc<ArrowSchema>, rows: &[FileRow]) -> Result<RecordBatch, String> {
    let columns = schema
        .fields()
        .iter()
        .map(|field| file_column(field, rows))
        .collect::<Result<Vec<_>, _>>()?;
    RecordBatch::try_new(schema.clone(), columns)
        .map_err(|err| format!("building files batch: {err}"))
}

fn chunk_batch(schema: &Arc<ArrowSchema>, rows: &[ChunkRow]) -> Result<RecordBatch, String> {
    let columns = schema
        .fields()
        .iter()
        .map(|field| chunk_column(field, rows))
        .collect::<Result<Vec<_>, _>>()?;
    RecordBatch::try_new(schema.clone(), columns)
        .map_err(|err| format!("building chunks batch: {err}"))
}

fn file_column(field: &Field, rows: &[FileRow]) -> Result<ArrayRef, String> {
    match field.name().as_str() {
        "id" => i64_column(field, rows.iter().map(|row| Some(row.id))),
        "path" => utf8_column(field, rows.iter().map(|row| Some(row.path.as_str()))),
        "size" => i64_column(field, rows.iter().map(|row| Some(row.size))),
        "modified_time" => f64_column(field, rows.iter().map(|row| row.modified_time)),
        "content_hash" => utf8_column(field, rows.iter().map(|row| row.content_hash.as_deref())),
        "indexed_time" => f64_column(field, rows.iter().map(|row| Some(row.indexed_time))),
        "language" => utf8_column(field, rows.iter().map(|row| Some(row.language.as_str()))),
        "skip_reason" => utf8_column(field, rows.iter().map(|row| row.skip_reason.as_deref())),
        "name" => utf8_column(field, rows.iter().map(|row| Some(row.name.as_str()))),
        "extension" => utf8_column(field, rows.iter().map(|row| row.extension.as_deref())),
        other => Err(format!("unexpected files column {other}")),
    }
}

fn chunk_column(field: &Field, rows: &[ChunkRow]) -> Result<ArrayRef, String> {
    match field.name().as_str() {
        "id" => i64_column(field, rows.iter().map(|row| Some(row.id))),
        "file_id" => i64_column(field, rows.iter().map(|row| Some(row.file_id))),
        "content" => utf8_column(field, rows.iter().map(|row| Some(row.content.as_str()))),
        "start_line" => i64_column(field, rows.iter().map(|row| Some(row.start_line))),
        "end_line" => i64_column(field, rows.iter().map(|row| Some(row.end_line))),
        "chunk_type" => utf8_column(field, rows.iter().map(|row| Some(row.chunk_type.as_str()))),
        "language" => utf8_column(field, rows.iter().map(|row| Some(row.language.as_str()))),
        "name" => utf8_column(field, rows.iter().map(|row| Some(row.name.as_str()))),
        "embedding" => embedding_column(field, rows),
        "provider" => utf8_column(field, rows.iter().map(|row| Some(row.provider.as_str()))),
        "model" => utf8_column(field, rows.iter().map(|row| Some(row.model.as_str()))),
        "created_time" => f64_column(field, rows.iter().map(|row| Some(row.created_time))),
        "metadata" => utf8_column(field, rows.iter().map(|row| row.metadata.as_deref())),
        "start_byte" => i64_column(field, rows.iter().map(|row| row.start_byte)),
        "end_byte" => i64_column(field, rows.iter().map(|row| row.end_byte)),
        other => Err(format!("unexpected chunks column {other}")),
    }
}

fn i64_column<I>(field: &Field, values: I) -> Result<ArrayRef, String>
where
    I: Iterator<Item = Option<i64>>,
{
    let nullable = field.is_nullable();
    let data: Vec<Option<i64>> = values
        .map(|value| match value {
            Some(item) => Some(item),
            None if nullable => None,
            None => Some(0),
        })
        .collect();
    match field.data_type() {
        DataType::Int64 => Ok(Arc::new(Int64Array::from(data))),
        other => Err(format!("{} has unsupported type {other}", field.name())),
    }
}

fn f64_column<I>(field: &Field, values: I) -> Result<ArrayRef, String>
where
    I: Iterator<Item = Option<f64>>,
{
    let nullable = field.is_nullable();
    let data: Vec<Option<f64>> = values
        .map(|value| match value {
            Some(item) => Some(item),
            None if nullable => None,
            None => Some(0.0),
        })
        .collect();
    match field.data_type() {
        DataType::Float64 => Ok(Arc::new(Float64Array::from(data))),
        other => Err(format!("{} has unsupported type {other}", field.name())),
    }
}

fn utf8_column<'a, I>(field: &Field, values: I) -> Result<ArrayRef, String>
where
    I: Iterator<Item = Option<&'a str>>,
{
    let nullable = field.is_nullable();
    let data: Vec<Option<String>> = values
        .map(|value| match value {
            Some(item) => Some(item.to_string()),
            None if nullable => None,
            None => Some(String::new()),
        })
        .collect();
    match field.data_type() {
        DataType::Utf8 => Ok(Arc::new(StringArray::from(data))),
        DataType::LargeUtf8 => Ok(Arc::new(LargeStringArray::from(data))),
        other => Err(format!("{} has unsupported type {other}", field.name())),
    }
}

fn embedding_column(field: &Field, rows: &[ChunkRow]) -> Result<ArrayRef, String> {
    match field.data_type() {
        DataType::FixedSizeList(inner, size) => fixed_embedding(inner.clone(), *size, rows),
        DataType::List(inner) => variable_embedding(inner.clone(), rows, false),
        DataType::LargeList(inner) => variable_embedding(inner.clone(), rows, true),
        other => Err(format!("embedding column has unsupported type {other}")),
    }
}

fn fixed_embedding(
    inner: arrow_schema::FieldRef,
    size: i32,
    rows: &[ChunkRow],
) -> Result<ArrayRef, String> {
    let width = usize::try_from(size).map_err(|err| err.to_string())?;
    let mut values = Float32Builder::with_capacity(rows.len().saturating_mul(width));
    let mut nulls = NullBufferBuilder::new(rows.len());
    let zeros = vec![0.0_f32; width];
    for row in rows {
        match &row.embedding {
            Some(vector) if vector.len() == width => {
                values.append_slice(vector);
                nulls.append(true);
            }
            Some(vector) => {
                return Err(format!(
                    "embedding width {} does not match column width {width}",
                    vector.len()
                ));
            }
            None => {
                if width > 0 {
                    values.append_slice(&zeros);
                }
                nulls.append(false);
            }
        }
    }
    FixedSizeListArray::try_new(inner, size, Arc::new(values.finish()), nulls.finish())
        .map(|array| Arc::new(array) as ArrayRef)
        .map_err(|err| err.to_string())
}

fn variable_embedding(
    inner: arrow_schema::FieldRef,
    rows: &[ChunkRow],
    large: bool,
) -> Result<ArrayRef, String> {
    if large {
        let mut builder = LargeListBuilder::new(Float32Builder::new()).with_field(inner);
        for row in rows {
            match &row.embedding {
                Some(vector) => {
                    builder.values().append_slice(vector);
                    builder.append(true);
                }
                None => builder.append(false),
            }
        }
        Ok(Arc::new(builder.finish()))
    } else {
        let mut builder = ListBuilder::new(Float32Builder::new()).with_field(inner);
        for row in rows {
            match &row.embedding {
                Some(vector) => {
                    builder.values().append_slice(vector);
                    builder.append(true);
                }
                None => builder.append(false),
            }
        }
        Ok(Arc::new(builder.finish()))
    }
}

fn required_i64(batch: &RecordBatch, name: &str, row: usize) -> Result<i64, String> {
    optional_i64(batch, name, row)?.ok_or_else(|| format!("{name} is null"))
}

fn optional_i64(batch: &RecordBatch, name: &str, row: usize) -> Result<Option<i64>, String> {
    match batch.column_by_name(name) {
        None => Ok(None),
        Some(column) => i64_value(column.as_ref(), row),
    }
}

fn i64_value(column: &dyn Array, row: usize) -> Result<Option<i64>, String> {
    if column.is_null(row) {
        return Ok(None);
    }
    column
        .as_any()
        .downcast_ref::<Int64Array>()
        .map(|array| Some(array.value(row)))
        .ok_or_else(|| format!("expected int64, found {}", column.data_type()))
}

fn optional_f64(batch: &RecordBatch, name: &str, row: usize) -> Result<Option<f64>, String> {
    match batch.column_by_name(name) {
        None => Ok(None),
        Some(column) => f64_value(column.as_ref(), row),
    }
}

fn f64_value(column: &dyn Array, row: usize) -> Result<Option<f64>, String> {
    if column.is_null(row) {
        return Ok(None);
    }
    column
        .as_any()
        .downcast_ref::<Float64Array>()
        .map(|array| Some(array.value(row)))
        .ok_or_else(|| format!("expected float64, found {}", column.data_type()))
}

fn utf8_or_empty(batch: &RecordBatch, name: &str, row: usize) -> Result<String, String> {
    Ok(optional_utf8(batch, name, row)?.unwrap_or_default())
}

fn optional_utf8(batch: &RecordBatch, name: &str, row: usize) -> Result<Option<String>, String> {
    match batch.column_by_name(name) {
        None => Ok(None),
        Some(column) => utf8_value(column.as_ref(), row),
    }
}

fn utf8_value(column: &dyn Array, row: usize) -> Result<Option<String>, String> {
    if column.is_null(row) {
        return Ok(None);
    }
    if let Some(array) = column.as_any().downcast_ref::<StringArray>() {
        return Ok(Some(array.value(row).to_string()));
    }
    if let Some(array) = column.as_any().downcast_ref::<LargeStringArray>() {
        return Ok(Some(array.value(row).to_string()));
    }
    Err(format!(
        "expected a string column, found {}",
        column.data_type()
    ))
}

fn embedding_value(column: &dyn Array, row: usize) -> Result<Option<Vec<f32>>, String> {
    if column.is_null(row) {
        return Ok(None);
    }
    if let Some(list) = column.as_any().downcast_ref::<FixedSizeListArray>() {
        return floats_of(list.value(row).as_ref()).map(Some);
    }
    if let Some(list) = column.as_any().downcast_ref::<ListArray>() {
        return floats_of(list.value(row).as_ref()).map(Some);
    }
    if let Some(list) = column.as_any().downcast_ref::<LargeListArray>() {
        return floats_of(list.value(row).as_ref()).map(Some);
    }
    Err(format!(
        "embedding column has unsupported type {}",
        column.data_type()
    ))
}

fn floats_of(values: &dyn Array) -> Result<Vec<f32>, String> {
    values
        .as_any()
        .downcast_ref::<Float32Array>()
        .map(|array| array.values().to_vec())
        .ok_or_else(|| format!("embedding values are {}, not float32", values.data_type()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chunk(
        symbol: &str,
        code: &str,
        start: i64,
        end: i64,
        embedding: Option<Vec<f32>>,
    ) -> ChunkRecord {
        ChunkRecord {
            chunk_type: "function".into(),
            symbol: Some(symbol.into()),
            code: code.into(),
            start_line: Some(start),
            end_line: Some(end),
            start_byte: None,
            end_byte: None,
            language: Some("python".into()),
            metadata: None,
            embedding,
            provider: Some("fake".into()),
            model: Some("fake-embeddings".into()),
        }
    }

    fn file(path: &str, existing: Option<i64>, chunks: Vec<ChunkRecord>) -> FileRecord {
        FileRecord {
            existing_file_id: existing,
            path: path.into(),
            mtime: Some(1.5),
            size_bytes: Some(4),
            content_hash: Some("abc".into()),
            language: Some("python".into()),
            skip_reason: None,
            chunks,
        }
    }

    #[test]
    fn chunk_id_matches_python_xxh3() {
        assert_eq!(
            generate_chunk_id(123, "def foo(): pass", None, None, None),
            -5663814011742303171
        );
        assert_eq!(
            generate_chunk_id(123, "def foo(): pass", Some("function"), Some(1), Some(2)),
            -6240286046547110159
        );
        assert_eq!(
            generate_chunk_id(1, "return 1\r\n", None, None, None),
            -7801835705982601440
        );
        assert_eq!(
            generate_chunk_id(7, "  code \r\n", None, Some(4), Some(5)),
            1326074192007274496
        );
    }

    #[test]
    fn file_name_matches_duckdb_rules() {
        assert_eq!(
            file_name_and_extension("src/pkg/main.py"),
            ("main.py".into(), Some("py".into()))
        );
        assert_eq!(
            file_name_and_extension("vendor/libfoo.tar.gz"),
            ("libfoo.tar.gz".into(), Some("gz".into()))
        );
        assert_eq!(
            file_name_and_extension("Makefile"),
            ("Makefile".into(), None)
        );
        assert_eq!(
            file_name_and_extension(".gitignore"),
            (".gitignore".into(), None)
        );
        assert_eq!(
            file_name_and_extension("src\\a.py"),
            ("a.py".into(), Some("py".into()))
        );
    }

    #[test]
    fn index_write_stores_names_distinct_ids_and_null_embeddings() {
        let temp = tempfile::tempdir().unwrap();
        let directory = temp.path().to_str().unwrap();
        let batch = DbWriterBatch {
            files: vec![
                file(
                    "src/pkg/main.py",
                    None,
                    vec![
                        chunk("a", "return 1", 1, 2, None),
                        chunk("b", "return 1", 4, 5, None),
                    ],
                ),
                file(".gitignore", None, vec![]),
            ],
            delete_paths: vec![],
        };
        let written = write_index_batch(directory, &batch).unwrap();
        assert_eq!(written.file_ids, vec![1, 2]);
        assert_eq!(written.chunks_written, 2);
        assert_eq!(written.embeddings_written, 0);

        let rows = block_on(async {
            let dataset = open_table(directory, "chunks").await?.unwrap();
            read_chunks(&dataset, None, false).await
        })
        .unwrap();
        assert_eq!(rows.len(), 2);
        assert_ne!(rows[0].id, rows[1].id);
        assert!(rows.iter().all(|row| row.embedding.is_none()));
        assert_eq!(dataset_fragment_count(directory, "chunks"), 1);

        let files = block_on(async {
            let dataset = open_table(directory, "files").await?.unwrap();
            read_files(&dataset).await
        })
        .unwrap();
        let by_path = files
            .iter()
            .map(|row| (row.path.as_str(), row))
            .collect::<std::collections::HashMap<_, _>>();
        assert_eq!(by_path["src/pkg/main.py"].name, "main.py");
        assert_eq!(by_path["src/pkg/main.py"].extension.as_deref(), Some("py"));
        assert_eq!(by_path[".gitignore"].name, ".gitignore");
        assert!(by_path[".gitignore"].extension.is_none());
    }

    #[test]
    fn index_write_deletes_quoted_paths_and_does_not_reuse_ids() {
        let temp = tempfile::tempdir().unwrap();
        let directory = temp.path().to_str().unwrap();
        let quoted = "x' OR path = 'keep.py";
        let first = DbWriterBatch {
            files: vec![
                file(
                    "keep.py",
                    None,
                    vec![chunk("f", "keep", 1, 1, Some(vec![0.5, 0.25]))],
                ),
                file(
                    "gone.py",
                    None,
                    vec![chunk("f", "gone", 1, 1, Some(vec![0.5, 0.25]))],
                ),
                file(
                    quoted,
                    None,
                    vec![chunk("f", "quoted", 1, 1, Some(vec![0.5, 0.25]))],
                ),
            ],
            delete_paths: vec![],
        };
        let written = write_index_batch(directory, &first).unwrap();
        assert_eq!(written.file_ids, vec![1, 2, 3]);

        let second = DbWriterBatch {
            files: vec![file(
                "keep.py",
                Some(1),
                vec![chunk("f", "kept-again", 1, 1, Some(vec![0.5, 0.25]))],
            )],
            delete_paths: vec!["gone.py".into(), quoted.into(), "missing.py".into()],
        };
        let again = write_index_batch(directory, &second).unwrap();
        assert_eq!(again.file_ids, vec![1]);

        let files = block_on(async {
            let dataset = open_table(directory, "files").await?.unwrap();
            read_files(&dataset).await
        })
        .unwrap();
        let paths = files
            .iter()
            .map(|row| row.path.as_str())
            .collect::<Vec<_>>();
        assert_eq!(paths, vec!["keep.py"]);
        assert_eq!(files[0].id, 1);

        let chunks = block_on(async {
            let dataset = open_table(directory, "chunks").await?.unwrap();
            read_chunks(&dataset, Some(2), false).await
        })
        .unwrap();
        assert_eq!(chunks.len(), 1);
        assert_eq!(chunks[0].content, "kept-again");
        assert_eq!(chunks[0].file_id, 1);

        let third = write_index_batch(
            directory,
            &DbWriterBatch {
                files: vec![file("new.py", None, vec![])],
                delete_paths: vec![],
            },
        )
        .unwrap();
        assert_eq!(third.file_ids, vec![4]);
    }

    #[test]
    fn index_write_migrates_embedding_width_and_keeps_byte_offsets() {
        let temp = tempfile::tempdir().unwrap();
        let directory = temp.path().to_str().unwrap();
        let mut kept = chunk("kept", "kept", 1, 1, Some(vec![0.5, 0.5, 0.5, 0.5]));
        kept.start_byte = Some(3);
        kept.end_byte = Some(6);
        kept.provider = Some("voyageai".into());
        kept.model = Some("voyage-code-2".into());
        write_index_batch(
            directory,
            &DbWriterBatch {
                files: vec![file("kept.py", None, vec![kept])],
                delete_paths: vec![],
            },
        )
        .unwrap();

        let mut added = chunk("added", "added", 1, 1, Some(vec![0.25; 8]));
        added.provider = Some("voyageai".into());
        added.model = Some("voyage-code-2".into());
        write_index_batch(
            directory,
            &DbWriterBatch {
                files: vec![file("added.py", None, vec![added])],
                delete_paths: vec![],
            },
        )
        .unwrap();

        let chunks = block_on(async {
            let dataset = open_table(directory, "chunks").await?.unwrap();
            let width = embedding_width(&arrow_schema(&dataset));
            let rows = read_chunks(&dataset, Some(8), false).await?;
            Ok::<_, String>((width, rows))
        })
        .unwrap();
        assert_eq!(chunks.0, Some(8));
        let by_name = chunks
            .1
            .iter()
            .map(|row| (row.name.as_str(), row))
            .collect::<std::collections::HashMap<_, _>>();
        assert!(by_name["kept"].embedding.is_none());
        assert_eq!(by_name["kept"].provider, "");
        assert_eq!(by_name["kept"].start_byte, Some(3));
        assert_eq!(by_name["kept"].end_byte, Some(6));
        assert_eq!(by_name["added"].embedding.as_ref().unwrap().len(), 8);

        let files = block_on(async {
            let dataset = open_table(directory, "files").await?.unwrap();
            read_files(&dataset).await
        })
        .unwrap();
        let files_by_path = files
            .iter()
            .map(|row| (row.path.as_str(), row))
            .collect::<std::collections::HashMap<_, _>>();
        assert!(files_by_path["kept.py"].modified_time.is_none());
        assert!(files_by_path["kept.py"].content_hash.is_none());
        assert_eq!(files_by_path["added.py"].modified_time, Some(1.5));
        assert_eq!(
            files_by_path["added.py"].content_hash.as_deref(),
            Some("abc")
        );
    }

    #[test]
    fn failed_chunk_write_drops_the_file_row() {
        let temp = tempfile::tempdir().unwrap();
        let directory = temp.path().to_str().unwrap();
        write_index_batch(
            directory,
            &DbWriterBatch {
                files: vec![file(
                    "keep.py",
                    None,
                    vec![chunk("f", "keep", 1, 1, Some(vec![0.5, 0.25]))],
                )],
                delete_paths: vec![],
            },
        )
        .unwrap();

        let err = write_index_batch(
            directory,
            &DbWriterBatch {
                files: vec![file(
                    "bad.py",
                    None,
                    vec![
                        chunk("f", "ok", 1, 1, Some(vec![0.5, 0.25])),
                        chunk("f", "wide", 2, 2, Some(vec![0.5, 0.25, 0.125])),
                    ],
                )],
                delete_paths: vec![],
            },
        )
        .unwrap_err();
        assert!(err.contains("embedding width"), "{err}");

        let files = block_on(async {
            let dataset = open_table(directory, "files").await?.unwrap();
            read_files(&dataset).await
        })
        .unwrap();
        let paths = files
            .iter()
            .map(|row| row.path.as_str())
            .collect::<Vec<_>>();
        assert_eq!(paths, vec!["keep.py"]);

        let chunks = block_on(async {
            let dataset = open_table(directory, "chunks").await?.unwrap();
            read_chunks(&dataset, Some(2), false).await
        })
        .unwrap();
        assert_eq!(chunks.len(), 1);
        assert_eq!(chunks[0].content, "keep");
    }

    #[test]
    fn failed_replace_drops_the_replacement_row() {
        let temp = tempfile::tempdir().unwrap();
        let directory = temp.path().to_str().unwrap();
        write_index_batch(
            directory,
            &DbWriterBatch {
                files: vec![file(
                    "keep.py",
                    None,
                    vec![chunk("f", "keep", 1, 1, Some(vec![0.5, 0.25]))],
                )],
                delete_paths: vec![],
            },
        )
        .unwrap();

        let err = write_index_batch(
            directory,
            &DbWriterBatch {
                files: vec![file(
                    "keep.py",
                    Some(1),
                    vec![
                        chunk("f", "ok", 1, 1, Some(vec![0.5, 0.25])),
                        chunk("f", "wide", 2, 2, Some(vec![0.5, 0.25, 0.125])),
                    ],
                )],
                delete_paths: vec![],
            },
        )
        .unwrap_err();
        assert!(err.contains("embedding width"), "{err}");

        let files = block_on(async {
            let dataset = open_table(directory, "files").await?.unwrap();
            read_files(&dataset).await
        })
        .unwrap();
        assert!(files.is_empty(), "file rows remained: {}", files.len());
        let chunks = block_on(async {
            let dataset = open_table(directory, "chunks").await?.unwrap();
            read_chunks(&dataset, Some(2), false).await
        })
        .unwrap();
        assert!(chunks.is_empty(), "chunk rows remained: {}", chunks.len());
    }

    #[test]
    fn orphan_delete_does_not_reuse_the_highest_id() {
        let temp = tempfile::tempdir().unwrap();
        let directory = temp.path().to_str().unwrap();
        let first = write_index_batch(
            directory,
            &DbWriterBatch {
                files: vec![
                    file("keep.py", None, vec![]),
                    file("mid.py", None, vec![]),
                    file("gone.py", None, vec![]),
                ],
                delete_paths: vec![],
            },
        )
        .unwrap();
        assert_eq!(first.file_ids, vec![1, 2, 3]);
        // A later process has not seeded yet. prepare_write deletes first.
        super::file_ids().remove(&super::db_key(directory));
        apply_deletes(
            directory,
            &DbWriterBatch {
                files: vec![],
                delete_paths: vec!["gone.py".into()],
            },
        )
        .unwrap();
        let next = write_index_batch(
            directory,
            &DbWriterBatch {
                files: vec![file("new.py", None, vec![])],
                delete_paths: vec![],
            },
        )
        .unwrap();
        assert_eq!(next.file_ids, vec![4]);
    }

    fn dataset_fragment_count(directory: &str, name: &str) -> usize {
        block_on(async {
            let dataset = open_table(directory, name).await?.unwrap();
            Ok(dataset.count_fragments())
        })
        .unwrap()
    }
}
