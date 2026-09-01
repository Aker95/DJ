# DJ Library MVP

A small toolkit for researching Spotify recordings, maintaining a canonical DJ taxonomy,
aligning the local music library, acquiring missing files, and using Rekordbox as read-only
feedback.

The workflow is deliberately explicit:

```text
inspect → write a readable plan or work artifact → review → explicitly apply
```

## Architecture

The repository uses six deep capability modules. Each presents a small command/function
interface while hiding provider APIs, validation, matching, and safety details.

```text
Catalog          taxonomy, aliases, BPM routing, destinations, accepted updates
Classification   evidence, research, decisions, approval, readable reports
Spotify          authentication, snapshots, provisioning, verified playlist sorting
Local Library    folders, deltas, reconciliation, collision-safe verified movement
Acquisition      slskd, intake, audio inspection, Essentia verification, quarantine
Rekordbox        read-only review and canonical XML export
```

Only three policies are shared across capabilities:

```text
Config            paths, credentials, formats, supported audio extensions
Artifacts         versioned atomic JSON/text persistence and plan fingerprints
Matching          common filename and recording-identity policy
```

Repository map:

```text
music_schema_knowledge.json   canonical BPM/genre/subgenre catalog
scripts/                      nine directly executable modules
data/                         current work, plan, result, and review artifacts
music/intake/<spotify-id>/    downloads awaiting or undergoing verification
tests/                        offline behavior and architecture tests
```

## Setup

Python 3.11 or newer is required.

```bash
./setup.sh
source .venv/bin/activate
python scripts/catalog.py validate
python scripts/spotify.py auth
```

Configure only the integrations you use in `.env`. Spotify uses Spotipy; research uses
Discogs and Last.fm; acquisition uses user-managed slskd plus Mutagen and Essentia; Rekordbox
review uses pyrekordbox against a temporary database copy.

## Classification workflow

Prepare research for one Spotify playlist:

```bash
python scripts/classification.py research --playlist "Playlist name or URL"
```

This writes `data/research.json` and `data/research.md`. Providers supply evidence only;
the agent and user make the classification decision. After editing research or decisions:

```bash
python scripts/classification.py render data/research.json
```

If accepted research proposes taxonomy changes, preview and apply them first:

```bash
python scripts/catalog.py update --research data/research.json --decisions data/decisions.json
python scripts/catalog.py update --apply
python scripts/local_library.py structure
python scripts/local_library.py structure --apply
```

Provision any new Spotify destination, approve classifications, and sort:

```bash
python scripts/spotify.py provision --master "03 • 132–144 FAST/RAVE" --main Techno --subgenre "Hard Groove"
python scripts/spotify.py provision --apply
python scripts/classification.py approve data/research.json --decisions data/decisions.json
python scripts/spotify.py sort data/classifications.json
python scripts/spotify.py sort --apply
```

Spotify sorting adds and verifies target membership before removing and verifying source
membership. Spotify playlist folders remain manual because the Web API cannot manage them.

## Local-library synchronization

```bash
python scripts/spotify.py snapshot
python scripts/local_library.py structure
python scripts/local_library.py structure --apply
python scripts/local_library.py delta data/spotify_snapshot.json
python scripts/local_library.py reconcile --delta data/library_delta.json
python scripts/local_library.py reconcile --apply
python scripts/local_library.py delta data/spotify_snapshot.json
```

Reconciliation records exact paths, file size, modification time, and SHA-256. Apply refuses
collisions, escaped paths, missing sources, and sources changed after preview, then verifies
the destination hash.

## Acquisition

```bash
python scripts/acquisition.py --delta data/library_delta.json
python scripts/acquisition.py --apply
```

Start slskd yourself. Downloads remain under `music/intake/<spotify-id>/` until exact identity,
version, duration, and BPM-band verification passes. Acquisition owns Mutagen and Essentia
analysis; Local Library alone promotes a verified file into a canonical folder. Ambiguous or
incorrect downloads remain in intake and appear in `data/intake_review.json`.

## Rekordbox feedback

```bash
python scripts/rekordbox.py review
python scripts/rekordbox.py export
```

Review reads a temporary database copy and never changes Rekordbox or files. Existing library
files use Rekordbox BPM and are never analyzed by Essentia. Export includes only direct files
inside canonical final destinations.

## Artifacts and plans

JSON artifacts carry a small `_artifact` kind/version header. Consequential operations preview
to a named plan and `--apply` consumes that saved plan. Catalog/input fingerprints reject stale
plans, while Spotify and filesystem operations also verify current external state.

The old unversioned ignored work files are intentionally unsupported; regenerate them with the
new commands.

## Tests

The default suite uses fake external clients and never mutates live services:

```bash
python -m pytest
python -m compileall scripts
```
