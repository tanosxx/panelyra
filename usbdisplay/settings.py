"""Validated desktop preferences and shared display presets."""

from dataclasses import asdict, dataclass, fields
import json
import os
from pathlib import Path
import tempfile

from .protocol import validate_mode
from .themes import DEFAULT_THEME, THEME_IDS


PRESETS = {
    "balanced": dict(width=1600, height=1000, fps=30, capture_fps=60, quality=18),
    "economy": dict(width=1280, height=800, fps=20, capture_fps=60, quality=20),
    "crisp": dict(width=1600, height=1000, fps=30, capture_fps=60, quality=14),
}


@dataclass(frozen=True)
class Settings:
    width: int = 1600
    height: int = 1000
    fps: int = 30
    capture_fps: int = 60
    quality: int = 18
    transport: str = "usb"
    auto_connect: bool = True
    language: str = "auto"
    theme: str = DEFAULT_THEME
    apk_path: str = ""
    apk_port: int = 8765
    apk_duration: int = 1800
    apk_interface: str = ""

    def validate(self):
        for name in ("width", "height", "fps", "capture_fps", "quality"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")
        validate_mode(self.width, self.height, self.fps)
        if not self.fps <= self.capture_fps <= 60:
            raise ValueError("Capture rate must be between output FPS and 60")
        if not 10 <= self.quality <= 35:
            raise ValueError("Quality QP must be between 10 and 35")
        if self.transport not in ("usb", "adb", "auto"):
            raise ValueError("Unknown transport")
        if self.language not in ("auto", "en", "ru"):
            raise ValueError("Unknown language")
        if not isinstance(self.theme, str) or self.theme not in THEME_IDS:
            raise ValueError("Unknown appearance theme")
        if type(self.auto_connect) is not bool:
            raise ValueError("auto_connect must be a boolean")
        if type(self.apk_port) is not int or not 1024 <= self.apk_port <= 65535:
            raise ValueError("APK server port must be between 1024 and 65535")
        if type(self.apk_duration) is not int or not 1 <= self.apk_duration <= 86400:
            raise ValueError("APK server duration must be between 1 and 86400 seconds")
        if not isinstance(self.apk_path, str) or '\0' in self.apk_path or len(self.apk_path) > 4096:
            raise ValueError("Invalid APK path")
        if (not isinstance(self.apk_interface, str) or len(self.apk_interface) > 64
                or any(character.isspace() or character in '/\0' for character in self.apk_interface)):
            raise ValueError("Invalid APK USB interface")
        return self

    def command_args(self):
        self.validate()
        return ["start", "--transport", self.transport, "--wait", "120",
                "--width", str(self.width), "--height", str(self.height),
                "--fps", str(self.fps), "--capture-fps", str(self.capture_fps),
                "--quality", str(self.quality)]


def config_path():
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "panelyra" / "settings.json"


def load(path=None):
    path = Path(path) if path is not None else config_path()
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            raise ValueError("Preferences must be a JSON object")
        known = {field.name for field in fields(Settings)}
        values = {key: value for key, value in content.items() if key in known}
        warning = None
        theme = values.get("theme", DEFAULT_THEME)
        if not isinstance(theme, str) or theme not in THEME_IDS:
            values["theme"] = DEFAULT_THEME
            warning = "Unknown appearance theme; using Classic"
        return Settings(**values).validate(), warning
    except FileNotFoundError:
        return Settings(), None
    except (ValueError, TypeError, OSError) as error:
        return Settings(), str(error)


def save(settings, path=None):
    settings.validate()
    path = Path(path) if path is not None else config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".settings-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(asdict(settings), stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
