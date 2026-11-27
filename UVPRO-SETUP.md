# BTECH UV-PRO Bluetooth packet setup

This experimental path uses the UV-PRO's built-in Bluetooth KISS TNC. Pat needs a connected-mode AX.25 engine as well as the TNC: PocketLink uses the separately installed [tncd](https://github.com/ben-kuhn/tncd) 2.1.0 bridge to provide that engine over local AGWPE. PocketLink starts it only for an explicitly confirmed packet session and stops its own bridge afterward. It does not program the radio or automatically select a frequency.

**Status:** physical Cardputer Zero and UV-PRO attempts have not yet established a reliable Bluetooth session. The experimental bridge below has not been tested on that hardware. A running local bridge alone does not prove Bluetooth connection, RF transmission, or delivery.

## 1. Prerequisites

- A Cardputer Zero with functioning Bluetooth and BlueZ, Pat, and PocketLink 0.3.1 or newer. The existing Telnet feature works without tncd.
- A BTECH UV-PRO with KISS TNC support enabled. BTECH documents the feature starting with [firmware 0.7.11](https://baofengtech.com/btech-uv-pro-firmware-update-0-7-11-kiss-mode-now-built-in/). Firmware updates and phone-app use are separate from this setup.
- A compatible **1200-baud AX.25 packet Winlink RMS gateway**, its full gateway callsign including SSID where applicable, and its current operating frequency and settings. Tune the radio's KISS transmit and receive frequencies appropriately, including any split, and check that the channel is clear before transmitting. The visible voice channel may differ from the KISS setting.
- Your amateur radio callsign and operating privileges appropriate for the selected frequency.

## 2. Install the bridge on the Cardputer

In an SSH session on the Cardputer, verify the operating system is 64-bit ARM:

```sh
uname -m
```

The commands below are for `aarch64`. If it reports something else, stop and choose an appropriate official build from the [tncd release](https://github.com/ben-kuhn/tncd/releases/tag/v2.1.0); do not run the ARM64 executable on a different architecture.

Make sure `bluetoothctl` and `curl` are available. On Debian-based images, install them if needed:

```sh
sudo apt update
sudo apt install bluez curl
```

Download the official pinned **tncd 2.1.0** ARM64 executable, verify the SHA-256 reported by its GitHub release, and install it for the current user. The guard avoids replacing an existing `~/.local/bin/tncd`:

```sh
mkdir -p "$HOME/.local/bin"
if [ -e "$HOME/.local/bin/tncd" ]; then
    echo "Already present; inspect it before replacing: $HOME/.local/bin/tncd"
else
    tnc_download_dir="$(mktemp -d)" &&
    curl -fL 'https://github.com/ben-kuhn/tncd/releases/download/v2.1.0/tncd-linux-arm64' -o "$tnc_download_dir/tncd" &&
    printf '%s  %s\n' 'ef43652a5c41157a8ef6534eb413f6ff40d23ca26c1bd386810cf807953a8b08' "$tnc_download_dir/tncd" | sha256sum -c - &&
    install -m 0755 "$tnc_download_dir/tncd" "$HOME/.local/bin/tncd" &&
    "$HOME/.local/bin/tncd" version
fi
```

The expected version output is `tncd 2.1.0`. PocketLink currently checks this pinned version; a different existing version is not automatically replaced. The checksum is the GitHub release asset digest, not an independent signature. Nothing is downloaded or installed automatically by PocketLink, and this does not add a third-party apt repository or start a service at boot.

## 3. Pair the radio

Enable **KISS TNC** on the radio. If the radio is already connected to another device or its programming app, disconnect it there first. The bridge may disconnect a Bluetooth audio profile to establish the KISS data link. From an SSH session:

```sh
bluetoothctl
```

In the interactive prompt, use the following commands. Replace `AA:BB:CC:DD:EE:FF` with the UV-PRO address shown by the scan; do not copy the example literally:

```text
power on
agent on
default-agent
scan on
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
scan off
exit
```

Follow any pairing prompts. `bluetoothctl devices Paired` can show paired device addresses afterward. An existing connection from another app can prevent the KISS link. The Bluetooth setup varies with the Cardputer image and radio firmware; do not assume pairing alone proves packet operation.

## 4. Use PocketLink

1. In **Settings**, set your own callsign if it isn't set already.
2. Open **Packet radio settings** and enter the radio's Bluetooth address and your gateway's callsign/SSID. If the path uses a digipeater, enter its callsign/SSID in the separate optional **Via** field; otherwise leave **Via** empty for a direct connection. The gateway field must contain the final Winlink RMS, not the digipeater.
3. On the radio, enable KISS TNC and manually verify its KISS RX/TX frequencies, mode, and power against the gateway. Do not use an APRS-only frequency for a Winlink connection.
4. Queue a short test message if desired. Choose **Connect via packet**, read the reminder, and explicitly select the transmit option. Bluetooth startup may take up to 2½ minutes; the app shows a waiting screen and allows Escape to cancel before the packet session begins. The radio can transmit only after you start the session.
5. Review the session result and Sent/Outbox. A local bridge or Bluetooth connection alone is not proof that RF was transmitted or that a gateway received the message. The UV-PRO may behave differently across firmware versions; an independent receiver on the gateway frequency can help verify RF.

For a failed attempt, check that the radio is paired and not connected elsewhere, KISS is enabled, `tncd` is present and version 2.1.0, the configured gateway and any digipeater callsign/SSID are correct, and the radio is on the gateway's actual packet RX/TX frequencies. Packet gateway and digipeater reachability, antenna, channel contention and permitted transmit settings must also be checked. PocketLink needs local TCP port 8000 for its temporary AGWPE bridge; if it reports the port is occupied, inspect the existing service before changing it. Do not infer success from Telnet or from a merely running bridge.

PocketLink keeps private per-session bridge diagnostics under `~/.local/state/cardputer-pat/uvpro-tncd-*.log` by default, and Pat writes `~/.local/state/cardputer-pat/pat.log` (Pat's log is replaced when its process starts). These may contain callsigns, addresses, or message-related details. Review and redact them before sharing, and never share a password.

## Experimental bridge for an already-connected radio

PocketLink 0.3.3 can opt into a separately built, experimental tncd that tries the Bluetooth Serial Port Profile without first disconnecting the entire radio. Some UV-PRO/BlueZ configurations reconnect an audio profile while that whole-device disconnect is in progress. This is an experiment, not a confirmed fix. Upstream added the disconnect to clear stale serial links; bypassing it could leave a link that appears online but does not transmit. Earlier failures without a pre-disconnect are not explained by this change. It retains upstream's later audio-profile cleanup. The app still requires the bridge's Bluetooth port to report online before starting Pat, but that does not prove RF transmission.

The experimental bridge, complete corresponding GPL source, and reproducible build instructions are in [experimental/tncd-uvpro](experimental/tncd-uvpro/README.md). The official `~/.local/bin/tncd` is not changed. To opt in after upgrading PocketLink, on a Linux **aarch64** Cardputer, with the app closed, run the following from this repository:

```sh
cd ~/cardputer-winlink
sh experimental/tncd-uvpro/enable.sh
```

If version verification fails, the marker is not created. To return to the official bridge, close PocketLink and remove only the opt-in marker:

```sh
rm -f "$HOME/.config/cardputer-pat/experimental-tncd"
```

Then a new PocketLink session selects the official bridge again. Neither path automatically retries a failed packet session.

PocketLink stores the Bluetooth address and gateway separately in `~/.config/pocketlink/radio.json`. Pat's mailbox and configuration remain in their existing locations. The app starts a private local bridge and private Pat runtime configuration and does not enable automatic QSY or modify radio memories.
