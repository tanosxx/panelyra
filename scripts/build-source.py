#!/usr/bin/env python3
"""Build a deterministic Panelyra source archive from an explicit allowlist.

Use --check to audit inputs without creating an archive. No Git repository,
network access, signing material, installed SDK or connected tablet is needed.
"""

import argparse
import gzip
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    "README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "AGENTS.md",
    "pyproject.toml", ".gitignore", "panelyra", "usb-display", "usb-display-launcher",
    "announcements.json",
}
SOURCE_DIRS = {
    "usbdisplay", "android", "desktop", "scripts", "tests", "docs", "packaging", ".github",
}
IGNORED_DIRS = {
    ".tools", ".git", ".agents", ".codex", "backups", "out", "__pycache__",
    "build", ".gradle", ".venv", "node_modules", ".pytest_cache", ".mypy_cache",
}
IGNORED_FILES = {"local.properties", ".DS_Store"}
SOURCE_SUFFIXES = {
    ".py", ".sh", ".java", ".xml", ".gradle", ".properties", ".md", ".txt",
    ".toml", ".yaml", ".yml", ".json", ".js", ".svg", ".png", ".css", ".desktop", ".in",
}
FORBIDDEN_SUFFIXES = {
    ".keystore", ".jks", ".p12", ".pfx", ".key", ".pem", ".apk", ".aab", ".dex",
    ".class", ".jar", ".h264", ".mp4", ".mkv", ".webm", ".mov", ".avi", ".wav",
    ".mp3", ".ogg", ".zip", ".gz", ".deb", ".snap",
}
FORBIDDEN_NAMES = {
    ".netrc", ".npmrc", "id_rsa", "id_ed25519", "credentials.json", "secrets.json",
    "key.properties", "keystore.properties", "signing.properties",
}
ENTRY_POINTS = {"panelyra", "usb-display", "usb-display-launcher"}
PRIVATE_KEY = re.compile(rb"(?m)^-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")


def collect(root=ROOT):
    """Return sorted relative source paths; fail on unexpected risky inputs."""
    root = Path(root)
    paths = []

    def visit(relative):
        path = root / relative
        if path.is_symlink():
            raise ValueError(f"Symlinks are excluded; review this source path: {relative}")
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            if path.name in IGNORED_DIRS:
                return
            for child in sorted(path.iterdir(), key=lambda entry: entry.name):
                visit(relative / child.name)
            return
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Only regular source files are allowed: {relative}")
        if path.name in IGNORED_FILES or path.suffix in {".pyc", ".pyo"}:
            return
        lowered = path.name.lower()
        if lowered.startswith(".env") or lowered in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise ValueError(f"Sensitive or binary build material in a source directory: {relative}")
        if relative.parts[0] not in ROOT_FILES and path.suffix not in SOURCE_SUFFIXES and path.name != ".gitignore":
            raise ValueError(f"Unlisted source file type; review the allowlist: {relative}")
        paths.append(relative)

    for name in sorted(ROOT_FILES):
        path = root / name
        if not path.exists() and not path.is_symlink():
            raise ValueError(f"Required source file is missing: {name}")
        if not path.is_file() and not path.is_symlink():
            raise ValueError(f"Required source entry must be a file: {name}")
        visit(Path(name))
    for name in sorted(SOURCE_DIRS):
        path = root / name
        if not path.exists() and not path.is_symlink():
            raise ValueError(f"Required source directory is missing: {name}")
        if not path.is_dir() and not path.is_symlink():
            raise ValueError(f"Required source entry must be a directory: {name}")
        visit(Path(name))
    return sorted(paths, key=lambda path: path.as_posix())


def read_source(root, relative):
    path = Path(root) / relative
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"Source changed into a non-regular file: {relative}")
        content = stream.read()
    if PRIVATE_KEY.search(content):
        raise ValueError(f"Private key material detected in source file: {relative}")
    return content


def source_version(root):
    source = (Path(root) / "usbdisplay/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*[\'"]([0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9.+-]*)?)[\'"]', source, re.M)
    if not match:
        raise ValueError("Could not read a safe release version from usbdisplay/__init__.py")
    return match.group(1)


def file_mode(relative):
    if relative.as_posix() in ENTRY_POINTS or relative.suffix == ".sh":
        return 0o755
    if relative.suffix == ".py" and "scripts" in relative.parts[:-1]:
        return 0o755
    return 0o644


def write_archive(destination, root, files, version):
    prefix = f"panelyra-{version}"
    with Path(destination).open("wb") as output:
        with gzip.GzipFile(fileobj=output, filename="", mode="wb", mtime=0, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                directories = {PurePosixPath(prefix)}
                for relative in files:
                    parent = PurePosixPath(prefix) / relative.parent.as_posix()
                    directories.update([parent, *parent.parents])
                directories.discard(PurePosixPath("."))
                for directory in sorted(directories, key=lambda path: path.as_posix()):
                    entry = tarfile.TarInfo(directory.as_posix())
                    entry.type = tarfile.DIRTYPE
                    entry.mode = 0o755
                    archive.addfile(entry)
                for relative in files:
                    content = read_source(root, relative)
                    entry = tarfile.TarInfo(f"{prefix}/{relative.as_posix()}")
                    # TarInfo defaults uid/gid/mtime to zero and uname/gname to empty.
                    entry.mode = file_mode(relative)
                    entry.size = len(content)
                    archive.addfile(entry, io.BytesIO(content))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "out")
    parser.add_argument("--check", action="store_true", help="audit inputs without creating any output")
    parser.add_argument("--list", action="store_true", help="list audited paths without creating output")
    args = parser.parse_args(argv)
    try:
        files = collect()
        version = source_version(ROOT)
        for relative in files:
            read_source(ROOT, relative)
        if args.check or args.list:
            if args.list:
                for relative in files:
                    print(relative.as_posix())
            print(f"Source audit passed: {len(files)} allowlisted files for Panelyra {version}; no archive created.")
            return 0
        output = args.output_dir.resolve()
        output.mkdir(parents=True, exist_ok=True)
        archive = output / f"panelyra-{version}-source.tar.gz"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=output, prefix=".panelyra-source-", delete=False) as stream:
                temporary = Path(stream.name)
            write_archive(temporary, ROOT, files, version)
            temporary.chmod(0o644)
            temporary.replace(archive)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        checksum = archive.with_suffix(archive.suffix + ".sha256")
        checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
        checksum.chmod(0o644)
        print(f"Built: {archive}")
        print(f"SHA256: {digest}")
        print(f"Included {len(files)} allowlisted files; no Git metadata, APKs, keys, tools or backups.")
        return 0
    except (OSError, ValueError, tarfile.TarError) as error:
        parser.exit(1, f"Source archive failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
