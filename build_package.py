#!/usr/bin/env python3
"""Build the local Cardputer Zero APPLaunch Debian package without installing it."""
import argparse
import io
from pathlib import Path
import struct
import tarfile
import zlib

ROOT = Path(__file__).resolve().parent
PACKAGE = "cardputer-winlink"
VERSION = "0.3.1"
PREFIX = "/usr/share/APPLaunch"
OUTPUT_NAME = f"{PACKAGE}_{VERSION}_all.deb"
SOURCES = ("winlink_gui.py", "backend.py", "device.py", "uvpro_tnc.py")


def icon_png():
    """Generate the PocketLink Signal Blue icon without image libraries."""
    width = height = 96
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            color = (10, 25, 37)
            # A compact radio face with inset display and cyan accent.
            if 8 <= x <= 87 and 8 <= y <= 87:
                color = (16, 42, 59)
            if 12 <= x <= 83 and 12 <= y <= 83:
                color = (22, 64, 81)
            if 16 <= x <= 79 and 17 <= y <= 72:
                color = (10, 25, 37)
            if 69 <= x <= 75 and 21 <= y <= 27:
                color = (123, 228, 238)
            # Envelope and radio bars combine messaging with a field-radio cue.
            if ((23 <= x <= 64 and y in (34, 35, 60, 61)) or
                (34 <= y <= 61 and x in (23, 24, 63, 64)) or
                (25 <= x <= 43 and abs(y - (36 + (x - 25) // 2)) <= 1) or
                (44 <= x <= 62 and abs(y - (45 - (x - 44) // 2)) <= 1)):
                color = (123, 228, 238)
            if ((24 <= x <= 32 and 77 <= y <= 79) or
                (37 <= x <= 45 and 77 <= y <= 79) or
                (50 <= x <= 58 and 77 <= y <= 79) or
                (63 <= x <= 71 and 77 <= y <= 79)):
                color = (49, 82, 100)
            raw.extend(color)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def make_tar(files):
    """Create root-owned tar members with explicit modes on any build OS."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as archive:
        directories = set()
        for name in files:
            parent = Path(name).parent
            while str(parent) not in (".", ""):
                directories.add(parent.as_posix())
                parent = parent.parent
        for name in sorted(directories):
            info = tarfile.TarInfo("./" + name + "/")
            info.type, info.mode, info.uid, info.gid = tarfile.DIRTYPE, 0o755, 0, 0
            archive.addfile(info)
        for name, (content, mode) in sorted(files.items()):
            info = tarfile.TarInfo("./" + name)
            info.size, info.mode, info.uid, info.gid = len(content), mode, 0, 0
            archive.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def ar_member(name, content):
    header = (f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}"
              f"{len(content):<10}`\n").encode("ascii")
    if len(header) != 60:
        raise ValueError("Invalid archive header")
    return header + content + (b"\n" if len(content) % 2 else b"")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(ROOT / "dist"))
    args = parser.parse_args()
    for name in SOURCES:
        if not (ROOT / name).is_file():
            parser.error(f"Missing required source file: {name}")
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    target = out / OUTPUT_NAME

    desktop = ("[Desktop Entry]\nType=Application\nName=PocketLink\n"
               f"Exec={PREFIX}/bin/{PACKAGE}\n"
               f"Icon=share/images/{PACKAGE}.png\nTerminal=false\n")
    wrapper = ("#!/bin/sh\nset -eu\numask 077\n"
               f"exec /usr/bin/python3 {PREFIX}/apps/{PACKAGE}/winlink_gui.py\n")
    control = (f"Package: {PACKAGE}\nVersion: {VERSION}\nArchitecture: all\n"
               "Maintainer: Cardputer local build <local@invalid>\n"
               "Depends: python3, python3-pil, python3-evdev, pat (>= 0.16.0)\n"
               "Section: hamradio\nPriority: optional\n"
               "Description: PocketLink keyboard-driven interface for Pat\n"
               " Graphical Winlink interface for Cardputer Zero using Pat.\n")
    files = {
        f"{PREFIX}/applications/{PACKAGE}.desktop".lstrip("/"): (desktop.encode(), 0o644),
        f"{PREFIX}/bin/{PACKAGE}".lstrip("/"): (wrapper.encode(), 0o755),
        f"{PREFIX}/share/images/{PACKAGE}.png".lstrip("/"): (icon_png(), 0o644),
    }
    for name in SOURCES:
        files[f"{PREFIX}/apps/{PACKAGE}/{name}".lstrip("/")] = ((ROOT / name).read_bytes(), 0o644)
    payload = (b"!<arch>\n" + ar_member("debian-binary", b"2.0\n") +
               ar_member("control.tar.gz", make_tar({"control": (control.encode(), 0o644)})) +
               ar_member("data.tar.gz", make_tar(files)))
    try:
        with target.open("xb") as output:
            output.write(payload)
    except FileExistsError:
        parser.error(f"Refusing to overwrite {target}")
    print(f"Built {target}. No installation performed.")


if __name__ == "__main__":
    main()
