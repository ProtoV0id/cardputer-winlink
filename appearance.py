"""Local appearance preferences; Pat and radio configuration stay separate."""
import json
import os
from pathlib import Path
import tempfile


LAYOUTS = ("classic", "dashboard", "radio")


class AppearanceSettings:
    def __init__(self, path=None):
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        self.path = Path(path) if path is not None else config_home / "pocketlink" / "appearance.json"

    def _read(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None, {}
        except OSError as exc:
            raise RuntimeError("Cannot read appearance settings: " + str(exc)) from exc
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError("Cannot read appearance settings: " + str(exc)) from exc
        return raw, data

    def load(self):
        _, data = self._read()
        value = data.get("layout", "classic")
        return value if value in LAYOUTS else "classic"

    def save(self, layout):
        if layout not in LAYOUTS:
            raise ValueError("Unknown layout.")
        before, data = self._read()
        data["layout"] = layout
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix=".appearance-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(data, stream, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    current = self.path.read_bytes()
                except FileNotFoundError:
                    current = None
                if current != before:
                    raise RuntimeError("Appearance settings changed; please retry.")
                os.replace(temp, self.path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        except OSError as exc:
            raise RuntimeError("Cannot save appearance settings: " + str(exc)) from exc
