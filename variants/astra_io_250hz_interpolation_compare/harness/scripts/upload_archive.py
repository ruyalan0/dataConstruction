"""Upload an existing immutable archive once, preserving failure and readback evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from datetime import datetime, timezone


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def upload(archive, expected, uri, attempt, execute=subprocess.run):
    attempt.mkdir(parents=True, exist_ok=False)
    record = {"archive": str(archive), "expected_sha256": expected, "uri": uri,
              "started_at": datetime.now(timezone.utc).isoformat(), "verified_readback": False}
    def save():
        temporary = attempt / "status.tmp"
        temporary.write_text(json.dumps(record, indent=2))
        temporary.replace(attempt / "status.json")
    save()
    try:
        if digest(archive) != expected:
            raise ValueError("Local archive checksum mismatch")
        record["bytes"] = archive.stat().st_size
        with (attempt / "transport.log").open("w") as log:
            execute(["hdfs", "dfs", "-put", str(archive), uri], check=True, stdout=log, stderr=subprocess.STDOUT)
            with tempfile.TemporaryDirectory(prefix="rsi-readback-") as directory:
                copy = Path(directory) / archive.name
                execute(["hdfs", "dfs", "-get", uri, str(copy)], check=True, stdout=log, stderr=subprocess.STDOUT)
                if digest(copy) != expected:
                    raise ValueError("HDFS readback checksum mismatch")
        record.update(status="verified", verified_readback=True)
    except Exception as error:
        record.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--uri", required=True)
    parser.add_argument("--attempt", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(upload(args.archive.resolve(), args.sha256, args.uri, args.attempt)))
