"""Regex, semantic search, and code_research on a Lance database written by
the Rust pipeline.

Hits are compared with fork ``main`` indexing the same fixture. Research
must also surface the chunk metadata stored on this branch.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("lancedb")

FIXTURE = (
    "def widget_entry(token):\n"
    "    '''Return the stored widget token.'''\n"
    "    return token\n"
)


def _index(root: Path, db_path: Path, embedder):
    from chunkhound.core.config.database_config import DatabaseConfig
    from chunkhound.core.types.common import Language
    from chunkhound.embeddings import EmbeddingManager
    from chunkhound.parsers.parser_factory import create_parser_for_language
    from chunkhound.providers.database.lancedb_provider import LanceDBProvider
    from chunkhound.services.indexing_coordinator import IndexingCoordinator

    manager = EmbeddingManager()
    manager.register_provider(embedder, set_default=True)
    config = DatabaseConfig(path=db_path, provider="lancedb")
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
    return provider, coordinator


def _hits(provider, embedder) -> set[tuple[str, str, str]]:
    regex_rows, _ = provider.search_regex("widget_entry")
    vector = asyncio.run(embedder.embed_single("widget_entry"))
    semantic_rows, _ = provider.search_semantic(
        vector, embedder.name, embedder.model, page_size=10
    )
    found = set()
    for row in list(regex_rows) + list(semantic_rows):
        meta = row.get("metadata") or {}
        found.add(
            (
                str(row.get("file_path") or ""),
                str(row.get("symbol") or ""),
                str(meta.get("kind") or ""),
            )
        )
    return found


def _fork_main_hits(
    repo: Path, fixture_root: Path, tmp_path: Path
) -> set[tuple[str, str, str]]:
    """Index the fixture with git ref ``main`` and return file, symbol, kind."""
    worktree = tmp_path / "fork-main"
    script = tmp_path / "fork_main_oracle.py"
    script.write_text(
        "import asyncio, json, os, sys\n"
        "from pathlib import Path\n"
        "os.environ['CHUNKHOUND_USE_RUST'] = '0'\n"
        "from chunkhound.core.config.database_config import DatabaseConfig\n"
        "from chunkhound.core.types.common import Language\n"
        "from chunkhound.embeddings import EmbeddingManager\n"
        "from chunkhound.parsers.parser_factory import create_parser_for_language\n"
        "from chunkhound.providers.database.lancedb_provider import LanceDBProvider\n"
        "from chunkhound.services.indexing_coordinator import IndexingCoordinator\n"
        "from tests.fixtures.fake_providers import ConstantEmbeddingProvider\n"
        "import chunkhound\n"
        "root = Path(sys.argv[1])\n"
        "db_path = Path(sys.argv[2])\n"
        "embedder = ConstantEmbeddingProvider(dims=8)\n"
        "manager = EmbeddingManager()\n"
        "manager.register_provider(embedder, set_default=True)\n"
        "config = DatabaseConfig(path=db_path, provider='lancedb')\n"
        "provider = LanceDBProvider(\n"
"    str(config.get_db_path()), base_directory=root,\n"
"    embedding_manager=manager, config=config,\n"
")\n"
        "provider.connect()\n"
        "parser = create_parser_for_language(Language.PYTHON)\n"
        "coordinator = IndexingCoordinator(\n"
"    provider, root, embedder, {Language.PYTHON: parser}\n"
")\n"
        "async def run():\n"
        "    await coordinator.process_directory(root, patterns=['**/*.py'])\n"
        "    if hasattr(coordinator, 'generate_missing_embeddings'):\n"
        "        await coordinator.generate_missing_embeddings()\n"
        "    regex_rows, _info = provider.search_regex('widget_entry')\n"
        "    vector = await embedder.embed_single('widget_entry')\n"
        "    semantic_rows, _info = provider.search_semantic(\n"
"        vector, embedder.name, embedder.model, page_size=10\n"
"    )\n"
        "    found = []\n"
        "    for row in list(regex_rows) + list(semantic_rows):\n"
        "        meta = row.get('metadata') or {}\n"
        "        found.append([\n"
"            str(row.get('file_path') or ''),\n"
"            str(row.get('symbol') or ''),\n"
"            str(meta.get('kind') or ''),\n"
"        ])\n"
        "    provider.disconnect()\n"
        "    located = str(Path(chunkhound.__file__).resolve())\n"
"    print(json.dumps({'chunkhound': located, 'hits': found}))\n"
        "asyncio.run(run())\n",
        encoding="utf-8",
    )
    add = subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree), "main"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert add.returncode == 0, add.stderr
    env = os.environ.copy()
    env["PYTHONPATH"] = str(worktree)
    env["CHUNKHOUND_USE_RUST"] = "0"
    env["CHUNKHOUND_NO_PROMPTS"] = "1"
    try:
        proc = subprocess.run(
            [sys.executable, str(script), str(fixture_root), str(tmp_path / "fork-db")],
            cwd=worktree,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert (
        str(worktree) in payload["chunkhound"].replace("/", "\\")
        or str(worktree) in payload["chunkhound"]
    )
    return {tuple(hit) for hit in payload["hits"]}


def test_lance_rust_search_matches_fork_main_and_research_sees_metadata(
    tmp_path, monkeypatch
):
    from chunkhound.database_factory import DatabaseServices
    from chunkhound.embeddings import EmbeddingManager
    from chunkhound.llm_manager import LLMManager
    from chunkhound.mcp_server.tools import deep_research_impl
    from chunkhound.services.embedding_service import EmbeddingService
    from chunkhound.services.search_service import SearchService
    from tests.fixtures.fake_providers import (
        ConstantEmbeddingProvider,
        FakeLLMProvider,
    )

    root = tmp_path / "repo"
    root.mkdir()
    (root / "widget.py").write_text(FIXTURE, encoding="utf-8")
    embedder = ConstantEmbeddingProvider(dims=8)

    monkeypatch.setenv("CHUNKHOUND_USE_RUST", "1")
    rust_provider, rust_coordinator = _index(root, tmp_path / "rust-db", embedder)
    rust_result = asyncio.run(
        rust_coordinator.process_directory(root, patterns=["**/*.py"])
    )
    assert rust_result["status"] == "success", rust_result
    assert rust_result["pipeline"] == "rust"
    rust_hits = _hits(rust_provider, embedder)
    repo = Path(__file__).resolve().parents[2]
    main_hits = _fork_main_hits(repo, root, tmp_path)

    assert ("widget.py", "widget_entry", "function") in rust_hits
    assert ("widget.py", "widget_entry", "function") in main_hits
    assert rust_hits == main_hits

    class _EchoMetadata(FakeLLMProvider):
        async def complete(
            self,
            prompt,
            system=None,
            max_completion_tokens=4096,
            timeout=None,
        ):
            response = await super().complete(
                prompt, system, max_completion_tokens, timeout
            )
            if "metadata:" in prompt:
                response.content = prompt
            return response

    manager = EmbeddingManager()
    manager.register_provider(embedder, set_default=True)

    def _fake_create(self, provider_config):
        return _EchoMetadata()

    original = LLMManager._create_provider
    LLMManager._create_provider = _fake_create  # type: ignore[assignment]
    try:
        llm = LLMManager(
            {"provider": "fake", "model": "fake-gpt"},
            {"provider": "fake", "model": "fake-gpt"},
        )
    finally:
        LLMManager._create_provider = original  # type: ignore[assignment]

    services = DatabaseServices(
        provider=rust_provider,
        indexing_coordinator=rust_coordinator,
        search_service=SearchService(rust_provider, embedder),
        embedding_service=EmbeddingService(rust_provider, embedder),
    )
    import chunkhound.services.research.shared.models as models_mod

    original_threshold = models_mod.RELEVANCE_THRESHOLD
    models_mod.RELEVANCE_THRESHOLD = None
    try:
        researched = asyncio.run(
            deep_research_impl(
                services=services,
                embedding_manager=manager,
                llm_manager=llm,
                query="where is widget_entry",
                progress=None,
            )
        )
    finally:
        models_mod.RELEVANCE_THRESHOLD = original_threshold
        rust_provider.disconnect()

    answer = researched["answer"]
    assert "widget.py" in answer
    assert "widget_entry" in answer
    assert '"kind": "function"' in answer
