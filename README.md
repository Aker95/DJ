# DJ Library

An agent-oriented toolkit for keeping Spotify placements, local audio, and Rekordbox aligned. The package separates repeatable logic from five thin command adapters: commands validate arguments, call `dj_library` functions, and emit exactly one JSON result.

The project deliberately avoids a service framework, global workflow state, interactive prompts, and implicit "latest run" behavior. Plans are reviewable artifacts; consequential operations accept only explicit, hashed manifests.

## Architecture

```text
music_schema_knowledge.json       canonical v3 taxonomy
src/dj_library/                  reusable domain functions
scripts/                         five JSON tool adapters
tests/unit/                      isolated logic tests
tests/integration/               offline workflow and adapter tests
tests/e2e/                       optional live-service tests
data/registry.json               generated workflow registry (ignored)
data/runs/<domain>/<run-id>/     generated artifacts (ignored)
music/intake/                    neutral new-file intake (ignored)
music/review/                    neutral exceptions (ignored)
```

The package uses plain functions and dictionaries. `Settings` is the only shared immutable object; paths, schemas, and external clients are otherwise passed explicitly.

## Installation

Python 3.11 or newer is required.

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Install optional integrations only when needed:

```bash
.venv/bin/pip install -e '.[audio]'
.venv/bin/pip install -e '.[rekordbox]'
```

## Environment

Copy values into `.env`; it is ignored by Git.

| Variable | Purpose | Default |
|---|---|---|
| `MUSIC_ROOT` | Local audio root | `<repo>/music` |
| `SPOTIFY_CLIENT_ID` | Spotify OAuth client | required for Spotify actions |
| `SPOTIFY_CLIENT_SECRET` | Spotify OAuth secret | required for Spotify actions |
| `SPOTIFY_REDIRECT_URI` | OAuth callback | required for Spotify actions |
| `SPOTIFY_CACHE_PATH` | Reusable OAuth cache | `data/spotify-token-cache.json` |
| `SLSKD_BASE_URL` | slskd API | `http://localhost:5030` |
| `SLSKD_API_KEY` | slskd API key | required for acquisition apply |
| `SLSKD_DOWNLOADS_DIR` | completed slskd files | `~/.slskd/downloads` |
| `SLSKD_CONTAINER_NAME` | local Docker container | `slskd` |
| `MIN_BITRATE_KBPS` | Minimum MP3 bitrate | `320` |
| `PREFERRED_FORMATS` | Comma-separated acquisition order | `flac,wav,aiff,mp3` |

Spotify authentication is intentional and cache-preserving:

```bash
.venv/bin/python scripts/spotify.py auth
```

## Tool contract

Every script writes one JSON object to stdout:

```json
{
  "ok": true,
  "action": "spotify.sort-plan",
  "run_id": "20260819T120000Z-ab12cd34",
  "artifacts": {"manifest": "data/runs/spotify/.../sort_manifest.json"},
  "summary": {"tracks_to_sort": 12, "tracks_skipped": 1}
}
```

Diagnostics go to stderr. Failures use the same envelope with `ok: false` and return a non-zero status.

### Research

```bash
.venv/bin/python scripts/research.py prepare --playlist 'Review Queue'
.venv/bin/python scripts/research.py validate --input data/runs/research/<run-id>/provisional.json
.venv/bin/python scripts/research.py approve \
  --input data/runs/research/<run-id>/provisional.json \
  --decisions decisions.json
```

`prepare` is read-only. `approve` records reviewed classifications and approved taxonomy proposals but does not sort Spotify.

### Spotify

```bash
.venv/bin/python scripts/spotify.py sort-plan --input data/runs/research/<run-id>/approved.json
.venv/bin/python scripts/spotify.py sort-apply --manifest data/runs/spotify/<run-id>/sort_manifest.json
.venv/bin/python scripts/spotify.py sort-audit --input data/runs/research/<run-id>/approved.json
.venv/bin/python scripts/spotify.py schema-plan
.venv/bin/python scripts/spotify.py schema-apply --manifest data/runs/spotify/<run-id>/schema_manifest.json
```

