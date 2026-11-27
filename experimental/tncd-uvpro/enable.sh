#!/bin/sh
# Opt in to the separate experimental bridge; never overwrite existing files.
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
binary="$script_dir/artifacts/tncd-2.1.0-pocketlink.1-linux-arm64"
destination="$HOME/.local/bin/pocketlink-tncd-experimental"
marker="$HOME/.config/cardputer-pat/experimental-tncd"
expected_hash=f8dff1b9ff2507427f5e96e7fcc1207b8a9e1ef16cd571860e0899723a648d81

if [ "$(uname -m)" != aarch64 ]; then
    echo 'The experimental binary requires aarch64.' >&2
    exit 1
fi
if [ -e "$destination" ] || [ -L "$destination" ] || [ -e "$marker" ] || [ -L "$marker" ]; then
    echo 'An experimental bridge or opt-in marker already exists; inspect it before continuing.' >&2
    exit 1
fi
printf '%s  %s\n' "$expected_hash" "$binary" | sha256sum -c -
mkdir -p "$HOME/.local/bin" "$HOME/.config/cardputer-pat"
# noclobber prevents overwriting an existing executable or marker.
set -C
cat "$binary" > "$destination"
chmod 0755 "$destination"
if [ "$("$destination" version)" != 'tncd 2.1.0-pocketlink.1' ]; then
    echo 'Version check failed. Experimental bridge was not enabled.' >&2
    exit 1
fi
printf 'enabled\n' > "$marker"
echo 'Experimental bridge enabled. Start a packet session only when ready to transmit.'
