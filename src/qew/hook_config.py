"""Safe configuration mutation primitives for agent hook adapters."""

from contextlib import contextmanager
import copy
import errno
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any, Callable, Dict, Iterator, Optional, Sequence, Tuple

from qew.errors import QewError


JsonTransform = Callable[[Dict[str, Any]], Dict[str, Any]]


def update_json_config(
    root: Path,
    relative: Sequence[str],
    transform: JsonTransform,
    *,
    create: bool = True,
) -> bool:
    try:
        return _update_json(root, relative, transform, create)
    except _MissingParent:
        return False


def _update_json(
    root: Path,
    relative: Sequence[str],
    transform: JsonTransform,
    create: bool,
) -> bool:
    with _parent_directory(root, relative, create=create) as target:
        directory, name = target
        existing = _read_regular_file(directory, name)
        if existing is None:
            current: Dict[str, Any] = {}
            mode = 0o600
        else:
            raw, mode = existing
            try:
                decoded = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise QewError(f"invalid JSON configuration {name}: {error}", 2) from error
            if not isinstance(decoded, dict):
                raise QewError(f"JSON configuration {name} must contain an object", 2)
            current = decoded
        updated = transform(copy.deepcopy(current))
        if not isinstance(updated, dict):
            raise QewError("JSON configuration transform must return an object", 2)
        if updated == current:
            return False
        rendered = (
            json.dumps(updated, ensure_ascii=False, indent=2, separators=(",", ": "))
            + "\n"
        ).encode("utf-8")
        _atomic_write(directory, name, rendered, mode)
        return True


def install_managed_file(
    root: Path,
    relative: Sequence[str],
    content: str,
    marker: str,
) -> bool:
    _require_marker(content, marker)
    encoded = content.encode("utf-8")
    with _parent_directory(root, relative, create=True) as target:
        directory, name = target
        existing = _read_regular_file(directory, name)
        if existing is None:
            mode = 0o600
        else:
            raw, mode = existing
            if raw == encoded:
                return False
            _require_owned(raw, marker, name)
        _atomic_write(directory, name, encoded, mode)
        return True


def remove_managed_file(
    root: Path,
    relative: Sequence[str],
    marker: str,
) -> bool:
    """Delete the file only when it carries the marker; a file that is not Qew's is left alone."""
    if not marker:
        raise QewError("managed-file marker must not be empty", 2)
    try:
        with _parent_directory(root, relative, create=False) as target:
            directory, name = target
            existing = _read_regular_file(directory, name)
            if existing is None:
                return False
            raw, _ = existing
            if marker.encode("utf-8") not in raw:
                return False
            try:
                os.unlink(name, dir_fd=directory)
                os.fsync(directory)
            except OSError as error:
                raise QewError(f"cannot remove managed file {name}: {error}", 2) from error
            return True
    except _MissingParent:
        return False


class _MissingParent(Exception):
    pass


@contextmanager
def _parent_directory(
    root: Path,
    relative: Sequence[str],
    *,
    create: bool,
) -> Iterator[Tuple[int, str]]:
    parts = _relative_parts(relative)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if root.is_symlink():
        raise QewError(f"refusing symlink root: {root}", 2)
    try:
        directory = os.open(str(root), flags)
    except OSError as error:
        raise QewError(f"cannot open configuration root without symlinks: {error}", 2) from error
    try:
        for component in parts[:-1]:
            try:
                details = os.stat(component, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    raise _MissingParent()
                try:
                    os.mkdir(component, mode=0o700, dir_fd=directory)
                    os.fsync(directory)
                    details = os.stat(component, dir_fd=directory, follow_symlinks=False)
                except OSError as error:
                    raise QewError(f"cannot create configuration directory {component}: {error}", 2) from error
            if stat.S_ISLNK(details.st_mode):
                raise QewError(f"refusing symlink directory: {component}", 2)
            if not stat.S_ISDIR(details.st_mode):
                raise QewError(f"configuration parent is not a directory: {component}", 2)
            try:
                child = os.open(component, flags, dir_fd=directory)
            except OSError as error:
                raise QewError(
                    f"cannot open configuration directory without symlinks: {component}: {error}",
                    2,
                ) from error
            os.close(directory)
            directory = child
        yield directory, parts[-1]
    finally:
        os.close(directory)


def _relative_parts(relative: Sequence[str]) -> Tuple[str, ...]:
    parts = tuple(relative)
    if not parts:
        raise QewError("configuration path must not be empty", 2)
    for component in parts:
        if (
            not isinstance(component, str)
            or not component
            or component in (".", "..")
            or os.sep in component
            or (os.altsep is not None and os.altsep in component)
        ):
            raise QewError("configuration path must be a relative component list", 2)
    return parts


def _read_regular_file(
    directory: int,
    name: str,
) -> Optional[Tuple[bytes, int]]:
    try:
        details = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(details.st_mode):
        raise QewError(f"refusing symlink configuration file: {name}", 2)
    if not stat.S_ISREG(details.st_mode):
        raise QewError(f"configuration target is not a regular file: {name}", 2)
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as source:
            raw = source.read()
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise QewError(f"refusing symlink configuration file: {name}", 2) from error
        raise QewError(f"cannot read configuration file {name}: {error}", 2) from error
    return raw, stat.S_IMODE(details.st_mode)


def _atomic_write(directory: int, name: str, content: bytes, mode: int) -> None:
    temporary = f".{name}.{secrets.token_hex(8)}.tmp"
    descriptor = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            mode,
            dir_fd=directory,
        )
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = None
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        try:
            os.replace(
                temporary,
                name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
            )
        except OSError as error:
            raise QewError(f"cannot replace configuration file {name}: {error}", 2) from error
        os.fsync(directory)
    except OSError as error:
        raise QewError(f"cannot write configuration file {name}: {error}", 2) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _require_marker(content: str, marker: str) -> None:
    if not marker:
        raise QewError("managed-file marker must not be empty", 2)
    if marker not in content:
        raise QewError("managed-file content must contain its Qew marker", 2)


def _require_owned(raw: bytes, marker: str, name: str) -> None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise QewError(f"managed file {name} is not UTF-8", 2) from error
    if marker not in text:
        raise QewError(f"existing file {name} is not managed by Qew", 2)