Snapshots never backfill schema IDs or clear OAuth state. Only `schema-apply` can update schema playlist mappings.

### Synchronization and acquisition

```bash
.venv/bin/python scripts/sync.py snapshot
.venv/bin/python scripts/sync.py delta --snapshot data/runs/sync/<run-id>/snapshot.json
.venv/bin/python scripts/sync.py reconcile-plan --delta data/runs/sync/<run-id>/delta.json
.venv/bin/python scripts/sync.py reconcile-apply --manifest data/runs/sync/<run-id>/reconciliation_manifest.json
.venv/bin/python scripts/sync.py acquire-plan --delta data/runs/sync/<run-id>/delta.json
.venv/bin/python scripts/sync.py acquire-apply --manifest data/runs/acquisition/<run-id>/acquisition_plan.json
.venv/bin/python scripts/sync.py analyze music/intake/<run-id>/track.flac
.venv/bin/python scripts/sync.py verify --manifest data/runs/acquisition/<run-id>/acquisition_manifest.json
.venv/bin/python scripts/sync.py promote-apply --manifest data/runs/acquisition/<run-id>/promotion_manifest.json
```

Acquisition writes only to a run-specific intake directory. Verification separates consistent files from review items. Promotion uses the reviewed, hash-checked move manifest.

### Local library

```bash
.venv/bin/python scripts/library.py structure-plan
.venv/bin/python scripts/library.py structure-apply --manifest data/runs/library/<run-id>/structure_manifest.json
.venv/bin/python scripts/library.py audit
.venv/bin/python scripts/library.py dedupe-plan
.venv/bin/python scripts/library.py dedupe-apply --manifest data/runs/library/<run-id>/deduplication_manifest.json
.venv/bin/python scripts/library.py alignment-plan --review review.json --decisions decisions.json
.venv/bin/python scripts/library.py alignment-apply --manifest data/runs/library/<run-id>/alignment_manifest.json
```

Deduplication quarantines selected copies under `music/review`; it never deletes them. Alignment preserves the required Spotify-add → local-move → previous-membership-remove order.

### Rekordbox

```bash
.venv/bin/python scripts/rekordbox.py verify --database ~/Library/Pioneer/rekordbox/master.db
.venv/bin/python scripts/rekordbox.py reconcile --reviewed approved.json
.venv/bin/python scripts/rekordbox.py export
```

Database inspection uses a temporary copy. XML export scans direct files in final schema destinations only, excluding intake and review trees.

## Normal workflows

1. Research: `research prepare` → agent/user decisions → `validate` → `approve`.
2. Spotify sorting: `spotify sort-plan` → review → explicit `sort-apply` → `sort-audit`.
3. New files: `sync snapshot` → `delta` → optional unsorted reconciliation → acquisition plan/apply → verification → promotion or mismatch review.
4. Existing library: `rekordbox verify` → `reconcile` → decisions → `library alignment-plan` → review → explicit `alignment-apply`.
5. Export: run `rekordbox export` after final local alignment.

No plan implies permission to apply it. Pass artifact paths explicitly; do not infer a newest run.

## Testing and quality

The default suite is offline and uses fake Spotify, slskd, and Rekordbox boundaries.

```bash
.venv/bin/pytest
.venv/bin/pytest --cov=dj_library --cov-report=term-missing
.venv/bin/ruff format --check src scripts tests/unit tests/integration
.venv/bin/ruff check src scripts tests/unit tests/integration
```

Coverage must remain at least 85% for `src/dj_library`. Optional live-client implementations are excluded; their workflow behavior is tested through injected clients. Put real service checks under `tests/e2e` with the `e2e` marker; they are excluded by default.

## Project documents

- `README.md` describes the current software and commands.
- `context.md` preserves durable reasoning, domain lessons, and limitations.
- `AGENTS.md` contains mandatory safety and classification rules.
- `music_schema_knowledge.json` is the canonical taxonomy and routing source.
