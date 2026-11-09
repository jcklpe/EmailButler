#!/usr/bin/env python3
"""Shared private-storage and value-validation mechanics owned by Setup EmailButler."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from contextlib import contextmanager


class ButlerError(ValueError):
    pass


EMAIL = re.compile(r"[^\s@*?<>]+@[^\s@*?<>]+\.[^\s@*?<>]+\Z")


def require(condition, message):
    if not condition:
        raise ButlerError(message)


def fields(value, required, optional=()):
    require(type(value) is dict, "Expected an object")
    require(set(required) <= value.keys(), "Missing required fields: " + ", ".join(sorted(set(required) - value.keys())))
    require(value.keys() <= set(required) | set(optional), "Unknown fields: " + ", ".join(sorted(value.keys() - set(required) - set(optional))))


def text_value(value, name, limit=1000):
    require(type(value) is str and 0 < len(value.strip()) <= limit, f"{name} must be nonempty text of at most {limit} characters")
    require(not any(ord(c) < 32 for c in value), f"{name} cannot contain control characters")


def email(value, name="email"):
    require(type(value) is str and len(value) <= 254 and EMAIL.fullmatch(value) is not None, f"{name} must be a complete email address without wildcards")


def integer(value, low, high, name):
    require(type(value) is int and low <= value <= high, f"{name} must be an integer from {low} to {high}")


def account_provider(value):
    email(value["account"], "account")
    require(value["provider"] in ("gmail", "outlook"), "provider must be gmail or outlook")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def no_symlinks(path):
    path = Path(os.path.abspath(path))
    for candidate in (path, *path.parents):
        require(not candidate.is_symlink(), "State/input paths cannot contain symlinks")
    return path


def load_json(path):
    path = no_symlinks(path)
    require(path.is_file() and path.stat().st_size <= 1_000_000, "Expected a JSON file of at most 1 MB")
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON key: " + key)
            result[key] = value
        return result
    with path.open(encoding="utf-8") as stream:
        return json.load(stream, object_pairs_hook=unique_pairs)


def atomic_json(path, value):
    path = no_symlinks(path)
    encoded = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    require(len(encoded.encode("utf-8")) <= 1_000_000, "State file exceeds the 1 MB read/write limit")
    fd, temporary = tempfile.mkstemp(prefix=".butler-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def private_directory(path, create=False):
    path = no_symlinks(path)
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(path.is_dir(), "Private state directory is missing; run init")
    if os.name == "posix":
        require(path.stat().st_mode & 0o077 == 0, "State directory must be private (owner-only permissions)")
    return path


@contextmanager
def locked(root):
    lock = no_symlinks(root / ".lock")
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ButlerError("State is locked; inspect the active writer instead of deleting its lock") from None
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink()


def default_state_directory():
    return Path.home() / ".email-butler"
