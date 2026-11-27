"""Private address book for PocketLink; independent of Pat's mailbox."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import uuid


def normalize_contact_name(value):
    if not isinstance(value, str):
        raise ValueError("Enter a contact name.")
    value = value.strip()
    if not value or len(value) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Enter a contact name of 1–80 characters on one line.")
    return value


def normalize_contact_address(value):
    """Accept one Winlink callsign or email address without changing email case.

    Pat is responsible for final address validation and routing. Reject only
    obviously ambiguous input here: a contact represents one recipient, not a
    recipient list or an email display-name expression.
    """
    if not isinstance(value, str):
        raise ValueError("Enter a callsign or email address.")
    value = value.strip()
    if not value or len(value) > 254 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Enter one callsign or email address on one line.")
    if any(c in value for c in ",;<>()"):
        raise ValueError("Enter one callsign or email address, without a display name.")
    is_smtp = value[:5].casefold() == "smtp:"
    email = value[5:] if is_smtp else value
    if "@" in email:
        local, separator, domain = email.partition("@")
        if not local or not separator or not domain or "@" in domain or ":" in email:
            raise ValueError("Enter a valid email address.")
        return ("SMTP:" if is_smtp else "") + email
    if is_smtp:
        raise ValueError("Enter an email address after SMTP:.")
    match = re.fullmatch(r"([A-Za-z0-9]{1,12})(?:-([0-9]{1,2}))?", value)
    if not match or (match.group(2) is not None and int(match.group(2)) > 15):
        raise ValueError("Enter a Winlink callsign or email address.")
    return value.upper()


class ContactBook:
    """Store contacts as a versioned JSON document with atomic replacement."""

    def __init__(self, path=None):
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        self.path = Path(path) if path is not None else root / "pocketlink" / "contacts.json"

    def _read(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None, {"version": 1, "contacts": []}
        except OSError as exc:
            raise RuntimeError("Cannot read address book: " + str(exc)) from exc
        try:
            document = json.loads(raw.decode("utf-8"))
            if not isinstance(document, dict) or document.get("version") != 1 or not isinstance(document.get("contacts"), list):
                raise ValueError("unsupported address-book format")
            ids = set()
            for record in document["contacts"]:
                if not isinstance(record, dict):
                    raise ValueError("invalid contact entry")
                contact_id = record.get("id")
                if not isinstance(contact_id, str) or not re.fullmatch(r"[0-9a-f]{32}", contact_id) or contact_id in ids:
                    raise ValueError("invalid or duplicate contact ID")
                ids.add(contact_id)
                if not isinstance(record.get("name"), str) or not isinstance(record.get("address"), str):
                    raise ValueError("invalid contact details")
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError("Cannot read address book: " + str(exc)) from exc
        return raw, document

    def list_contacts(self):
        """Return copies in display-name order without altering the saved order."""
        _, document = self._read()
        return sorted((dict(record) for record in document["contacts"]),
                      key=lambda c: (c["name"].casefold(), c["address"].casefold(), c["id"]))

    def _write(self, before, document):
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix=".contacts-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(document, stream, indent=2, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    current = self.path.read_bytes()
                except FileNotFoundError:
                    current = None
                if current != before:
                    raise RuntimeError("Address book changed; please retry.")
                os.replace(temp, self.path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        except OSError as exc:
            raise RuntimeError("Cannot save address book: " + str(exc)) from exc

    def save(self, name, address, contact_id=None, expected=None):
        """Add a contact or edit the identified entry without replacing stale data.

        If editing from a previously loaded record, callers may pass that
        entire record as ``expected``. Its current value must still match.
        """
        name, address = normalize_contact_name(name), normalize_contact_address(address)
        before, document = self._read()
        contacts = document["contacts"]
        existing = next((c for c in contacts if c["id"] == contact_id), None) if contact_id is not None else None
        if contact_id is not None and existing is None:
            raise ValueError("Contact no longer exists; reload the address book.")
        if expected is not None:
            if contact_id is None or not isinstance(expected, dict) or expected.get("id") != contact_id:
                raise ValueError("Invalid contact snapshot; reload the address book.")
            if existing != expected:
                raise RuntimeError("Contact changed; reload the address book before editing.")
        if any(c["address"].casefold() == address.casefold() and c["id"] != contact_id for c in contacts):
            raise ValueError("That address is already in the address book.")
        if existing is None:
            existing = {"id": uuid.uuid4().hex}
            contacts.append(existing)
        existing.update({"name": name, "address": address})
        self._write(before, document)
        return dict(existing)

    def delete(self, contact_id, expected=None):
        """Delete only the explicit, unchanged contact after user confirmation."""
        before, document = self._read()
        contacts = document["contacts"]
        index = next((i for i, c in enumerate(contacts) if c["id"] == contact_id), None)
        if index is None:
            raise ValueError("Contact no longer exists; reload the address book.")
        if expected is not None:
            if not isinstance(expected, dict) or expected.get("id") != contact_id:
                raise ValueError("Invalid contact snapshot; reload the address book.")
            if contacts[index] != expected:
                raise RuntimeError("Contact changed; reload the address book before deleting.")
        del contacts[index]
        self._write(before, document)
