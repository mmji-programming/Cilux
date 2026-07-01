"""
path_check.py
=============
A single-function path validation utility built on pathlib.

USAGE
-----
    from path_check import path_check, PathOp

    path_check("output.v", PathOp.EXISTS_FILE)
    path_check("output.v", PathOp.MATCH_EXTENSION, ext=".v")
    path_check("some/dir",  PathOp.DIR_HAS_FILE,   name="config.json")

On success  -> returns Path object of the resolved path.
On failure  -> raises PathCheckError with a descriptive message.

KEYWORD ARGUMENTS (used only by specific operations)
-----------------------------------------------------
    ext       (str)            Single extension to match, e.g. ".v" or "v".
    exts      (set[str])       Set of allowed extensions, e.g. {".v", ".sv"}.
    name      (str)            Exact filename or subdirectory name, e.g. "main.py".
    pattern   (str)            Glob pattern for name matching, e.g. "*.blif".
    text      (str)            Substring or prefix/suffix text to search in file content.
    header    (bytes)          Byte sequence to match at the start of a file (magic bytes).
    checksum  (str)            Hex digest string for MD5 or SHA-256 comparison.
    bytes     (int)            File size threshold in bytes for SIZE_* operations.
    count     (int)            Numeric threshold for DIR_FILE_COUNT_* or DEPTH_EQ.
    root      (str | Path)     Ancestor path used in IS_UNDER check.
    other     (str | Path)     Second path used in SAME_AS check.
    encoding  (str)            Text encoding for content operations. Default: "utf-8".
"""

from pathlib import Path
from enum import Enum, auto
from typing import Optional, Union
import os
import hashlib


class PathCheckError(Exception):
    __module__ = Exception.__module__

    def __init__(self, op: "PathOp", path: Path, message: str, detail: Optional[str] = None):
        self.op = op
        self.path = path
        self.message = message
        self.detail = detail
        full = f"[{op.name}] {path} — {message}"
        if detail:
            full += f" ({detail})"
        super().__init__(full)


class PathOp(Enum):
    # Existence
    EXISTS = auto()  # Path exists (file or directory).
    EXISTS_FILE = auto()  # Path exists and is a regular file.
    EXISTS_DIR = auto()  # Path exists and is a directory.
    EXISTS_SYMLINK = auto()  # Path exists and is a symbolic link.

    # Extension
    HAS_EXTENSION = auto()  # Path has any extension.
    MATCH_EXTENSION = auto()  # Extension equals `ext`.
    MATCH_EXTENSIONS = auto()  # Extension is one of `exts`.

    # Name
    MATCH_NAME = auto()  # Filename (with extension) equals `name`.
    MATCH_STEM = auto()  # Filename without extension equals `name`.
    MATCH_PATTERN = auto()  # Filename matches glob `pattern`.

    # Permissions
    IS_READABLE = auto()  # Current process can read the path.
    IS_WRITABLE = auto()  # Current process can write the path.
    IS_EXECUTABLE = auto()  # Current process can execute the path.

    # Size
    IS_EMPTY = auto()  # File is 0 bytes, or directory has no entries.
    IS_NOT_EMPTY = auto()  # File is >0 bytes, or directory has at least one entry.
    SIZE_LT = auto()  # File size in bytes is strictly less than `bytes`.
    SIZE_GT = auto()  # File size in bytes is strictly greater than `bytes`.
    SIZE_EQ = auto()  # File size in bytes equals `bytes`.

    # Content
    CONTAINS_TEXT = auto()  # File text content contains `text` as a substring.
    STARTS_WITH_TEXT = auto()  # First line of file starts with `text`.
    ENDS_WITH_TEXT = auto()  # Last line of file ends with `text`.
    MATCH_SUFFIX_BYTES = auto()  # First N bytes of file equal `header` (magic bytes check).

    # Hash
    MATCH_MD5 = auto()  # MD5 hex digest of file equals `checksum`.
    MATCH_SHA256 = auto()  # SHA-256 hex digest of file equals `checksum`.

    # Directory contents
    DIR_IS_EMPTY = auto()  # Directory exists and contains no entries.
    DIR_HAS_FILE = auto()  # Directory contains a file named `name`.
    DIR_HAS_SUBDIR = auto()  # Directory contains a subdirectory named `name`.
    DIR_FILE_COUNT_GT = auto()  # Number of direct files in directory is greater than `count`.
    DIR_FILE_COUNT_LT = auto()  # Number of direct files in directory is less than `count`.

    # Path structure
    IS_ABSOLUTE = auto()  # Path string is absolute.
    IS_RELATIVE = auto()  # Path string is relative.
    PARENT_EXISTS = auto()  # The immediate parent directory exists.
    HAS_PARENT = auto()  # Path has a parent (i.e. is not a root).
    DEPTH_EQ = auto()  # Number of path components equals `count`.

    # Relation
    IS_UNDER = auto()  # Resolved path is a descendant of `root`.
    SAME_AS = auto()  # Resolved path points to the same target as `other`.


