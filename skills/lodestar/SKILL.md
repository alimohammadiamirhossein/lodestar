---
name: lodestar
description: Locate files, folders, repos, datasets and experiment folders in a large indexed file tree before searching or listing directories yourself. Use when the user asks where something is, or when you would otherwise grep or list many folders.
---

# Ask Lodestar before searching

Lodestar holds an index of the user's file trees. It answers from the index and never reads file
contents, so an answer costs a few lines instead of a search through thousands of files.

1. `find` with words from the name or path, or an extension (`jpg`). Add `kind` to narrow it:
   `repo`, `experiment`, `dataset`, `checkpoints`, `media`, `docs`, `dir`, `file`.
2. `tree` on a result to see what is inside it, biggest first.
3. `info` for counts, size, extensions and sample names of one path.
4. Open only the files these name.

A `[collapsed]` entry stands for a folder with hundreds of files or many subfolders. Its files are
not indexed one by one, so list that folder if you need a specific file in it.

If `find` returns nothing or the answer looks stale, call `reindex` once, then ask again. The index
only covers roots the user has indexed; tell them to run `lodestar index <root>` for a new one.
