"""A different worker kernel must select its own libraries and reject stubs."""
from pathlib import Path
import tempfile
from worker_driver import prepare


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    libraries = root / "libraries"
    libraries.mkdir()
    names = ["libcuda", "libnvidia-ml", "libGLX_nvidia", "libEGL_nvidia", "libnvidia-ptxjitcompiler"]
    for name in names:
        (libraries / (name + ".so.535.261.03")).write_bytes(bytes([127, 69, 76, 70]) + b"fixture")
        (libraries / (name + ".so.535.183.06")).write_text("old driver stub")
    record = prepare("NVIDIA Kernel Module  535.261.03", libraries, root / "selected")
    assert record["kernel_version"] == "535.261.03"
    for alias, target in record["libraries"].items():
        assert target.endswith("535.261.03")
        assert (root / "selected" / alias).resolve() == Path(target)
    (libraries / "libnvidia-ml.so.535.261.03").write_text("current driver stub")
    try:
        prepare("NVIDIA Kernel Module  535.261.03", libraries, root / "rejected")
    except ValueError as error:
        assert "not ELF" in str(error)
    else:
        raise AssertionError("Malformed current driver accepted")
    assert not (root / "rejected").exists()
    try:
        prepare("unrecognized kernel", libraries, root / "unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown kernel accepted")
print("Kernel-specific selection and fail-fast malformed-library checks passed; loading requires real worker libraries")
