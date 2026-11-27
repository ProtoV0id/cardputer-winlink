#!/usr/bin/env python3
"""Build PocketLink's experimental tncd from a verified upstream snapshot.

This creates a separate executable. It does not install or replace tncd.
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

COMMIT = "5180db276f8f922cd90db7f68e067a28be8dc515"
ARCHIVE_HASH = "92421ca0cf22d92b12482a3b5eb77f5e8e82f85def16f2b0b2e1cf2fbe24bc75"
ARCHIVE_URL = f"https://codeload.github.com/ben-kuhn/tncd/tar.gz/{COMMIT}"
VERSION = "2.1.0-pocketlink.1"
START = "\t// Check if the device is already Connected; if so, disconnect first.\n"
END = "\t// Call ConnectProfile asynchronously so the GLib main loop (or D-Bus\n"
REPLACEMENT = '''\t// PocketLink experimental build: keep any existing ACL connection and
\t// connect the registered SPP profile directly. Full-device disconnects
\t// can leave the Cardputer Zero controller waiting for a response.
\t// This bypasses upstream's stale-SPP recovery and needs field testing.
\tlog.Printf("bluetooth: PocketLink experimental: skipping pre-disconnect for %s", bt.cfg.BDAddr)

'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="use a previously downloaded upstream tar.gz")
    parser.add_argument("--arch", choices=("arm64", "amd64"), default="arm64")
    parser.add_argument("--output", type=Path, required=True, help="new output binary path")
    parser.add_argument("--source-output", type=Path, help="new corresponding complete source archive")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite {output}")
    source_output = args.source_output.resolve() if args.source_output else None
    if source_output and (source_output == output or source_output.exists()):
        raise SystemExit(f"Refusing to overwrite {source_output}")
    if not shutil.which("go"):
        raise SystemExit("Go 1.25.13 or later is required for this build")
    with tempfile.TemporaryDirectory(prefix="pocketlink-tncd-build-") as temp:
        root = Path(temp)
        archive = root / "upstream.tar.gz"
        if args.archive:
            shutil.copyfile(args.archive, archive)
        else:
            with urllib.request.urlopen(ARCHIVE_URL, timeout=90) as response, archive.open("wb") as out:
                shutil.copyfileobj(response, out)
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != ARCHIVE_HASH:
            raise SystemExit(f"Upstream archive hash mismatch: {actual}")
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                path = Path(member.name)
                if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                    raise SystemExit(f"Unsafe source archive entry: {member.name}")
            tar.extractall(root)
        source = root / f"tncd-{COMMIT}"
        target = source / "kiss" / "bluetooth_linux.go"
        text = target.read_text(encoding="utf-8")
        if text.count(START) != 1 or text.count(END) != 1:
            raise SystemExit("Pinned source does not contain the expected patch markers")
        before, rest = text.split(START)
        removed, after = rest.split(END)
        if '"org.bluez.Device1.Disconnect"' not in removed:
            raise SystemExit("Expected upstream full-device disconnect was not found")
        target.write_text(before + REPLACEMENT + END + after, encoding="utf-8", newline="\n")
        env = os.environ.copy()
        env.update({"CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": args.arch,
                    "GOTOOLCHAIN": "local", "GOCACHE": str(root / "go-cache"),
                    "GOMODCACHE": str(root / "go-mod-cache")})
        staged_binary = root / "tncd-pocketlink"
        subprocess.run(["go", "build", "-trimpath", "-buildvcs=false", "-ldflags",
                        f"-X github.com/ben-kuhn/tncd/v2/internal/version.Version={VERSION}",
                        "-o", str(staged_binary), "./cmd/tncd"], cwd=source, env=env, check=True)
        if source_output:
            # Include all external Go dependencies in the corresponding GPL source.
            subprocess.run(["go", "mod", "vendor"], cwd=source, env=env, check=True)
            source_output.parent.mkdir(parents=True, exist_ok=True)
            with source_output.open("xb") as stream:
                with tarfile.open(fileobj=stream, mode="w:gz") as package:
                    package.add(source, arcname=f"tncd-{VERSION}-source")
                    package.add(Path(__file__), arcname=f"tncd-{VERSION}-source/pocketlink-build/build.py")
                    package.add(Path(__file__).with_name("README.md"),
                                arcname=f"tncd-{VERSION}-source/pocketlink-build/README.md")
            print(f"Complete source archive: {source_output}")
            print(f"Source SHA-256: {hashlib.sha256(source_output.read_bytes()).hexdigest()}")
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create protects an existing user file if it appeared during the build.
        with output.open("xb") as out, staged_binary.open("rb") as src:
            shutil.copyfileobj(src, out)
        output.chmod(0o755)
        print(f"Built {output}")
        print(f"SHA-256: {hashlib.sha256(output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
