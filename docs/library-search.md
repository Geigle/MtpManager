# Library fuzzy search

Toolbar **Search** filters Music, Video, and Audiobooks from the in-memory library index.

## Behavior

| Mode | Result layout | Order |
|------|----------------|--------|
| **No query** | Normal grouped library (artist/album/year/… headers) | Current column sort |
| **Active query** | **Flat** list — **no** group headers | Strongest match first |

Clear the search (× or Escape in the field) to restore the full grouped view.

Debounce is ~200 ms. **⌘F / Ctrl+F** focuses the search entry.

### Debug scores

With `MTP_MANAGER_DEBUG=1` (or `-v` / `--verbose`), the `#0` column left of Title shows the numeric match score (`score` heading) while a filter is active.

## Free text

Tokens match across metadata fields with **equal** base weights (no default artist boost):

- title, artist, album artist, album, genre, composer, path/filename

Multi-word free text uses **AND** (every token must match somewhere). Matching is fuzzy (substring preferred; character-subsequence for light typos).

## Field keywords

Prefix a term with a field name to **require** a match on that field and **boost** that field’s ranking weight (×3 vs base).

| Keyword | Metadata |
|---------|----------|
| `artist:` | Track artist / primary artist |
| `albumartist:` | Album artist |
| `album:` | Album title |
| `title:` | Track title |
| `genre:` | Genre |
| `composer:` | Composer |
| `path:` / `file:` / `filename:` | Path and basename |

Examples:

```text
nightwish
artist:nightwish
artist:iron maiden album:powerslave
title:countdown europe
artist:"blind guardian"
```

- Quoted terms: `artist:"blind guardian"`.
- Unknown `field:` prefixes are treated as ordinary free text.
- Combine free text with keywords: `artist:nightwish ghost` (artist must match Nightwish; “ghost” matches any field).

## Implementation

| Piece | Role |
|-------|------|
| `mtpmanager/domain/library_search.py` | Parse, score, filter; module docstring is the API summary |
| `mtpmanager/ui/window.py` | Toolbar entry, clear, focus shortcuts |
| `mtpmanager/ui/controllers.py` | Debounce; flat rebuild while filtering |
| `tests/test_library_search.py` | Unit tests |

See also [AGENTS.md](../AGENTS.md) change surface **Library fuzzy search**.
