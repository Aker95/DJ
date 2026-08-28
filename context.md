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

## Known limitations

- Spotify Web API cannot read or create playlist folders; folder organization is manual.
- Filename and metadata matching can leave bootlegs, transliterations, and unusual edits unresolved.
- Discogs and Last.fm evidence may be missing or incorrect, so exact public-web research remains necessary.
- slskd and Rekordbox are user-managed external applications.
- Fixed `data/*.json` files hold the current work only; Git preserves source and schema history.
