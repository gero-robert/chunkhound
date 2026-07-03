"""YAML frontmatter extraction for memory markdown documents."""

from __future__ import annotations

from typing import Any

import yaml

MEMORY_TYPES = frozenset({"user_preference", "skill", "lesson", "failure"})


class FrontmatterExtractor:
    """Parse YAML frontmatter from the top of markdown memory entries."""

    def extract(self, content: str) -> tuple[str, dict[str, Any]]:
        """Return body text and normalized frontmatter fields."""
        if not content.startswith("---"):
            return content, {}

        lines = content.split("\n")
        if len(lines) < 2:
            return content, {}

        end_idx: int | None = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                end_idx = idx
                break

        if end_idx is None:
            return content, {}

        yaml_block = "\n".join(lines[1:end_idx])
        body = "\n".join(lines[end_idx + 1 :])
        if body.startswith("\n"):
            body = body[1:]

        try:
            parsed = yaml.safe_load(yaml_block)
        except yaml.YAMLError:
            return body, {}

        if not isinstance(parsed, dict):
            return body, {}

        return body, self.normalize_fields(parsed)

    def normalize_fields(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize frontmatter to the memory schema."""
        fields: dict[str, Any] = {}

        entry_type = raw.get("type")
        if isinstance(entry_type, str) and entry_type in MEMORY_TYPES:
            fields["type"] = entry_type

        applies_to = raw.get("applies_to")
        if isinstance(applies_to, str) and applies_to.strip():
            fields["applies_to"] = applies_to.strip()

        learned_at = raw.get("learned_at")
        if learned_at is not None:
            fields["learned_at"] = str(learned_at)

        tags = raw.get("tags")
        if isinstance(tags, list):
            normalized_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
            if normalized_tags:
                fields["tags"] = normalized_tags
        elif isinstance(tags, str) and tags.strip():
            fields["tags"] = [part.strip() for part in tags.split(",") if part.strip()]

        confidence = raw.get("confidence")
        if isinstance(confidence, str) and confidence.strip():
            fields["confidence"] = confidence.strip().lower()

        return fields