# Experimental PocketLink tncd build

This separately named build of tncd 2.1.0 skips a whole-device Bluetooth
disconnect immediately before SPP connection. It is an experiment for the
Cardputer Zero and has not been validated on the radio. Upstream included that
disconnect to clear stale SPP links, so this change may leave a connection that
appears online but does not transmit. The build retains upstream's subsequent
audio-profile disconnect behavior.

The upstream source is commit `5180db276f8f922cd90db7f68e067a28be8dc515`
from <https://github.com/ben-kuhn/tncd> (the commit behind tag `v2.1.0`).
It is licensed under GPL-3.0; the complete source, including COPYING, is
available at `https://codeload.github.com/ben-kuhn/tncd/tar.gz/5180db276f8f922cd90db7f68e067a28be8dc515`.
Its SHA-256 is `92421ca0cf22d92b12482a3b5eb77f5e8e82f85def16f2b0b2e1cf2fbe24bc75`.
The exact corresponding modification is expressed in `build.py`, which checks
that hash and applies the source transformation before building.
This hash checks that the downloaded archive matches the snapshot used for
this build; it is not a publisher signature or independent authenticity proof.

Artifacts produced with Go 1.27.3 on Windows, for Linux:

| Artifact | SHA-256 |
| --- | --- |
| `tncd-2.1.0-pocketlink.1-linux-arm64` | `f8dff1b9ff2507427f5e96e7fcc1207b8a9e1ef16cd571860e0899723a648d81` |
| `tncd-2.1.0-pocketlink.1-source.tar.gz` | `7f57eba3394ec534497db7a34f2847271ec68955c8febd1db809cdc582f93a62` |

The source archive includes the complete patched tncd source, its GPL license,
and vendored dependencies. With Go 1.25.13 or newer, it can be rebuilt offline:

```sh
tar -xzf tncd-2.1.0-pocketlink.1-source.tar.gz
cd tncd-2.1.0-pocketlink.1-source
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 GOTOOLCHAIN=local go build \
  -mod=vendor -trimpath -buildvcs=false \
  -ldflags '-X github.com/ben-kuhn/tncd/v2/internal/version.Version=2.1.0-pocketlink.1' \
  -o ../tncd-2.1.0-pocketlink.1-linux-arm64 ./cmd/tncd
```

Byte-for-byte reproducibility depends on the Go toolchain version; the ARM64
binary was independently rebuilt with the same Go 1.27.3 and matched.

With Python 3, Go 1.25.13 or newer, and network access for Go dependencies:

```sh
python3 build.py --arch arm64 --output ./tncd-2.1.0-pocketlink.1-linux-arm64 \
  --source-output ./tncd-2.1.0-pocketlink.1-source.tar.gz
```

The script downloads and checks the upstream source, builds in a temporary
directory, and refuses to overwrite an existing output. A previously acquired
upstream source archive may be passed with `--archive`. `--source-output`
archives the patched source and vendored Go dependencies, together with this
build recipe; include it when distributing the executable. Build an amd64 test
executable by specifying `--arch amd64`. The executable is separate from the
official `tncd`; restoring the original means selecting the original executable.
