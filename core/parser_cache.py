import gzip
import hashlib
import io
import os
import pathlib
import platform
import sys
import tempfile
from typing import Any

import lark
from lark import Lark

from version import __version__

try:
    from platformdirs import user_cache_dir as _user_cache_dir

    _HAS_PLATFORMDIRS = True
except ImportError:
    _HAS_PLATFORMDIRS = False


try:
    import zstandard as _zstd

    def _compress(data: bytes) -> bytes:
        return _zstd.ZstdCompressor(level=3).compress(data)

    def _decompress(data: bytes) -> bytes:
        return _zstd.ZstdDecompressor().decompress(data)

    _EXT = ".lark.zst"

except ImportError:

    def _compress(data: bytes) -> bytes:
        return gzip.compress(data, compresslevel=3)

    def _decompress(data: bytes) -> bytes:
        return gzip.decompress(data)

    _EXT = ".lark.gz"


def _get_cache_dir() -> pathlib.Path:
    exe_path = pathlib.Path(sys.argv[0]).resolve()
    if exe_path.exists() and exe_path.is_file() and exe_path.suffix.lower() in (".exe", ""):
        candidate = exe_path.parent / "__parser_cache__"
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            test_file = candidate / ".write_test"
            test_file.write_text("ok")
            test_file.unlink()
            return candidate
        except (OSError, PermissionError):
            pass

    if _HAS_PLATFORMDIRS:
        candidate = pathlib.Path(_user_cache_dir("cilux", "cilux"))
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except (OSError, PermissionError):
            pass

    candidate = pathlib.Path(__file__).parent / "__parser_cache__"
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


_CACHE_DIR = _get_cache_dir()


def _ensure_dir() -> bool:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return True
    except OSError as e:
        print(f"[parser_cache] cannot create cache dir: {e}", file=sys.stderr)
        return False


def _compute_hash(kernel: Any, grammar: str) -> str:
    plugin_key = "|".join(sorted(kernel.plugins.keys()))
    raw = "\x00".join(
        [
            plugin_key,
            grammar,
            platform.python_version(),
            lark.__version__,
            "lalr",
            __version__,
        ]
    ).encode("utf-8")
    return hashlib.blake2b(raw, digest_size=16).hexdigest()


def _atomic_write(path: pathlib.Path, data: bytes) -> None:
    dir_ = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        pathlib.Path(tmp_path).replace(path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class _WrappedParser:
    def __init__(self, parser: Lark, transformer: Any) -> None:
        self._parser = parser
        self._transformer = transformer

    def parse(self, text: str, start: str | None = None, on_ambiguity=None):
        kw: dict = {}
        if start is not None:
            kw["start"] = start
        if on_ambiguity is not None:
            kw["on_ambiguity"] = on_ambiguity
        tree = self._parser.parse(text, **kw)
        return self._transformer.transform(tree)

    def __getattr__(self, name: str):
        return getattr(self._parser, name)


def _wrap(parser: Lark, transformer: Any) -> Lark | _WrappedParser:
    if transformer is None:
        return parser
    return _WrappedParser(parser, transformer)


def build_or_load_parser(kernel: Any) -> Lark | _WrappedParser:

    grammar = kernel.build_grammar()
    current_hash = _compute_hash(kernel, grammar)

    if _ensure_dir():
        hash_file = _CACHE_DIR / "grammar.hash"
        parser_file = _CACHE_DIR / ("parser" + _EXT)

        if hash_file.exists() and parser_file.exists():
            try:
                stored_hash = hash_file.read_text(encoding="utf-8").strip()
                if stored_hash == current_hash:
                    buf = io.BytesIO(_decompress(parser_file.read_bytes()))
                    parser = Lark.load(buf)
                    # print("[parser_cache] loaded from cache", file=sys.stderr)
                    return _wrap(parser, kernel.build_transformer())
                else:
                    # print("[parser_cache] hash mismatch — rebuilding", file=sys.stderr)
                    pass
            except Exception as e:
                print(
                    f"[parser_cache] cache load failed ({type(e).__name__}: {e}) — rebuilding",
                    file=sys.stderr,
                )

        parser = Lark(grammar, parser="lalr")

        try:
            buf = io.BytesIO()
            parser.save(buf)
            _atomic_write(parser_file, _compress(buf.getvalue()))
            _atomic_write(hash_file, current_hash.encode("utf-8"))
            # print("[parser_cache] cache written", file=sys.stderr)
        except Exception as e:
            print(f"[parser_cache] cache write failed: {e}", file=sys.stderr)

    else:
        # print("[parser_cache] running without cache", file=sys.stderr)
        parser = Lark(grammar, parser="lalr")

    return _wrap(parser, kernel.build_transformer())


def clear_cache() -> None:
    if not _CACHE_DIR.exists():
        print(f"[parser_cache] cache dir does not exist: {_CACHE_DIR}", file=sys.stderr)
        return
    removed = 0
    for f in _CACHE_DIR.glob("*"):
        try:
            f.unlink()
            removed += 1
        except OSError as e:
            print(f"[parser_cache] could not delete {f.name}: {e}", file=sys.stderr)
    print(f"[parser_cache] cleared {removed} file(s) from {_CACHE_DIR}", file=sys.stderr)
