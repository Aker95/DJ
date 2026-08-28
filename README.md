# DJ Library MVP

A readable solo-project toolkit for researching Spotify playlists, keeping local music aligned, acquiring missing tracks, and using Rekordbox as feedback.

The project follows one simple pattern:

```text
inspect → write a readable work file → review → explicitly apply
```

There is no installed project package, workflow registry, run history, generic manifest system, or automatic genre voting.

## Repository map

```text
music_schema_knowledge.json   Canonical BPM/genre/subgenre taxonomy
scripts/                      Commands you run
scripts/utils/                Logic genuinely shared by commands
data/*.json                   Current preview and review files
music/intake/<spotify-id>/    New files that still need verification
tests/                        Offline behavior tests
requirements.txt              Python dependencies
setup.sh                      Environment setup
```

## Setup

Python 3.11 or newer is required.

```bash
./setup.sh
source .venv/bin/activate
```

`setup.sh` creates `.venv`, installs `requirements.txt`, and creates `.env` from `.env.example` only when `.env` is missing.

Important dependencies:

- Spotipy communicates with Spotify.
- Requests and pylast collect Discogs and Last.fm evidence.
- Mutagen reads local audio metadata.
- RapidFuzz compares Spotify metadata with real-world filenames.
- Essentia checks BPM/key for newly acquired intake files.
- pyrekordbox reads a temporary copy of the Rekordbox database.
- pytest runs the offline test suite.

Fill in the integrations you use in `.env`, then authenticate Spotify once:

```bash
python scripts/spotify_auth.py
```

## Playlist research—the central workflow

Prepare a complete breakdown of a Spotify playlist:

```bash
python scripts/classification_research.py --playlist "Playlist name or URL"
```

This creates:

- `data/research.json`: editable structured research.
- `data/research.md`: human-readable playlist breakdown.

Each track contains Spotify identity/release metadata, Discogs styles, Last.fm tags, direct evidence links, and empty fields for the agent's final recommendation and rationale. Providers never vote or classify automatically.

The agent researches every exact recording, compares all plausible existing taxonomy nodes, and fills the recommendations. The Markdown groups the results by BPM master, main genre, subgenre, parent fallback, and review flag.

After editing the JSON, regenerate the readable breakdown without fetching Spotify again:

```bash
python scripts/classification_research.py --render data/research.json
```

Optional user corrections and accepted taxonomy changes live in `data/decisions.json`:

```json
{
  "tracks": [
    {
      "spotify_track_id": "spotify-id",
      "master_folder": "03 • 132–144 FAST/RAVE",
      "main_genre": "Techno",
      "subgenre": "Hard Groove",
      "rationale": "Exact release and label evidence support hard groove.",
      "alternatives_considered": ["Techno parent", "Peak-Time Techno"],
      "web_evidence": [{"url": "https://example.com/release"}],
      "review_flags": [],
      "research_status": "reviewed"
    }
  ],
  "schema_changes": []
}
```

When research proposes accepted taxonomy, preview and apply it first:

```bash
python scripts/schema_update.py data/research.json --decisions data/decisions.json
python scripts/schema_update.py data/research.json --decisions data/decisions.json --apply
python scripts/library_structure.py --apply
```

Provision a Spotify playlist for any new destination, then approve classifications:

```bash
python scripts/spotify_playlist.py --master "03 • 132–144 FAST/RAVE" --main Techno --subgenre "Hard Groove"
python scripts/spotify_playlist.py --master "03 • 132–144 FAST/RAVE" --main Techno --subgenre "Hard Groove" --apply
python scripts/classification_approve.py data/research.json --decisions data/decisions.json
```

`spotify_playlist.py` handles one narrow job: it maps one schema destination to one real Spotify playlist. Preview shows whether it will reuse an exact-name playlist or create one. Apply saves the playlist ID into that destination. It does not classify or sort tracks and cannot create Spotify folders.

Finally preview and apply the Spotify sort:

