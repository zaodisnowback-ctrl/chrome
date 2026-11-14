import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from navigation import validate_web_url


MAX_STORE_BYTES = 2 * 1024 * 1024
MAX_BOOKMARKS = 500
MAX_HISTORY = 1000


class StorageError(Exception):
    pass


class BrowserStorage:
    def __init__(self):
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        if xdg_data_home:
            base = Path(xdg_data_home).expanduser()
            if not base.is_absolute():
                raise StorageError("XDG_DATA_HOME must be an absolute path.")
        else:
            base = Path.home() / ".local" / "share"
        self.data_directory = base / "z-browser"

    @property
    def webkit_data_directory(self) -> Path:
        return self.data_directory / "webkit-data"

    @property
    def webkit_cache_directory(self) -> Path:
        return self.data_directory / "webkit-cache"

    def prepare_profile(self) -> None:
        try:
            self.webkit_data_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.webkit_cache_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.data_directory, 0o700)
            os.chmod(self.webkit_data_directory, 0o700)
            os.chmod(self.webkit_cache_directory, 0o700)
        except OSError as error:
            raise StorageError(f"Cannot prepare the browser profile: {error.strerror}") from error

    def load_bookmarks(self) -> list[dict[str, str]]:
        return self._load_records("bookmarks.json", "bookmarks", MAX_BOOKMARKS)

    def load_history(self) -> list[dict[str, str]]:
        return self._load_records("history.json", "history", MAX_HISTORY)

    def toggle_bookmark(self, title: str, uri: str) -> bool:
        bookmarks = self.load_bookmarks()
        for index, bookmark in enumerate(bookmarks):
            if bookmark["uri"] == uri:
                del bookmarks[index]
                self._save_records("bookmarks.json", "bookmarks", bookmarks)
                return False
        if len(bookmarks) >= MAX_BOOKMARKS:
            raise StorageError(f"You can save at most {MAX_BOOKMARKS} bookmarks.")
        bookmarks.insert(0, {"title": _clean_text(title, 200), "uri": _validate_uri(uri)})
        self._save_records("bookmarks.json", "bookmarks", bookmarks)
        return True

    def remove_bookmark(self, uri: str) -> None:
        bookmarks = self.load_bookmarks()
        updated = [bookmark for bookmark in bookmarks if bookmark["uri"] != uri]
        if len(updated) != len(bookmarks):
            self._save_records("bookmarks.json", "bookmarks", updated)

    def record_visit(self, title: str, uri: str) -> None:
        history = self.load_history()
        history.append(
            {
                "title": _clean_text(title, 200),
                "uri": _validate_uri(uri),
                "visited_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        self._save_records("history.json", "history", history[-MAX_HISTORY:])

    def _load_records(
        self, filename: str, key: str, maximum: int
    ) -> list[dict[str, str]]:
        path = self.data_directory / filename
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            return []
        except OSError as error:
            raise StorageError(f"Cannot read {filename}: {error.strerror}") from error
        if len(content) > MAX_STORE_BYTES:
            raise StorageError(f"{filename} is too large to read safely.")
        try:
            document = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise StorageError(f"{filename} is corrupt; it was not changed.") from error
        if (
            not isinstance(document, dict)
            or set(document) != {"format_version", key}
            or type(document["format_version"]) is not int
            or document["format_version"] != 1
            or not isinstance(document[key], list)
            or len(document[key]) > maximum
        ):
            raise StorageError(f"{filename} has an unsupported or invalid format.")

        records = []
        for item in document[key]:
            if not isinstance(item, dict):
                raise StorageError(f"{filename} contains an invalid record.")
            if key == "bookmarks":
                if set(item) != {"title", "uri"}:
                    raise StorageError(f"{filename} contains an invalid bookmark.")
                records.append(
                    {
                        "title": _clean_text(item["title"], 200),
                        "uri": _validate_uri(item["uri"]),
                    }
                )
            else:
                if set(item) != {"title", "uri", "visited_at"}:
                    raise StorageError(f"{filename} contains an invalid history record.")
                visited_at = item["visited_at"]
                if not isinstance(visited_at, str) or len(visited_at) > 40:
                    raise StorageError(f"{filename} contains an invalid visit time.")
                try:
                    datetime.fromisoformat(visited_at)
                except ValueError as error:
                    raise StorageError(f"{filename} contains an invalid visit time.") from error
                records.append(
                    {
                        "title": _clean_text(item["title"], 200),
                        "uri": _validate_uri(item["uri"]),
                        "visited_at": visited_at,
                    }
                )
        return records

    def _save_records(self, filename: str, key: str, records: list[dict[str, str]]) -> None:
        document = {"format_version": 1, key: records}
        content = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(content) > MAX_STORE_BYTES:
            raise StorageError(f"{filename} would exceed the storage size limit.")
        try:
            self.data_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.data_directory, 0o700)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{filename}.", dir=self.data_directory
            )
            try:
                with os.fdopen(descriptor, "wb") as temporary_file:
                    temporary_file.write(content)
                    temporary_file.flush()
                    os.fsync(temporary_file.fileno())
                os.chmod(temporary_name, 0o600)
                os.replace(temporary_name, self.data_directory / filename)
            except OSError:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
                raise
        except OSError as error:
            raise StorageError(f"Cannot save {filename}: {error.strerror}") from error


def _validate_uri(uri: object) -> str:
    if not isinstance(uri, str) or len(uri) > 4096:
        raise StorageError("Stored address is invalid.")
    try:
        return validate_web_url(uri)
    except (UnicodeError, ValueError) as error:
        raise StorageError("Stored address is invalid.") from error


def _clean_text(value: object, maximum: int) -> str:
    if not isinstance(value, str):
        raise StorageError("Stored text is invalid.")
    return "".join(character for character in value if character.isprintable())[:maximum]
