from __future__ import annotations

import errno
import math
import os
import stat
import threading
import time
import unicodedata
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Iterator

from mdex.path_identity import canonical_path_key


DEFAULT_DB_LOCK_TIMEOUT_SECONDS = 30.0
DEFAULT_DB_LOCK_POLL_SECONDS = 0.05


class DbLockTimeoutError(TimeoutError):
    pass


_THREAD_LOCKS: dict[str, Any] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_CONTENTION_ERRNOS = {
    errno.EACCES,
    errno.EAGAIN,
    getattr(errno, "EDEADLK", errno.EACCES),
}


def db_lock_path(db_path: str | Path) -> Path:
    resolved = Path(db_path).resolve()
    canonical_name = unicodedata.normalize("NFC", resolved.name).casefold()
    return resolved.with_name(f".{canonical_name}.lock")


def resource_lock_path(resource_path: str | Path) -> Path:
    """Return the persistent lock file for a generated resource."""

    return db_lock_path(resource_path)


def _thread_lock_for(path: Path) -> Any:
    key = canonical_path_key(path)
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


def _validate_timeout(timeout: float) -> float:
    value = float(timeout)
    if not math.isfinite(value) or value < 0:
        raise ValueError("database lock timeout must be a finite non-negative number")
    return value


def _open_lock_file(path: Path) -> BinaryIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError(f"database lock path must not be a symlink: {path}")

    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise OSError(f"database lock path must be a regular file: {path}")
        if file_stat.st_nlink != 1:
            raise OSError(f"database lock path must not have multiple hard links: {path}")
        if file_stat.st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        return os.fdopen(fd, "r+b", buffering=0)
    except Exception:
        os.close(fd)
        raise


def _try_acquire_os_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_os_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _acquire_os_lock(
    handle: BinaryIO,
    *,
    deadline: float,
    timeout: float,
    poll_interval: float,
    lock_path: Path,
) -> None:
    while True:
        try:
            _try_acquire_os_lock(handle)
            return
        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            if exc.errno not in _CONTENTION_ERRNOS:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DbLockTimeoutError(
                    f"timed out after {timeout:g}s waiting for database lock: {lock_path}"
                ) from exc
            time.sleep(min(poll_interval, remaining))


@contextmanager
def exclusive_resource_lock(
    resource_path: str | Path,
    *,
    timeout: float = DEFAULT_DB_LOCK_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_DB_LOCK_POLL_SECONDS,
) -> Iterator[Path]:
    safe_timeout = _validate_timeout(timeout)
    safe_poll_interval = _validate_timeout(poll_interval)
    if safe_poll_interval == 0:
        safe_poll_interval = DEFAULT_DB_LOCK_POLL_SECONDS

    lock_path = resource_lock_path(resource_path)
    deadline = time.monotonic() + safe_timeout
    thread_lock = _thread_lock_for(lock_path)
    if not thread_lock.acquire(timeout=safe_timeout):
        raise DbLockTimeoutError(
            f"timed out after {safe_timeout:g}s waiting for database lock: {lock_path}"
        )

    handle: BinaryIO | None = None
    os_lock_acquired = False
    try:
        handle = _open_lock_file(lock_path)
        _acquire_os_lock(
            handle,
            deadline=deadline,
            timeout=safe_timeout,
            poll_interval=safe_poll_interval,
            lock_path=lock_path,
        )
        os_lock_acquired = True
        yield lock_path
    finally:
        try:
            if handle is not None and os_lock_acquired:
                _release_os_lock(handle)
        finally:
            try:
                if handle is not None:
                    handle.close()
            finally:
                thread_lock.release()


@contextmanager
def exclusive_resource_locks(
    resource_paths: Iterable[str | Path],
    *,
    timeout: float = DEFAULT_DB_LOCK_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_DB_LOCK_POLL_SECONDS,
) -> Iterator[tuple[Path, ...]]:
    """Lock multiple resources in canonical order under one total timeout.

    Sorting and de-duplicating the canonical resource paths prevents lock-order
    deadlocks.  Sharing either a database or JSON output therefore serializes a
    complete scan pair, even when the other output differs.
    """

    safe_timeout = _validate_timeout(timeout)
    safe_poll_interval = _validate_timeout(poll_interval)
    canonical: dict[str, Path] = {}
    for value in resource_paths:
        resolved = Path(value).resolve()
        canonical[canonical_path_key(resolved)] = resolved
    ordered = tuple(canonical[key] for key in sorted(canonical))
    deadline = time.monotonic() + safe_timeout

    with ExitStack() as stack:
        for resource in ordered:
            remaining = max(0.0, deadline - time.monotonic())
            stack.enter_context(
                exclusive_resource_lock(
                    resource,
                    timeout=remaining,
                    poll_interval=safe_poll_interval,
                )
            )
        yield ordered


@contextmanager
def exclusive_db_lock(
    db_path: str | Path,
    *,
    timeout: float = DEFAULT_DB_LOCK_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_DB_LOCK_POLL_SECONDS,
) -> Iterator[Path]:
    with exclusive_resource_lock(
        db_path,
        timeout=timeout,
        poll_interval=poll_interval,
    ) as lock_path:
        yield lock_path
