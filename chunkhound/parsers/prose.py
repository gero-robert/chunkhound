"""Prose-oriented chunker for memory and natural-language documents.

Splits on Markdown headings, paragraph boundaries, bullet lists, and fenced code
blocks while preserving semantic context. Used when indexing.chunker is "prose".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from chunkhound.core.models.chunk import Chunk
from chunkhound.core.types.common import (
    ByteOffset,
    ChunkType,
    FileId,
    FilePath,
    Language,
    LineNumber,
)
from chunkhound.parsers.chunk_splitter import CASTConfig, ChunkSplitter
from chunkhound.parsers.universal_engine import UniversalChunk, UniversalConcept
from chunkhound.utils.normalization import normalize_content

PROSE_EXTENSIONS = frozenset(
    {
        ".md",
        ".markdown",
        ".mdown",
        ".mkd",
        ".mdx",
        ".txt",
        ".text",
        ".html",
        ".htm",
    }
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_BULLET_RE = re.compile(r"^(\s*)([-*+]|\d+\.)\s+")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def is_prose_file(file_path: Path) -> bool:
    """Return True when the file extension is prose-indexable."""
    return file_path.suffix.lower() in PROSE_EXTENSIONS


@dataclass(frozen=True)
class _ProseSegment:
    """A contiguous prose region with source line numbers."""

    content: str
    start_line: int
    end_line: int
    chunk_type: ChunkType
    symbol: str
    parent_header: str | None = None


class ProseParser:
    """Parser for prose/memory documents with heading-aware chunk boundaries."""

    def __init__(self, cast_config: CASTConfig | None = None) -> None:
        self.cast_config = cast_config or CASTConfig()
        self.chunk_splitter = ChunkSplitter(self.cast_config)

    @property
    def language(self) -> Language:
        return Language.MARKDOWN

    def parse_file(self, file_path: Path, file_id: FileId) -> list[Chunk]:
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            for encoding in ("latin-1", "cp1252", "iso-8859-1"):
                try:
                    content = file_path.read_text(encoding=encoding)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise

        if file_path.suffix.lower() not in {".html", ".htm"}:
            content = normalize_content(content)

        return self.parse_content(content, file_path, file_id)

    def parse_content(
        self,
        content: str,
        file_path: Path | None = None,
        file_id: FileId | None = None,
    ) -> list[Chunk]:
        if not content.strip():
            return []

        body, line_offset = _strip_frontmatter_stub(content)
        segments = _segment_prose(body, start_line=1 + line_offset)
        universal_chunks = _segments_to_universal(segments)
        validated: list[UniversalChunk] = []
        for chunk in universal_chunks:
            validated.extend(self.chunk_splitter.validate_and_split(chunk))

        return _universal_to_chunks(
            validated,
            source=body,
            file_path=file_path,
            file_id=file_id or FileId(0),
        )


def _strip_frontmatter_stub(content: str) -> tuple[str, int]:
    """Remove YAML frontmatter block; metadata extraction added in Phase 2."""
    if not content.startswith("---"):
        return content, 0

    lines = content.split("\n")
    if len(lines) < 2:
        return content, 0

    end_idx: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break

    if end_idx is None:
        return content, 0

    body = "\n".join(lines[end_idx + 1 :])
    if body.startswith("\n"):
        body = body[1:]
    return body, end_idx + 1


def _segment_prose(body: str, *, start_line: int) -> list[_ProseSegment]:
    """Split prose into heading sections, lists, paragraphs, and code blocks."""
    lines = body.split("\n")
    segments: list[_ProseSegment] = []
    current_header: str | None = None
    current_header_level = 0

    idx = 0
    absolute_line = start_line

    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()

        fence_match = _FENCE_RE.match(stripped)
        if fence_match:
            fence_char = fence_match.group(1)[0]
            fence_len = len(fence_match.group(1))
            block_start = absolute_line
            block_lines = [line]
            idx += 1
            absolute_line += 1
            while idx < len(lines):
                block_lines.append(lines[idx])
                if lines[idx].strip().startswith(fence_char * fence_len):
                    break
                idx += 1
                absolute_line += 1
            block_end = block_start + len(block_lines) - 1
            segments.append(
                _ProseSegment(
                    content="\n".join(block_lines),
                    start_line=block_start,
                    end_line=block_end,
                    chunk_type=ChunkType.CODE_BLOCK,
                    symbol="code_block",
                    parent_header=current_header,
                )
            )
            idx += 1
            absolute_line += 1
            continue

        heading_match = _HEADING_RE.match(stripped)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            current_header = heading_text
            current_header_level = level
            header_type = _header_chunk_type(level)
            segments.append(
                _ProseSegment(
                    content=stripped,
                    start_line=absolute_line,
                    end_line=absolute_line,
                    chunk_type=header_type,
                    symbol=heading_text[:80],
                    parent_header=current_header,
                )
            )
            idx += 1
            absolute_line += 1
            continue

        if not stripped:
            idx += 1
            absolute_line += 1
            continue

        if _BULLET_RE.match(line):
            list_start = absolute_line
            list_lines: list[str] = []
            while idx < len(lines):
                current = lines[idx]
                if not current.strip():
                    break
                if _BULLET_RE.match(current) or (
                    list_lines and current.startswith(("  ", "\t"))
                ):
                    list_lines.append(current)
                    idx += 1
                    absolute_line += 1
                    continue
                break
            prefix = f"## {current_header}\n\n" if current_header else ""
            segments.append(
                _ProseSegment(
                    content=prefix + "\n".join(list_lines),
                    start_line=list_start,
                    end_line=list_start + len(list_lines) - 1,
                    chunk_type=ChunkType.BLOCK,
                    symbol=_symbol_from_content(list_lines[0]),
                    parent_header=current_header,
                )
            )
            continue

        para_start = absolute_line
        para_lines: list[str] = []
        while idx < len(lines):
            current = lines[idx]
            if not current.strip():
                break
            if (
                _HEADING_RE.match(current.strip())
                or _FENCE_RE.match(current.strip())
                or _BULLET_RE.match(current)
            ):
                break
            para_lines.append(current)
            idx += 1
            absolute_line += 1

        paragraph = " ".join(part.strip() for part in " ".join(para_lines).split())
        prefix = f"## {current_header}\n\n" if current_header else ""
        for piece in _split_paragraph_on_sentences(paragraph, max_chars=900):
            segments.append(
                _ProseSegment(
                    content=prefix + piece,
                    start_line=para_start,
                    end_line=para_start + len(para_lines) - 1 if para_lines else para_start,
                    chunk_type=ChunkType.PARAGRAPH,
                    symbol=_symbol_from_content(piece),
                    parent_header=current_header,
                )
            )

    return segments


def _split_paragraph_on_sentences(text: str, *, max_chars: int) -> list[str]:
    """Split long paragraphs on sentence boundaries, never mid-word."""
    if len(text) <= max_chars:
        return [text] if text.strip() else []

    sentences = _SENTENCE_SPLIT_RE.split(text)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                pieces.append(current)
            if len(sentence) <= max_chars:
                current = sentence
            else:
                words = sentence.split()
                chunk_words: list[str] = []
                for word in words:
                    test = " ".join(chunk_words + [word])
                    if len(test) <= max_chars:
                        chunk_words.append(word)
                    else:
                        if chunk_words:
                            pieces.append(" ".join(chunk_words))
                        chunk_words = [word]
                if chunk_words:
                    current = " ".join(chunk_words)
                else:
                    current = ""
    if current:
        pieces.append(current)
    return pieces


def _header_chunk_type(level: int) -> ChunkType:
    mapping = {
        1: ChunkType.HEADER_1,
        2: ChunkType.HEADER_2,
        3: ChunkType.HEADER_3,
        4: ChunkType.HEADER_4,
        5: ChunkType.HEADER_5,
        6: ChunkType.HEADER_6,
    }
    return mapping.get(level, ChunkType.HEADER_1)


def _symbol_from_content(content: str) -> str:
    cleaned = re.sub(r"\s+", " ", content.strip())
    if not cleaned:
        return "prose"
    return cleaned[:60]


def _segments_to_universal(segments: list[_ProseSegment]) -> list[UniversalChunk]:
    universal: list[UniversalChunk] = []
    for segment in segments:
        metadata: dict[str, object] = {
            "chunk_type_hint": segment.chunk_type.value,
            "parser": "prose",
        }
        if segment.parent_header:
            metadata["parent_header"] = segment.parent_header
        universal.append(
            UniversalChunk(
                concept=UniversalConcept.BLOCK,
                name=segment.symbol,
                content=segment.content,
                start_line=segment.start_line,
                end_line=segment.end_line,
                metadata=metadata,
                language_node_type=segment.chunk_type.value,
            )
        )
    return universal


def _universal_to_chunks(
    universal_chunks: list[UniversalChunk],
    *,
    source: str,
    file_path: Path | None,
    file_id: FileId,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    language = Language.MARKDOWN
    if file_path and file_path.suffix.lower() in {".txt", ".text"}:
        language = Language.TEXT

    for uc in universal_chunks:
        hint = str(uc.metadata.get("chunk_type_hint", "block"))
        chunk_type = ChunkType.from_string(hint)
        parent_header = uc.metadata.get("parent_header")
        if isinstance(parent_header, str):
            parent = parent_header
        else:
            parent = None

        start_byte = None
        end_byte = None
        if source:
            lines_before = source.split("\n")[: uc.start_line - 1]
            start_byte = ByteOffset(sum(len(item) + 1 for item in lines_before))
            end_byte = ByteOffset(start_byte + len(uc.content.encode("utf-8")))

        chunks.append(
            Chunk(
                symbol=uc.name,
                start_line=LineNumber(uc.start_line),
                end_line=LineNumber(uc.end_line),
                code=uc.content,
                chunk_type=chunk_type,
                file_id=file_id,
                language=language,
                file_path=FilePath(str(file_path)) if file_path else None,
                parent_header=parent,
                start_byte=start_byte,
                end_byte=end_byte,
                metadata=dict(uc.metadata),
            )
        )
    return chunks