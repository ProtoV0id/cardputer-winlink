# Cardputer Zero application wheel setup

PocketLink is an experimental keyboard-driven front end for the Pat
Winlink client. The initial connection mode is Internet Telnet; no radio or
modem is required. A previous version completed a Telnet round trip on a
Cardputer Zero; the updated interface and launcher name need device verification.
For experimental UV-PRO Bluetooth packet support, see [UVPRO-SETUP.md](UVPRO-SETUP.md).

## Install from GitHub over SSH

Connect the Cardputer Zero to Wi-Fi and enable SSH. From your computer connect
with `ssh YOUR_USERNAME@CARDPUTER_IP_ADDRESS`, substituting the actual username
and IP address. Once logged in, these commands run on the Cardputer:

```sh
sudo apt update
sudo apt install git python3
git clone https://github.com/ProtoV0id/cardputer-winlink.git ~/cardputer-winlink
cd ~/cardputer-winlink
python3 build_package.py
sudo apt install ./dist/cardputer-winlink_0.3.1_all.deb
```

The clone is public and uses HTTPS, so it does not need a GitHub account or key.
Stop if a command fails. If `~/cardputer-winlink` already exists, inspect it and
choose another clone destination instead of overwriting it. The package build
refuses to overwrite an existing `.deb`. To rebuild without replacing it, from
the repository run:

```sh
build_dir=$(mktemp -d)
python3 build_package.py --output-dir "$build_dir"
sudo apt install "$build_dir/cardputer-winlink_0.3.1_all.deb"
```

The `.deb` requests Debian's `pat` package version 0.16.0 or newer; an older OS
or different repository may not provide it. Installation also requires the
device's configured package sources to provide Python, Pillow and evdev.
Verify the installed package and launcher entry:

```sh
dpkg -s cardputer-winlink
command -v pat-winlink || command -v pat
ls -l /usr/share/APPLaunch/applications/cardputer-winlink.desktop
```

Then open **PocketLink** on the device. These commands do not verify the
physical display, keyboard, application wheel, or a Winlink session.

## Build the package

From this project directory, on a computer with Python 3:

```sh
python3 build_package.py
```

The builder uses only the Python standard library and writes
`dist/cardputer-winlink_0.3.1_all.deb`. It does not install software or access
the network. It refuses to overwrite an existing package; move or rename the
old package before rebuilding. The package may be built on Windows or Linux.

## Install on the Cardputer Zero

Copy the built `.deb` to the Cardputer Zero. On the device, from the directory
containing the file:

```sh
sudo apt install ./cardputer-winlink_0.3.1_all.deb
```

Use the actual path if you copied it elsewhere, for example
`./dist/cardputer-winlink_0.3.1_all.deb`. This explicitly installs the app and
its Debian dependencies: Python 3, Pillow, evdev, and Pat (version 0.16.0 or
newer). It may access configured package repositories. On Debian, Pat's
executable is normally `pat-winlink`; upstream packages commonly use `pat`.

Find **PocketLink** in APPLaunch's application wheel. If it does not
appear, restart the launcher or reboot. APPLaunch versions and the physical
device have not yet been verified with this package. If the app cannot open
the display or keyboard, check the launcher's device permissions and selected
framebuffer/input device; do not run the app as root to work around a problem.

The package installs a `.desktop` entry in `/usr/share/APPLaunch/applications`,
an icon and wrapper in APPLaunch's normal directories, and the app in
`/usr/share/APPLaunch/apps/cardputer-winlink`. It does not publish to the
Cardputer app store, start services at boot, flash firmware, or configure a
radio.

The package retains the `cardputer-winlink` identifier and launcher file paths
so installations of the earlier Cardputer Winlink upgrade in place. Pat's
mailbox and settings are not moved or removed by this upgrade.

To remove the application package later:

```sh
sudo apt remove cardputer-winlink
```

Removing the application does not intentionally remove your Pat configuration
or mailbox. Back up those files separately before changing Pat or the device.
