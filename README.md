# Cardputer Winlink

An experimental, keyboard-operated graphical interface for [Pat Winlink](https://getpat.io/) on the M5Stack Cardputer Zero. This first version supports Internet Telnet sessions. It can read Pat's inbox, outbox and sent folders; compose and queue text messages; and send and receive when the operator explicitly starts a session.

The package adds **Cardputer Winlink** to the APPLaunch application wheel. See [LAUNCHER-SETUP.md](LAUNCHER-SETUP.md) for the full instructions. The built-in display, keyboard, application wheel, and real Winlink connection have not yet been tested on a physical Zero.

## Install over SSH

Connect the Cardputer Zero to Wi-Fi and enable SSH in its setup or settings. From PowerShell on your computer, connect using the username and IP address of your Cardputer:

```powershell
ssh YOUR_USERNAME@CARDPUTER_IP_ADDRESS
```

After logging in, run the following **on the Cardputer**. This uses the public HTTPS repository; a GitHub account or SSH key is not required for cloning it. Check the output of each command before continuing.

```sh
sudo apt update
sudo apt install git python3
git clone https://github.com/ProtoV0id/cardputer-winlink.git ~/cardputer-winlink
cd ~/cardputer-winlink
python3 build_package.py
sudo apt install ./dist/cardputer-winlink_0.1.0_all.deb
```

The final command installs the app, Pat, Pillow, and evdev using the device's configured Debian repositories. The package requires Pat 0.16.0 or newer; if apt cannot satisfy a dependency, stop and check your Cardputer's OS and package sources. If `~/cardputer-winlink` already exists, Git refuses to overwrite it; inspect the existing folder before choosing a different destination.

Check the installation from the same SSH session:

```sh
dpkg -s cardputer-winlink
command -v pat-winlink || command -v pat
ls -l /usr/share/APPLaunch/applications/cardputer-winlink.desktop
```

Then use the Cardputer's own screen to find **Cardputer Winlink** in the application wheel. If it is absent, restart APPLaunch or reboot and check again. These checks confirm installed files and Pat's executable; the application still needs to be opened and tested on the device.

## First use

1. Connect the Cardputer Zero to Wi-Fi and open **Cardputer Winlink** from the application wheel.
2. Open **Settings**, enter your callsign, and optionally your grid locator. If you already have a Winlink account, enter its secure login password. Password entry is masked; leaving it empty keeps the current password.
3. Open **Compose message**, enter the recipient, subject, and body, review the message and queue it. Queuing saves it to Pat's outbox; it does not transmit.
4. Choose **Connect via Telnet**, then confirm. Pat sends queued mail and receives waiting mail over the Internet. Open **Inbox** to read incoming messages.

Winlink accounts require a valid amateur license or qualifying agency authorization. Follow [Winlink's account instructions](https://winlink.org/) if creating a new account: for a client other than Winlink Express, the first connection starts account creation without a password, and a subsequent connection retrieves the password message. Add that password in Settings before later secure logins. Telnet uses an Internet connection; it does not test radio equipment.

## Controls

- Navigate with `F` / `X` or the arrow keys on menu and reading screens. Press Enter to select and Escape to go back. On the Zero, Fn+F/X/Z/C produces the arrow keys.
- In a text field, type normally, use left/right to move the cursor and Backspace to delete. Enter saves a single-line field.
- In the message body, Enter starts a new line; Shift+Enter saves the body. Escape returns without saving that field's changes. The rest of the draft remains available until the app exits or you clear it.
- Review a completed draft and explicitly choose **Queue in Pat outbox**. A separate confirmation starts the Telnet session.

The built-in keypad translation follows M5Stack's v5 Zero driver and keymap. Verify punctuation, cursor controls and Shift+Enter on the device before relying on the app.

## Data and limitations

- Pat stores its configuration in `~/.config/pat/config.json` by default and its mailbox in its usual data directory. This front end preserves other existing Pat settings and mail. Configuration and mailbox paths can depend on the user's XDG environment.
- The Winlink password is stored in Pat's configuration, and messages are stored on the device; they are not encrypted at rest by this app. A loopback-only Pat process uses a temporary private configuration copy while the app runs and is shut down on normal exit. Avoid running multiple front ends that modify Pat configuration at the same time.
- Plain-text messaging is supported. Attachments may be indicated on received messages, but this version does not open, create, or save attachments or Winlink forms. Radio and modem support are outside this Telnet-first version.
- This is an early build. A local preview or mocked test is not proof of the on-device keyboard, display, application wheel, Pat version compatibility, or an actual Winlink session.

## Local development

Python 3 and Pillow are needed to render the interface. Linux device use also needs `python3-evdev`; Pat is an external system dependency. See [requirements.txt](requirements.txt).

```sh
python3 winlink_gui.py --preview preview.png
python3 -m unittest discover -s tests -v
python3 build_package.py
```

The preview and tests do not connect to Winlink. On Linux, device paths may be overridden with `WINLINK_FB` and `WINLINK_KEYBOARD`, or command-line `--framebuffer` and `--keyboard` arguments. The launcher-provided framebuffer and keyboard environment variables are also honored.