def path_check(
    path: Union[str, Path],
    op: PathOp,
    *,
    ext: Optional[str] = None,
    exts: Optional[set] = None,
    name: Optional[str] = None,
    pattern: Optional[str] = None,
    text: Optional[str] = None,
    header: Optional[bytes] = None,
    checksum: Optional[str] = None,
    bytes: Optional[int] = None,
    count: Optional[int] = None,
    root: Optional[Union[str, Path]] = None,
    other: Optional[Union[str, Path]] = None,
    encoding: str = "utf-8",
) -> Path:

    p = Path(path)

    def err(message: str, detail: Optional[str] = None) -> PathCheckError:
        return PathCheckError(op, p, message, detail)

    #  Existence
    if op == PathOp.EXISTS:
        if not p.exists():
            raise err("Path does not exist.")
        return p

    if op == PathOp.EXISTS_FILE:
        if not p.exists():
            raise err("Path does not exist.")
        if not p.is_file():
            kind = "directory" if p.is_dir() else "symlink" if p.is_symlink() else "unknown"
            raise err("Path exists but is not a file.", f"Actual type: {kind}")
        return p

    if op == PathOp.EXISTS_DIR:
        if not p.exists():
            raise err("Path does not exist.")
        if not p.is_dir():
            kind = "file" if p.is_file() else "symlink" if p.is_symlink() else "unknown"
            raise err("Path exists but is not a directory.", f"Actual type: {kind}")
        return p

    if op == PathOp.EXISTS_SYMLINK:
        if not p.is_symlink():
            raise err("Path is not a symbolic link or does not exist.")
        return p

    #  Extension
    if op == PathOp.HAS_EXTENSION:
        if not p.suffix:
            raise err("Path has no extension.")
        return p

    if op == PathOp.MATCH_EXTENSION:
        if ext is None:
            raise err("Missing required argument: ext.")
        expected = (ext if ext.startswith(".") else f".{ext}").lower()
        actual = p.suffix.lower()
        if actual != expected:
            raise err(
                "File extension does not match.",
                f"Expected '{expected}', got '{p.suffix or 'none'}'",
            )
        return p

    if op == PathOp.MATCH_EXTENSIONS:
        if not exts:
            raise err("Missing required argument: exts.")
        normalized = {(e if e.startswith(".") else f".{e}").lower() for e in exts}
        if p.suffix.lower() not in normalized:
            raise err(
                "File extension is not in the allowed set.",
                f"Got '{p.suffix or 'none'}', allowed: {sorted(normalized)}",
            )
        return p

    #  Name
    if op == PathOp.MATCH_NAME:
        if name is None:
            raise err("Missing required argument: name.")
        if p.name != name:
            raise err("Filename does not match.", f"Expected '{name}', got '{p.name}'")
        return p

    if op == PathOp.MATCH_STEM:
        if name is None:
            raise err("Missing required argument: name.")
        if p.stem != name:
            raise err("Filename stem does not match.", f"Expected '{name}', got '{p.stem}'")
        return p

    if op == PathOp.MATCH_PATTERN:
        if pattern is None:
            raise err("Missing required argument: pattern.")
        if not p.match(pattern):
            raise err(f"Filename does not match pattern '{pattern}'.", f"Got '{p.name}'")
        return p

    #  Permissions
    if op == PathOp.IS_READABLE:
        if not p.exists():
            raise err("Path does not exist.")
        if not os.access(p, os.R_OK):
            raise err("Path is not readable by the current process.")
        return p

    if op == PathOp.IS_WRITABLE:
        if not p.exists():
            raise err("Path does not exist.")
        if not os.access(p, os.W_OK):
            raise err("Path is not writable by the current process.")
        return p

    if op == PathOp.IS_EXECUTABLE:
        if not p.exists():
            raise err("Path does not exist.")
        if not os.access(p, os.X_OK):
            raise err("Path is not executable by the current process.")
        return p

    #  Size
    if op == PathOp.IS_EMPTY:
        if not p.exists():
            raise err("Path does not exist.")
        if p.is_file():
            size = p.stat().st_size
            if size != 0:
                raise err("File is not empty.", f"Size: {size} bytes")
        elif p.is_dir():
            entries = list(p.iterdir())
            if entries:
                raise err("Directory is not empty.", f"{len(entries)} entries found")
        else:
            raise err("Path is neither a file nor a directory.")
        return p

    if op == PathOp.IS_NOT_EMPTY:
        if not p.exists():
            raise err("Path does not exist.")
        if p.is_file():
            size = p.stat().st_size
            if size == 0:
                raise err("File is empty.")
        elif p.is_dir():
            if not any(p.iterdir()):
                raise err("Directory is empty.")
        else:
            raise err("Path is neither a file nor a directory.")
        return p

    if op == PathOp.SIZE_LT:
        if bytes is None:
            raise err("Missing required argument: bytes.")
        if not p.is_file():
            raise err("Path is not a file or does not exist.")
        size = p.stat().st_size
        if size >= bytes:
            raise err(
                f"File size is not less than {bytes} bytes.",
                f"Actual size: {size} bytes",
            )
        return p

    if op == PathOp.SIZE_GT:
        if bytes is None:
            raise err("Missing required argument: bytes.")
        if not p.is_file():
            raise err("Path is not a file or does not exist.")
        size = p.stat().st_size
        if size <= bytes:
            raise err(
                f"File size is not greater than {bytes} bytes.",
                f"Actual size: {size} bytes",
            )
        return p

    if op == PathOp.SIZE_EQ:
        if bytes is None:
            raise err("Missing required argument: bytes.")
        if not p.is_file():
            raise err("Path is not a file or does not exist.")
        size = p.stat().st_size
        if size != bytes:
            raise err("File size does not match.", f"Expected {bytes} bytes, got {size} bytes")
        return p

    #  Content
    if op == PathOp.CONTAINS_TEXT:
        if text is None:
            raise err("Missing required argument: text.")
        if not p.is_file():
            raise err("Path is not a file or does not exist.")
        try:
            content = p.read_text(encoding=encoding)
        except Exception as e:
            raise err("Failed to read file.", str(e))
        if text not in content:
            preview = text[:60] + ("..." if len(text) > 60 else "")
            raise err(
                "File does not contain the specified text.",
                f"Searched for: '{preview}'",
            )
        return p

    if op == PathOp.STARTS_WITH_TEXT:
        if text is None:
            raise err("Missing required argument: text.")
        if not p.is_file():
            raise err("Path is not a file or does not exist.")
        try:
            first_line = p.open(encoding=encoding).readline()
        except Exception as e:
            raise err("Failed to read file.", str(e))
        if not first_line.startswith(text):
            raise err(
                "File does not start with the specified text.",
                f"First line: '{first_line[:80].strip()}'",
            )
        return p

    if op == PathOp.ENDS_WITH_TEXT:
        if text is None:
            raise err("Missing required argument: text.")
        if not p.is_file():
            raise err("Path is not a file or does not exist.")
        try:
            lines = p.read_text(encoding=encoding).splitlines()
        except Exception as e:
            raise err("Failed to read file.", str(e))
        last = lines[-1] if lines else ""
        if not last.endswith(text):
            raise err(
                "File does not end with the specified text.",
                f"Last line: '{last[:80]}'",
            )
        return p

    if op == PathOp.MATCH_SUFFIX_BYTES:
        if header is None:
            raise err("Missing required argument: header.")
        if not p.is_file():
            raise err("Path is not a file or does not exist.")
        try:
            actual = p.read_bytes()[: len(header)]
        except Exception as e:
            raise err("Failed to read file bytes.", str(e))
        if actual != header:
            raise err(
                "File header (magic bytes) does not match.",
                f"Expected {header.hex()}, got {actual.hex()}",
            )
        return p

    #  Hash
    if op == PathOp.MATCH_MD5:
        if checksum is None:
            raise err("Missing required argument: checksum.")
        if not p.is_file():
            raise err("Path is not a file or does not exist.")
        try:
            actual = hashlib.md5(p.read_bytes()).hexdigest()
        except Exception as e:
            raise err("Failed to compute MD5.", str(e))
        if actual != checksum.lower():
            raise err("MD5 checksum does not match.", f"Expected {checksum}, got {actual}")
        return p

    if op == PathOp.MATCH_SHA256:
        if checksum is None:
            raise err("Missing required argument: checksum.")
        if not p.is_file():
            raise err("Path is not a file or does not exist.")
        try:
            actual = hashlib.sha256(p.read_bytes()).hexdigest()
        except Exception as e:
            raise err("Failed to compute SHA-256.", str(e))
        if actual != checksum.lower():
            raise err("SHA-256 checksum does not match.", f"Expected {checksum}, got {actual}")
        return p

    #  Directory contents
    if op == PathOp.DIR_IS_EMPTY:
        if not p.is_dir():
            raise err("Path is not a directory or does not exist.")
        entries = list(p.iterdir())
        if entries:
            raise err("Directory is not empty.", f"{len(entries)} entries found")
        return p

    if op == PathOp.DIR_HAS_FILE:
        if name is None:
            raise err("Missing required argument: name.")
        if not p.is_dir():
            raise err("Path is not a directory or does not exist.")
        target = p / name
        if not target.is_file():
            raise err(f"File '{name}' not found in directory.")
        return p

    if op == PathOp.DIR_HAS_SUBDIR:
        if name is None:
            raise err("Missing required argument: name.")
        if not p.is_dir():
            raise err("Path is not a directory or does not exist.")
        target = p / name
        if not target.is_dir():
            raise err(f"Subdirectory '{name}' not found in directory.")
        return p

    if op == PathOp.DIR_FILE_COUNT_GT:
        if count is None:
            raise err("Missing required argument: count.")
        if not p.is_dir():
            raise err("Path is not a directory or does not exist.")
        n = sum(1 for x in p.iterdir() if x.is_file())
        if n <= count:
            raise err(
                f"Directory does not have more than {count} files.",
                f"Actual count: {n}",
            )
        return p

    if op == PathOp.DIR_FILE_COUNT_LT:
        if count is None:
            raise err("Missing required argument: count.")
        if not p.is_dir():
            raise err("Path is not a directory or does not exist.")
        n = sum(1 for x in p.iterdir() if x.is_file())
        if n >= count:
            raise err(
                f"Directory does not have fewer than {count} files.",
                f"Actual count: {n}",
            )
        return p

    #  Path structure
    if op == PathOp.IS_ABSOLUTE:
        if not p.is_absolute():
            raise err("Path is not absolute.", f"Got: '{p}'")
        return p

    if op == PathOp.IS_RELATIVE:
        if p.is_absolute():
            raise err("Path is not relative.", f"Got: '{p}'")
        return p

    if op == PathOp.PARENT_EXISTS:
        if not p.parent.exists():
            raise err("Parent directory does not exist.", f"Parent: '{p.parent}'")
        return p

    if op == PathOp.HAS_PARENT:
        if p.parent == p:
            raise err("Path has no parent (is a root).")
        return p

    if op == PathOp.DEPTH_EQ:
        if count is None:
            raise err("Missing required argument: count.")
        depth = len(p.parts)
        if depth != count:
            raise err("Path depth does not match.", f"Expected {count}, got {depth}")
        return p

    #  Relation
    if op == PathOp.IS_UNDER:
        if root is None:
            raise err("Missing required argument: root.")
        try:
            p.resolve().relative_to(Path(root).resolve())
        except ValueError:
            raise err(f"Path is not under '{root}'.", f"Resolved path: '{p.resolve()}'")
        return p

    if op == PathOp.SAME_AS:
        if other is None:
            raise err("Missing required argument: other.")
        try:
            if p.resolve() != Path(other).resolve():
                raise err(
                    "Paths do not resolve to the same target.",
                    f"Left: '{p.resolve()}', Right: '{Path(other).resolve()}'",
                )
        except PathCheckError:
            raise
        except Exception as e:
            raise err("Failed to resolve paths for comparison.", str(e))
        return p

    raise err(f"Unknown operation: {op}")
