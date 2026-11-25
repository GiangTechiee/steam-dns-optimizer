from __future__ import annotations

import json
import msvcrt
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def data_directory() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "SteamDNSOptimizer"


def read_json(path: Path, default: dict, *, lock_held: bool = False) -> dict:
    """Healthy snapshots are atomic and readable even during a network change."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return default
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict) or "version" not in data:
        if not lock_held:
            with locked(path):
                # Re-read: the writer may have replaced the file before we acquired the lock.
                return read_json(path, default, lock_held=True)
        archive_json(path, raw)
        write_json(path, default)
        return default
    if data["version"] != 1:
        raise ValueError(f"Định dạng {path.name} không được hỗ trợ.")
    return data


def archive_json(path: Path, raw: bytes) -> None:
    number = 0
    while True:
        suffix = ".bak" if number == 0 else f".{number}.bak"
        try:
            handle = path.with_name(path.name + suffix).open("xb")
        except FileExistsError:
            number += 1
            continue
        with handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        return


@contextmanager
def locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as handle:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("Một cửa sổ khác đang cập nhật dữ liệu. Hãy thử lại.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", delete=False) as handle:
        temp = Path(handle.name)
        try:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
