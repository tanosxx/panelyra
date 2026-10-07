#!/usr/bin/env python3
"""Build a local Panelyra .deb without root, installation or network access.

Only allowlisted source files are copied. Local tools, signing keys, backups,
Git metadata, recordings and APKs are excluded (APK inclusion is opt-in).
"""

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
APP_ID = "io.github.tanosx.Panelyra"
LOCAL_MAINTAINER = "TanosX <packaging@example.invalid>"


def copy_file(source, target, mode=0o644):
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"Missing file or unsupported symlink: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(mode)


def write_file(target, content, mode=0o644):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    target.chmod(mode)


def build(args):
    if shutil.which("dpkg-deb") is None:
        raise ValueError("dpkg-deb is required (Ubuntu package: dpkg)")
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+:~\-]*", args.version):
        raise ValueError("Invalid Debian version")
    if not re.fullmatch(r"[^<>\r\n]+ <[^<>\s@]+@[^<>\s@]+>", args.maintainer):
        raise ValueError("Use --maintainer 'Name <email@example.org>'")
    if args.release and (args.maintainer == LOCAL_MAINTAINER
                         or ".invalid" in args.maintainer):
        raise ValueError("--release requires a real --maintainer contact")
    if args.homepage and (not args.homepage.startswith("https://")
                          or any(c.isspace() for c in args.homepage)):
        raise ValueError("--homepage must be an HTTPS URL without whitespace")
    if args.release and not args.homepage:
        raise ValueError("--release requires the actual public --homepage")
    if args.include_apk:
        if args.include_apk.suffix != ".apk":
            raise ValueError("--include-apk must name an APK")
        if args.release:
            raise ValueError("Release .deb files keep the signed Android APK separate")

    metadata = ROOT / "packaging" / f"{APP_ID}.metainfo.xml"
    component = ET.parse(metadata).getroot()
    version = component.find("releases/release").get("version")
    if args.version != version:
        raise ValueError(f"Update MetaInfo release version ({version}) before changing package version")
    if args.release:
        if component.find("url[@type='homepage']") is None:
            raise ValueError("Release MetaInfo needs a real homepage URL")
        if component.find("screenshots/screenshot/image") is None:
            raise ValueError("Release MetaInfo needs a real screenshot URL")

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="panelyra-deb-") as temporary:
        package = Path(temporary) / "package"
        data = package / "usr/share/panelyra"
        for path in sorted((ROOT / "usbdisplay").glob("*.py")):
            copy_file(path, data / "usbdisplay" / path.name)
        assets = ROOT / "usbdisplay/assets"
        for path in sorted(assets.glob("*")):
            if path.suffix in (".svg", ".png", ".css"):
                copy_file(path, data / "usbdisplay/assets" / path.name)
        # Bundle the optional GNOME helper as source, without installing or
        # enabling it in any user's Shell session from a package maintainer hook.
        extension = Path("desktop/gnome-extension/panelyra-windows@tanosx.github.io")
        for name in ("metadata.json", "extension.js", "windowRestore.js"):
            copy_file(ROOT / extension / name, data / extension / name)
        copy_file(ROOT / "scripts/install-window-extension.py",
                  data / "scripts/install-window-extension.py", 0o755)
        copy_file(assets / "panelyra.svg", package / "usr/share/icons/hicolor/scalable/apps" / f"{APP_ID}.svg")
        copy_file(assets / "panelyra-256.png", package / "usr/share/icons/hicolor/256x256/apps" / f"{APP_ID}.png")
        copy_file(ROOT / "packaging" / f"{APP_ID}.desktop",
                  package / "usr/share/applications" / f"{APP_ID}.desktop")
        copy_file(metadata, package / "usr/share/metainfo" / metadata.name)
        # Keep imports and resources independent of the caller's working directory.
        write_file(package / "usr/bin/panelyra", '''#!/usr/bin/python3
import sys
sys.path.insert(0, "/usr/share/panelyra")
from usbdisplay.__main__ import main
sys.exit(main())
''', 0o755)
        write_file(package / "usr/bin/panelyra-launcher", '''#!/bin/sh
exec /usr/bin/panelyra gui "$@"
''', 0o755)
        for path in (ROOT / "README.md", ROOT / "LICENSE", ROOT / "SECURITY.md",
                     ROOT / "CONTRIBUTING.md", ROOT / "CHANGELOG.md"):
            if path.exists():
                copy_file(path, package / "usr/share/doc/panelyra" / path.name)
        for path in sorted((ROOT / "docs").rglob("*")):
            if path.is_file() and path.suffix in (".md", ".png", ".svg"):
                copy_file(path, package / "usr/share/doc/panelyra/docs" / path.relative_to(ROOT / "docs"))
        copy_file(ROOT / "android/README.md", package / "usr/share/doc/panelyra/android/README.md")
        for path in sorted(assets.glob("*")):
            if path.suffix in (".svg", ".png"):
                copy_file(path, package / "usr/share/doc/panelyra/usbdisplay/assets" / path.name)
        copy_file(ROOT / "LICENSE", package / "usr/share/doc/panelyra/copyright")
        if args.include_apk:
            copy_file(args.include_apk.resolve(), data / "out/panelyra.apk")

        installed = sum(p.stat().st_size for p in package.rglob("*") if p.is_file())
        control = (ROOT / "packaging/debian/control.in").read_text(encoding="utf-8")
        replacements = {"@VERSION@": args.version, "@MAINTAINER@": args.maintainer,
                        "@INSTALLED_SIZE@": str((installed + 1023) // 1024)}
        for token, value in replacements.items():
            control = control.replace(token, value)
        if args.homepage:
            control = control.replace("Description:", f"Homepage: {args.homepage}\nDescription:", 1)
        write_file(package / "DEBIAN/control", control)
        sums = []
        for path in sorted(package.rglob("*")):
            if path.is_file() and "DEBIAN" not in path.relative_to(package).parts:
                sums.append(f"{hashlib.md5(path.read_bytes()).hexdigest()}  {path.relative_to(package)}")
        write_file(package / "DEBIAN/md5sums", "\n".join(sums) + "\n")
        # Do not inherit a permissive developer umask into a system package.
        package.chmod(0o755)
        for path in package.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
        if args.release:
            subprocess.run(["appstreamcli", "validate", "--no-net", "--strict", str(metadata)], check=True)
            subprocess.run(["desktop-file-validate", str(ROOT / "packaging" / f"{APP_ID}.desktop")], check=True)
        target = output / f"panelyra_{args.version}_all.deb"
        # Build to a temporary output and atomically replace a previous local build.
        pending = Path(temporary) / target.name
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(package), str(pending)], check=True)
        shutil.copyfile(pending, output / (target.name + ".tmp"))
        os.replace(output / (target.name + ".tmp"), target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        write_file(target.with_suffix(target.suffix + ".sha256"), f"{digest}  {target.name}\n")
        print(f"Built: {target}")
        print(f"SHA256: {digest}")
        print("No system packages were installed and no display session was started.")
        if not args.release:
            print("Local preview package: complete the publication checklist before distribution.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.3.0")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "out")
    parser.add_argument("--maintainer", default=LOCAL_MAINTAINER)
    parser.add_argument("--homepage", help="actual public HTTPS project URL")
    parser.add_argument("--include-apk", type=Path, help="include this APK in a local build (opt-in)")
    parser.add_argument("--release", action="store_true", help="require release metadata and real maintainer contact")
    args = parser.parse_args()
    try:
        build(args)
    except (OSError, ValueError, subprocess.CalledProcessError, ET.ParseError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
