"""Build a relocatable Windows Python/Node runtime from the checked-out wheel."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def prepare(wheel: Path, destination: Path) -> None:
    if sys.platform != "win32":
        raise SystemExit("Prepare the Windows runtime on a Windows build runner.")
    if destination.exists():
        raise SystemExit(f"Refusing to overwrite an existing runtime: {destination}")
    python_root = destination / "python"
    python_root.mkdir(parents=True)
    source = Path(sys.base_prefix)
    for path in source.iterdir():
        if path.is_file() and (path.suffix.lower() in {".exe", ".dll", ".zip"} or path.name == "LICENSE.txt"):
            shutil.copy2(path, python_root / path.name)
    ignored = shutil.ignore_patterns("__pycache__", "*.pyc", "site-packages", "test", "tests", "idlelib", "tkinter", "turtledemo")
    for name in ["Lib", "DLLs"]:
        shutil.copytree(source / name, python_root / name, ignore=ignored)
    site_packages = python_root / "Lib" / "site-packages"
    subprocess.run([
        sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
        "--only-binary=:all:", "--no-compile", "--target", str(site_packages), str(wheel.resolve()),
    ], check=True)
    version_tag = f"{sys.version_info.major}{sys.version_info.minor}"
    (python_root / f"python{version_tag}._pth").write_text(
        f"python{version_tag}.zip\n.\nLib\nDLLs\nLib/site-packages\nimport site\n", encoding="utf-8"
    )
    node_source = Path(shutil.which("node") or "")
    if not node_source.is_file():
        raise SystemExit("Node.js was not found on the build runner.")
    node_root = destination / "node"
    node_root.mkdir()
    shutil.copy2(node_source, node_root / "node.exe")
    node_version = subprocess.check_output([str(node_source), "--version"], text=True).strip()
    with urlopen(f"https://raw.githubusercontent.com/nodejs/node/{node_version}/LICENSE", timeout=60) as response:
        (node_root / "LICENSE").write_bytes(response.read())
    shutil.copy2(ROOT / "desktop/runtime/runner.py", destination / "runner.py")
    shutil.copy2(ROOT / "LICENSE", destination / "LICENSE-DeepTutor")
    manifest = {
        "source_commit": os.environ.get("GITHUB_SHA", "local"),
        "python": sys.version, "node": node_version,
        "packages": sorted(
            ({"name": d.metadata["Name"], "version": d.version}
             for d in importlib.metadata.distributions(path=[str(site_packages)])),
            key=lambda item: item["name"].lower(),
        ),
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # Use the copied interpreter so absolute build-runner paths cannot mask a broken bundle.
    subprocess.run([str(python_root / "python.exe"), "-I", "-c",
        "import deeptutor, uvicorn, win32api, faiss; from deeptutor.runtime.launcher import start; print('Portable imports OK')"],
        cwd=destination, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--destination", type=Path, default=ROOT / "desktop/src-tauri/runtime")
    options = parser.parse_args()
    prepare(options.wheel, options.destination)
