# DJ Library Context

## Goal

This solo MVP keeps one understandable relationship between a Spotify recording, its researched genre, its BPM placement, its local audio file, and its Rekordbox entry.

The canonical taxonomy is:

```text
BPM master → main genre → optional subgenre
```

## Current decisions

- `music_schema_knowledge.json` is the taxonomy and routing source of truth.
- Playlist research is agent/user judgment supported by Spotify, Discogs, Last.fm, labels, stores, and public-web evidence.
- Metadata providers do not vote.
- Existing nodes must be exhausted before a new subgenre is proposed; lexical synonyms become aliases.
- An accurate defensible new style is preferable to forcing a poor existing fit.
- User acceptance, not an automatic confidence threshold, approves a classification.
- Spotify sorting, schema updates, file moves, downloads, and folder creation preview before explicit apply.
- New files enter intake and use Essentia. Existing files use Rekordbox BPM.
- Rekordbox inspection is read-only feedback.
- Audio is never overwritten or automatically deleted.

## Deep module design

The six capability boundaries are Catalog, Classification, Spotify, Local Library,
Acquisition, and Rekordbox. Config, Artifacts, and Matching are the only currently justified
shared modules.

- A deep module may contain many small atomic functions. Its public interface remains much
  smaller than the behavior it hides.
- Keep a function inside the capability that owns its policy. Extract it only when multiple
  capabilities must share exactly the same policy, it protects a shared critical invariant,
  or it is a substantial independently meaningful boundary.
- Function length, reuse count, or an external provider name alone does not justify another
  module. Generic `utils`, service-per-operation files, speculative adapters, and compatibility
  layers are discouraged.
- Catalog owns taxonomy interpretation and mutation. Local Library owns every move into a
  canonical folder. Acquisition owns intake audio inspection and Essentia analysis. Rekordbox
  owns its output-specific metadata extraction.
- External providers are implementation details hidden by the capability that uses them.
- Dependencies flow toward Catalog, Config, Artifacts, and Matching. Acquisition may call the
  Local Library promotion interface; Local Library never depends on Acquisition.
- Future UI or LLM-agent code calls the public capability interfaces instead of private helpers.
- KISS and YAGNI remain priorities. Do not introduce an orchestration framework before a real
  UI or agent workflow demonstrates the need.
- Preview/apply, user approval, exact identity evidence, intake quarantine, no overwrite or
  automatic deletion, and read-only Rekordbox handling are architectural invariants.

## Known limitations

- Spotify Web API cannot read or create playlist folders; folder organization is manual.
- Filename and metadata matching can leave bootlegs, transliterations, and unusual edits unresolved.
- Discogs and Last.fm evidence may be missing or incorrect, so exact public-web research remains necessary.
- slskd and Rekordbox are user-managed external applications.
- Versioned `data/*.json` files hold the current work only; Git preserves source and catalog history.
