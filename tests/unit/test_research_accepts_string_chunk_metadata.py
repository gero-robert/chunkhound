"""Research keeps going when Lance chunk metadata is still a JSON string."""

from __future__ import annotations

import json

from chunkhound.providers.database.lancedb_provider import _deserialize_metadata
from chunkhound.services.research.shared.chunk_range import expand_to_natural_boundaries
from chunkhound.services.research.shared.evidence_ledger.ledger import EvidenceLedger


def test_deserialize_metadata_accepts_storage_shapes() -> None:
    raw = '{"kind": "variable"}'
    assert _deserialize_metadata(raw) == {"kind": "variable"}
    assert _deserialize_metadata({"kind": "function"}) == {"kind": "function"}
    assert _deserialize_metadata(json.dumps(json.dumps({"kind": "class"}))) == {
        "kind": "class"
    }
    assert _deserialize_metadata(None) == {}
    assert _deserialize_metadata("") == {}
    assert _deserialize_metadata("not-json") == {}
    assert _deserialize_metadata("[1, 2]") == {}
    assert _deserialize_metadata("null") == {}


def test_evidence_ledger_reads_constants_from_string_metadata() -> None:
    chunks = [
        {
            "file_path": "a.py",
            "metadata": '{"constants": [{"name": "FOO", "value": "1"}]}',
        },
        {
            "file_path": "b.py",
            "metadata": {"constants": [{"name": "BAR", "value": "2"}]},
        },
        {"file_path": "c.py", "metadata": "not-json"},
        {
            "file_path": "d.py",
            "metadata": json.dumps(
                json.dumps({"constants": [{"name": "BAZ", "value": "3"}]})
            ),
        },
        {"file_path": "e.py", "metadata": {"constants": ["nope"]}},
    ]
    ledger = EvidenceLedger.from_chunks(chunks)
    names = {entry.name for entry in ledger.constants.values()}
    assert names == {"FOO", "BAR", "BAZ"}


def test_boundary_expansion_reads_kind_from_string_metadata() -> None:
    lines = [f"line {n}" for n in range(1, 21)]
    start, end = expand_to_natural_boundaries(
        lines,
        10,
        12,
        {"metadata": '{"kind": "function"}'},
        "sample.py",
    )
    assert (start, end) == (7, 15)
