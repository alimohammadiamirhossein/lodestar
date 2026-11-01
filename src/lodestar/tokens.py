"""Rough token estimates.

These are for planning and for the savings ledger. They are not what a tokenizer would count or what
a bill would say: text is taken as 4 bytes per token, and a directory listing as 8 tokens per entry.
"""

from __future__ import annotations

import math

BYTES_PER_TOKEN = 4
PATH_TOKENS = 8   # what listing one entry (a name on its own line) costs

# Files with no useful text to estimate: media, binaries and office or PDF documents. Their text
# tokens count as 0, which means "not estimated", not "free to read".
BINARY = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".mp4", ".avi", ".mov",
    ".mkv", ".webm", ".wav", ".mp3", ".flac", ".ckpt", ".pt", ".pth", ".safetensors", ".bin",
    ".onnx", ".npy", ".npz", ".h5", ".hdf5", ".parquet", ".tfrecord", ".arrow", ".lmdb", ".mat",
    ".pkl", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".whl", ".so", ".dll", ".exe",
    ".pyc", ".o", ".a", ".class", ".jar", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".db",
    ".sqlite", ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".psd", ".ai", ".eps",
    ".blend", ".fbx", ".glb", ".obj", ".ply", ".mp4v",
})


def file_tokens(size: int, ext: str) -> int:
    """Tokens needed to read a file of this size and extension as text."""
    if size <= 0 or ext.lower() in BINARY:
        return 0
    return math.ceil(size / BYTES_PER_TOKEN)


def text_tokens(text: str) -> int:
    return math.ceil(len(text) / BYTES_PER_TOKEN)


def human(n: int) -> str:
    n = int(n)
    if abs(n) < 1000:
        return str(n)
    if abs(n) < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"
