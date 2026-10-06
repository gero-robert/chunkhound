"""A table written by the native lance crate opens in installed lancedb.

A second process opens the same directory and reads the row. Both launches
must report the same path.
"""

import subprocess
import sys

import pytest

pytest.importorskip("lancedb")


def test_rust_store_probe_opens_in_a_new_process(tmp_path):
    import chunkhound_native

    directory = tmp_path / "probe.lancedb"
    chunkhound_native.write_lance_format_probe(str(directory))

    script = (
        "import lancedb, sys\n"
        "db = lancedb.connect(sys.argv[1])\n"
        "rows = db.open_table('files').to_arrow().to_pylist()\n"
        "assert rows == [{'id': 1, 'path': 'main.py'}], rows\n"
        "print(rows[0]['path'])\n"
    )
    paths = []
    for _ in range(2):
        proc = subprocess.run(
            [sys.executable, "-c", script, str(directory)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        paths.append(proc.stdout.strip())
    assert paths == ["main.py", "main.py"]
