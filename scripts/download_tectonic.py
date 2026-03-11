#!/usr/bin/env python3
"""Download the Tectonic LaTeX engine binary for the current platform.

Usage:
    python scripts/download_tectonic.py

Downloads to bin/tectonic (Linux/macOS) or bin/tectonic.exe (Windows).
Skips download if the binary already exists and is functional.
"""

from __future__ import annotations

import logging
import os
import platform
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Project root is parent of scripts/
REPO_ROOT = Path(__file__).parent.parent
BIN_DIR = REPO_ROOT / "bin"

# Tectonic GitHub releases API
RELEASES_API = "https://api.github.com/repos/tectonic-typesetting/tectonic/releases/latest"


def _get_asset_name() -> str:
    """Return the correct Tectonic release asset filename for this platform."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    # Normalise architecture names
    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("aarch64", "arm64"):
        arch = "aarch64"
    else:
        raise RuntimeError(f"Unsupported architecture: {machine}")

    if system == "linux":
        return f"tectonic-{arch}-unknown-linux-musl.tar.gz"
    if system == "darwin":
        if arch == "aarch64":
            return f"tectonic-{arch}-apple-darwin.tar.gz"
        return f"tectonic-{arch}-apple-darwin.tar.gz"
    if system == "windows":
        return f"tectonic-{arch}-pc-windows-msvc.zip"

    raise RuntimeError(f"Unsupported OS: {system}")


def _binary_path() -> Path:
    """Return the expected path of the tectonic binary."""
    if platform.system().lower() == "windows":
        return BIN_DIR / "tectonic.exe"
    return BIN_DIR / "tectonic"


def _binary_works(path: Path) -> bool:
    """Return True if the binary at *path* is executable and responds to --version."""
    if not path.exists():
        return False
    try:
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _get_download_url(asset_name: str) -> str:
    """Fetch the latest release from GitHub and return the asset download URL."""
    import json

    logger.info("Fetching latest Tectonic release info from GitHub…")
    req = urllib.request.Request(
        RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "jobpilot-installer/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    for asset in data.get("assets", []):
        if asset["name"] == asset_name:
            url = asset["browser_download_url"]
            logger.info("Found asset: %s", url)
            return url

    # Try a fallback with partial match (e.g. different glibc variant)
    asset_base = asset_name.replace(".tar.gz", "").replace(".zip", "")
    for asset in data.get("assets", []):
        if asset_base.split("-")[0] in asset["name"] and (
            ".tar.gz" in asset["name"] or ".zip" in asset["name"]
        ):
            url = asset["browser_download_url"]
            logger.warning("Exact asset not found; using fallback: %s", url)
            return url

    tag = data.get("tag_name", "unknown")
    raise RuntimeError(
        f"Asset '{asset_name}' not found in release {tag}.\n"
        f"Available: {[a['name'] for a in data.get('assets', [])]}"
    )


def _download_and_extract(url: str, dest: Path) -> None:
    """Download the archive from *url* and extract the tectonic binary to *dest*."""
    import io
    import tarfile
    import zipfile

    logger.info("Downloading %s …", url)
    with urllib.request.urlopen(url, timeout=120) as resp:
        data = resp.read()
    logger.info("Downloaded %.1f MB", len(data) / 1024 / 1024)

    dest.parent.mkdir(parents=True, exist_ok=True)

    if url.endswith(".tar.gz"):
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            # The tectonic binary is typically the only file in the archive
            for member in tf.getmembers():
                if member.name.endswith("tectonic") or member.name == "tectonic":
                    member.name = dest.name  # rename to just 'tectonic'
                    tf.extract(member, path=dest.parent)
                    break
            else:
                # Fallback: extract first executable-looking file
                for member in tf.getmembers():
                    if member.isfile() and "tectonic" in member.name.lower():
                        member.name = dest.name
                        tf.extract(member, path=dest.parent)
                        break
                else:
                    raise RuntimeError("tectonic binary not found in archive")

    elif url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if "tectonic" in name.lower() and (
                    name.endswith(".exe") or not "." in name.split("/")[-1]
                ):
                    with zf.open(name) as src, open(dest, "wb") as out:
                        out.write(src.read())
                    break
            else:
                raise RuntimeError("tectonic binary not found in zip archive")
    else:
        raise RuntimeError(f"Unknown archive format: {url}")

    # Make executable on Unix
    if platform.system().lower() != "windows":
        current = dest.stat().st_mode
        dest.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    logger.info("Extracted to %s", dest)


def main() -> int:
    binary = _binary_path()

    # Idempotent: skip if already working
    if _binary_works(binary):
        result = subprocess.run([str(binary), "--version"], capture_output=True, text=True)
        logger.info("Tectonic already installed: %s", result.stdout.strip())
        return 0

    logger.info("Tectonic not found or not working. Downloading…")

    try:
        asset_name = _get_asset_name()
    except RuntimeError as exc:
        logger.error("Platform detection failed: %s", exc)
        return 1

    try:
        url = _get_download_url(asset_name)
    except Exception as exc:
        logger.error("Failed to fetch release info: %s", exc)
        logger.error("You can manually download Tectonic from:")
        logger.error("  https://github.com/tectonic-typesetting/tectonic/releases")
        logger.error("Place the binary at: %s", binary)
        return 1

    try:
        _download_and_extract(url, binary)
    except Exception as exc:
        logger.error("Download/extract failed: %s", exc)
        return 1

    if not _binary_works(binary):
        logger.error("Binary downloaded but doesn't work: %s", binary)
        return 1

    result = subprocess.run([str(binary), "--version"], capture_output=True, text=True)
    logger.info("✅ Tectonic installed successfully: %s", result.stdout.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
