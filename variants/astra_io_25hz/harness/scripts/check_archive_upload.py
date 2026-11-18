"""A failed upload cannot produce verified evidence or destroy its source."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from upload_archive import upload, digest

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    archive = root / "evidence.tar.gz"
    archive.write_bytes(b"immutable evidence")
    expected = digest(archive)
    def denied(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="quota exceeded")
    try:
        upload(archive, expected, "hdfs://storage.example.invalid/REDACTED", root / "failed", denied)
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("Upload failure swallowed")
    failure = json.loads((root / "failed/status.json").read_text())
    assert failure["status"] == "failed" and not failure["verified_readback"]
    assert digest(archive) == expected
    def success(command, **kwargs):
        if command[2] == "-get":
            shutil.copyfile(archive, command[-1])
    result = upload(archive, expected, "hdfs://storage.example.invalid/REDACTED", root / "ok", success)
    assert result["verified_readback"]
    def corrupt(command, **kwargs):
        if command[2] == "-get":
            Path(command[-1]).write_bytes(b"corrupt")
    try:
        upload(archive, expected, "hdfs://storage.example.invalid/REDACTED", root / "corrupt", corrupt)
    except ValueError:
        pass
    else:
        raise AssertionError("Corrupted readback accepted")
    assert not json.loads((root / "corrupt/status.json").read_text())["verified_readback"]
print("Failed transport and corrupt readback retained; source unchanged; valid readback verified")