```bash
python scripts/spotify_sort.py data/classifications.json
python scripts/spotify_sort.py --apply
```

Apply adds and verifies target membership before removing and verifying source membership.

## Spotify-to-local synchronization

```bash
python scripts/spotify_snapshot.py
python scripts/library_structure.py
python scripts/library_structure.py --apply
python scripts/library_delta.py
```

The snapshot reads schema-mapped playlists without changing Spotify or the schema. The delta compares those tracks with direct files in their canonical local destinations and writes `data/delta.json`.

Before downloading, look for matches in unsorted local folders:

```bash
python scripts/library_reconcile.py
python scripts/library_reconcile.py --apply
python scripts/library_delta.py
```

The preview records exact source/destination paths and hashes. Apply refuses collisions and changed files.

## Acquisition

Preview missing tracks:

```bash
python scripts/acquire.py
```

Start slskd yourself, then explicitly acquire:

```bash
python scripts/acquire.py --apply
```

Downloads enter `music/intake/<spotify-id>/`. Mutagen and Essentia verify identity, version, duration, and BPM band. A file is promoted automatically only when every check passes and the destination is free. Otherwise it stays in intake and appears in `data/intake_review.json`.

## Rekordbox feedback

```bash
python scripts/rekordbox_review.py
```

This reads a temporary database copy and writes `data/rekordbox_review.json`. It flags files outside the schema, missing BPM, BPM-folder mismatches, identity ambiguity, acquisition version/download warnings, and disagreement with approved Spotify classifications. It never modifies Rekordbox or moves files.

Export canonical final folders to Rekordbox XML:

```bash
python scripts/rekordbox_export.py
```

Intake and review files are excluded.

## Schema tools

Validate the current taxonomy at any time:

```bash
python scripts/schema_validate.py
```

Accepted schema changes support only three clear actions:

- `add_alias`
- `ensure_main_genre`
- `ensure_subgenre`

The update tool resolves canonical names and aliases first, reuses existing subgenre identity across BPM placements, validates before saving, and is safe to rerun.

## Command reference

| Script | Input | Output | Default / `--apply` |
|---|---|---|---|
| `spotify_auth.py` | Spotify credentials in `.env` | token cache | Opens authorization only when run. |
| `classification_research.py` | `--playlist`, or `--render research.json` | `data/research.json`, `data/research.md` | Always read-only toward Spotify, schema, and audio. |
| `schema_validate.py` | current schema | terminal summary | Always read-only. |
| `schema_update.py` | research and optional decisions | `data/schema_plan.json` | Previews; `--apply` atomically updates only the schema. |
| `classification_approve.py` | reviewed research and optional decisions | `data/classifications.json`, final research files | Validates and writes work files; never changes Spotify. |
| `spotify_playlist.py` | one master/main/optional subgenre | playlist plan/result; schema mapping | Previews reuse/create; `--apply` provisions and records the ID. |
| `spotify_sort.py` | classifications | `data/spotify_sort_plan.json` or result | Previews; `--apply` adds/verifies, then removes/verifies. |
| `spotify_snapshot.py` | mapped Spotify playlists | `data/spotify_snapshot.json` | Always read-only. |
| `library_structure.py` | schema | terminal folder list | Previews; `--apply` creates missing folders. |
| `library_delta.py` | Spotify snapshot | `data/delta.json` | Always read-only. |
| `library_reconcile.py` | delta and unsorted files | `data/reconcile_plan.json` | Previews; `--apply` performs verified collision-safe moves. |
| `acquire.py` | delta | acquisition plan/results and intake review | Previews; `--apply` downloads, verifies, and safely promotes. |
| `rekordbox_review.py` | Rekordbox DB, classifications, acquisition results | `data/rekordbox_review.json` | Always read-only. |
| `rekordbox_export.py` | schema and direct final files | `rekordbox_schema.xml` | Generates XML; never modifies Rekordbox. |

## Tests

```bash
python -m pytest
python -m compileall scripts
```

The default suite uses fake external clients and never mutates live services.
