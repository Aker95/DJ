# DJ Library Project Context

This document stores durable project knowledge. It intentionally does not repeat command reference material or generated run details; those belong in `README.md` and `data/runs` respectively.

## Domain model

The repository maintains one explainable view of a recording across Spotify, local audio, and Rekordbox. Every workflow ultimately answers:

1. Which exact recording and version is this?
2. Which canonical main genre and optional subgenre describe it?
3. Which BPM band contains it?
4. Do Spotify membership, local path, registry state, and Rekordbox describe the same destination?

The canonical taxonomy is `BPM master → main genre → optional subgenre`.

The non-overlapping masters are 118–<126, 126–<132, 132–<145, and 145–180 inclusive. A genre may have placements in multiple bands without becoming multiple genres. Synonyms are aliases and never create parallel folders or playlists.

Identity keys are deliberately stronger than labels:

- Spotify track: Spotify ID, with ISRC and release/version evidence where available.
- Recording: strongest available combination of ISRC, version, duration, fingerprint, and researched evidence.
- File: SHA-256 plus immutable size/mtime facts at plan time.
- Taxonomy destination: stable placement ID and Spotify playlist ID.

Filenames and display names aid matching but are never authoritative identity.

## Architectural decisions

### Small functional core

Reusable logic lives under `src/dj_library` as plain functions accepting settings, schemas, paths, and clients explicitly. The five scripts are adapters for future agent tools: they parse arguments, call one workflow method, persist explicit artifacts, and emit one JSON envelope.

`Settings` is immutable. There is no service container, deep class hierarchy, hidden latest-run lookup, interactive prompt, or dynamic import of historical scripts. This is intentional KISS/YAGNI: the artifact contracts are the future UI boundary, and no UI framework is needed yet.

### Explicit artifacts and plans

Generated state lives under `data/runs/<domain>/<run-id>`. Snapshots, reviews, manifests, results, and XML are passed by path. `data/registry.json` is created when absent and stores durable workflow status, but it is not a replacement for versioned review evidence.

Every consequential operation has a plan/apply split. Manifests include a canonical content hash. Apply revalidates that hash, refuses changed sources and collisions, and is idempotent where the external system permits it.

### Taxonomy v3

`music_schema_knowledge.json` remains at the repository root. Version 3 removes migration-only v1/v2 fields and compatibility behavior. Legacy labels survive only as aliases. Stable placement and playlist IDs remain authoritative even if display names change.

### Read-only collection boundaries

Spotify snapshot collection does not mutate the schema or OAuth cache. Rekordbox inspection reads a temporary database copy. Existing files reuse Rekordbox BPM; only newly acquired files use the optional preflight audio analyzer.

## Workflow lessons

### Research and classification

Codex/user research is the supported classifier. Exact track/release/label/store evidence is preferred; artist-level evidence is a fallback. BPM, ISRC, duration, version tokens, related releases, and previews are supporting evidence.

Every valid track gets a main genre. If no leaf is defensible, the parent fallback is preferable to an invented subgenre. A useful recognized or creator-defined leaf may be proposed after checking every canonical label and alias. Confidence expresses evidence quality; explicit approval determines eligibility.

Research approval and Spotify sorting are separate. Approval preserves decisions and may update reviewed taxonomy proposals; it never implies permission for Spotify writes.

### Matching

The historical matching experiment showed RapidFuzz token matching was materially more tolerant of real filenames than `SequenceMatcher`. At the established artist/title thresholds of 0.80/0.85, it recovered 19 evaluated matches where `SequenceMatcher` recovered none. The production matcher therefore normalizes Unicode, site prefixes, numbering, generic mix labels, featured-artist noise, and swapped artist/title fields before scoring.

A second conservative path accepts an almost-exact title only when the normalized artist also appears in the filename. This improves recovery without making title-only matches authoritative. Version words use token boundaries—`live` must not match `deliver`—and remix/live/radio incompatibilities remain review conditions.

### Acquisition and verification

A delta compares canonical Spotify placements with final local destinations and pending registry/run state. Pending files count as acquired so reruns do not request the same track again.

Downloads enter `music/intake/<run-id>`, never a guessed final folder. The acquisition record preserves expected Spotify identity, intended placement, source candidate, local metadata, duration, codec, size, and SHA-256.

New-file analysis is cached by SHA-256. A file is promotion-ready only when identity and version checks pass, analysis is unambiguous, and normalized BPM remains in the provisional band. Same-band numerical variation is acceptable. Missing evidence, analysis failure, identity conflict, or a cross-band result enters review.

### Mismatch adjudication

The decision order is recording identity → version identity → BPM interpretation → genre → placement. BPM is context, not proof of identity or genre.

Every mismatch receives exactly one identity outcome:

- `same-recording`
- `alternate-version`
- `wrong-download`
- `duplicate-copy`
- `spotify-catalog-gap`
- `needs-evidence`

A confirmed same recording may have multiple correction axes: `metadata-error`, `genre-error`, `bpm-routing-error`, `spotify-membership-error`, and `local-path-error`.

A wrong download is an acquisition failure. Preserve both the intended Spotify expectation and the observed file, and propose reacquisition; never route the wrong file to make the acquisition look correct. An alternate version is a separate recording and needs its own Spotify ID or local-only identity.

Review packets must retain original expectation, observed path/hash/tags/duration, Spotify metadata, version evidence, BPM sources and normalization, current memberships/path, prior rationale, and all escalation flags.

### Final alignment and deduplication

For a corrected placement the safe order is:

1. validate the final schema placement;
2. add the Spotify track to the final playlist and verify;
3. move the exact local file and verify its hash;
4. remove the previous Spotify membership;
5. verify final registry/local/Spotify agreement.

Deduplication is recoverable quarantine, not deletion. Duration divergence greater than three seconds remains uncertain because it may represent another edit/version. Quality ranking favors lossless containers, bit depth, sample rate, bitrate, channels, metadata completeness, and clean source identity, but an apply still requires a reviewed manifest.

### Rekordbox

Only direct files in canonical final destinations are exported. Intake and review directories are excluded structurally, not by post-filtering a broad scan. Rekordbox BPM is the existing-library authority and may expose a routing mismatch without proving a genre or identity change.

## Known limitations

- Spotify Web API cannot read or create playlist folders; folder organization is manual.
- Audio fingerprint/AcoustID evidence is not yet a default dependency, so ambiguous identity remains a review case.
- Essentia and pyrekordbox are optional environment-specific integrations. Their pure orchestration is tested with fakes; real checks are opt-in e2e tests.
- Matching is intentionally conservative and can leave unusual bootlegs, transliterations, or badly tagged files unmatched.
- The registry is operational state, not a complete provenance database. Immutable run artifacts remain necessary for audit.
- Applying a plan cannot make multiple external systems transactional. Resumable ordering and verification minimize risk, but a partial failure may still require retry.

## Future considerations

- A review UI can consume and produce the existing JSON artifacts without changing the domain package.
- Fingerprinting can be added as another injected identity signal when operationally justified.
- Richer validation schemas may become useful if multiple external callers appear; adding a framework before then would be premature.
- Historical run artifacts may be archived outside the repository after their durable lessons are captured here.
- The quarantined deduplication audio must be relocated through its separately reviewed hash manifest before the obsolete historical research tree can be removed.
