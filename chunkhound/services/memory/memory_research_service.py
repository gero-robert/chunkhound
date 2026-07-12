"""Lightweight research pipeline for Memory MCP (no BFS/code exploration)."""

from __future__ import annotations

from typing import Any

from chunkhound.database_factory import DatabaseServices
from chunkhound.embeddings import EmbeddingManager
from chunkhound.llm_manager import LLMManager
from chunkhound.services.memory.metadata_filter import filter_chunks_by_task_context

_MEMORY_SYSTEM = (
    "You summarize agent long-term memory entries: user preferences, skills, "
    "lessons, failures, and architecture decisions. Be concise and actionable. "
    "Use bullet points. Cite sources as [filename] when helpful. "
    "Do not invent entries."
)


class MemoryResearchService:
    """Semantic retrieval + optional LLM synthesis for memory queries."""

    async def research(
        self,
        *,
        query: str,
        task_context: str,
        services: DatabaseServices,
        embedding_manager: EmbeddingManager,
        llm_manager: LLMManager | None,
    ) -> dict[str, Any]:
        results, _pagination = await services.search_service.search_semantic(
            query=query,
            page_size=12,
            offset=0,
        )
        ranked = filter_chunks_by_task_context(results, task_context)

        if llm_manager and llm_manager.is_configured():
            answer = await self._synthesize(query, task_context, ranked, llm_manager)
        else:
            answer = self._fallback_summary(ranked)

        return {
            "answer": answer,
            "sources": [
                {
                    "file_path": item.get("file_path") or item.get("path"),
                    "start_line": item.get("start_line"),
                    "end_line": item.get("end_line"),
                    "metadata": item.get("metadata"),
                }
                for item in ranked[:8]
            ],
            "tool": "memory_research",
        }

    async def _synthesize(
        self,
        query: str,
        task_context: str,
        chunks: list[dict[str, Any]],
        llm_manager: LLMManager,
    ) -> str:
        context_blocks: list[str] = []
        for index, chunk in enumerate(chunks[:10], start=1):
            metadata = chunk.get("metadata") or {}
            file_path = chunk.get("file_path") or chunk.get("path") or "unknown"
            content = chunk.get("content") or chunk.get("code") or ""
            context_blocks.append(
                f"[{index}] {file_path} metadata={metadata}\n{content}"
            )

        prompt = (
            f"User query: {query}\n"
            f"Task context: {task_context or '(none)'}\n\n"
            "Relevant memory chunks:\n"
            f"{chr(10).join(context_blocks) if context_blocks else '(no matches)'}\n\n"
            "Summarize the preferences, skills, lessons, failures, and "
            "decisions that matter for this task."
        )
        provider = llm_manager.get_synthesis_provider()
        response = await provider.complete(
            prompt=prompt,
            system=_MEMORY_SYSTEM,
            max_completion_tokens=2048,
        )
        return response.content.strip()

    def _fallback_summary(self, chunks: list[dict[str, Any]]) -> str:
        if not chunks:
            return "No matching memory entries found."

        lines = ["Top memory matches (no LLM configured for synthesis):"]
        for chunk in chunks[:5]:
            file_path = chunk.get("file_path") or chunk.get("path") or "unknown"
            content = (chunk.get("content") or chunk.get("code") or "").strip()
            preview = content[:400] + ("..." if len(content) > 400 else "")
            lines.append(f"- **{file_path}**: {preview}")
        return "\n".join(lines)
