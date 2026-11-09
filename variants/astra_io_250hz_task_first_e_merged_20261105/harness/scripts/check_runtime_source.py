"""Exercise source enforcement with real temporary repositories, without GPU imports."""

import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from runtime_source import parse_required_submodules, verify_runtime_source, write_runtime_provenance
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from egg_harness.runner import runtime_source_arguments


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE).strip()


def create_repo(root):
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Gabrielinger")
    git(root, "config", "user.email", "author@example.invalid")
    (root / "source.py").write_text("value = 1\n")
    git(root, "add", "source.py")
    git(root, "commit", "-qm", "Initial source")
    return git(root, "rev-parse", "HEAD")


def rejected(call, contains):
    try:
        call()
    except ValueError as error:
        assert contains in str(error), str(error)
    else:
        raise AssertionError(f"Accepted invalid source: {contains}")


with tempfile.TemporaryDirectory() as temporary:
    base = Path(temporary).resolve()
    dependency = base / "dependency"
    dependency_sha = create_repo(dependency)
    root = base / "runtime"
    create_repo(root)
    git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(dependency), "XPolicyLab")
    git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(dependency), "InstalledOnly")
    git(root, "commit", "-qam", "Pin runtime submodules")
    revision = git(root, "rev-parse", "HEAD")
    pins = {"XPolicyLab": dependency_sha}
    arguments=runtime_source_arguments({'robodojo_revision':revision, 'robodojo_required_submodules':pins})
    assert arguments==['--robodojo-revision',revision,'--required-submodule','XPolicyLab='+dependency_sha]
    assert runtime_source_arguments({})==[]
    rejected(lambda: runtime_source_arguments({'robodojo_required_submodules':pins}), 'pinned runtime')
    rejected(lambda: runtime_source_arguments({'robodojo_revision':revision[:7]}), 'full lowercase')
    git(root, "submodule", "deinit", "-f", "InstalledOnly")
    report = verify_runtime_source(root, revision, pins)
    assert report["required_submodules"]["XPolicyLab"]["commit"] == dependency_sha
    write_runtime_provenance(base, report)
    assert json.loads((base / "runtime-source-provenance.json").read_text()) == report

    rejected(lambda: verify_runtime_source(base / "missing", revision, pins), "Missing runtime")
    rejected(lambda: verify_runtime_source(root, revision[:7], pins), "full lowercase")
    rejected(lambda: verify_runtime_source(root, "0" * 40, pins), "commit mismatch")
    for path in ("../escape", "/absolute", "./XPolicyLab", "XPolicyLab/", "."):
        rejected(lambda: parse_required_submodules([f"{path}={dependency_sha}"]), "Invalid required")
    rejected(lambda: parse_required_submodules([f"XPolicyLab={dependency_sha}"] * 2), "unique")
    rejected(lambda: verify_runtime_source(root, revision, {"XPolicyLab": "0" * 40}), "gitlink mismatch")
    rejected(lambda: verify_runtime_source(root, revision, {"Missing": dependency_sha}), "gitlink mismatch")

    for source in (root, root / "XPolicyLab"):
        file = source / "source.py"
        original = file.read_text()
        file.write_text("value = 2\n")
        rejected(lambda: verify_runtime_source(root, revision, pins), "Dirty runtime")
        git(source, "add", "source.py")
        rejected(lambda: verify_runtime_source(root, revision, pins), "Dirty runtime")
        file.write_text(original)
        git(source, "add", "source.py")
        untracked = source / "unexpected.py"
        untracked.write_text("value = 3\n")
        rejected(lambda: verify_runtime_source(root, revision, pins), "Dirty runtime")
        untracked.unlink()

    child = root / "XPolicyLab"
    git(child, "-c", "user.name=Gabrielinger", "-c", "user.email=author@example.invalid", "commit", "--allow-empty", "-qm", "Wrong revision")
    rejected(lambda: verify_runtime_source(root, revision, pins), "commit mismatch")
    git(child, "checkout", "-q", dependency_sha)
    git(root, "submodule", "deinit", "-f", "XPolicyLab")
    rejected(lambda: verify_runtime_source(root, revision, pins), "not an initialized")

    # Execute the real startup entrypoint with an invalid pin. It must exit
    # before even attempting a Torch import, and retain the failure status.
    serve = Path(__file__).with_name("serve.py").resolve()
    output = base / "serve-output"
    result = subprocess.run(
        [sys.executable, str(serve), "--robodojo-root", str(root), "--assets", str(base),
         "--output", str(output), "--robodojo-revision", "0" * 40],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1, result
    assert "commit mismatch" in (output / "error.txt").read_text()
    assert json.loads((output / "exit-status.json").read_text()) == {"exit_code": 1}
    assert not (output / "runtime-source-provenance.json").exists()
    tree = ast.parse(serve.read_text())
    startup = next(node for node in tree.body if isinstance(node, ast.Try))
    check = next(node for node in ast.walk(startup) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "verify_runtime_source")
    torch_import = next(node for node in ast.walk(startup) if isinstance(node, ast.Import) and any(alias.name == "torch" for alias in node.names))
    assert check.lineno < torch_import.lineno

print("Runtime source pins reject missing, dirty, uninitialized and mismatched Git sources before GPU initialization")
