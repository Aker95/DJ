# DJ Music Repository Instructions

## Source of truth

- Read `music_schema_knowledge.json` before classifying, routing, acquiring, or moving music.
- The schema is BPM master → canonical main genre → optional subgenre.
- Canonical names create destinations. Aliases are lookup terms only.
- Stable Spotify IDs, placement IDs, ISRCs, and SHA-256 hashes are identity keys; display names are not.

## Classification

- Classification is Codex/user research through `scripts/research.py`; do not use the retired Discogs/Last.fm consensus classifier.
- Fetch Spotify metadata read-only, then research the exact recording. Prefer track, release, label, and store evidence; artist evidence is a fallback.
- Every valid track receives a canonical main genre. A missing subgenre uses the parent fallback. Use review flags instead of leaving the main genre unset.
- Check canonical names and aliases before proposing a main genre, subgenre, alias, or new BPM placement.
- Metadata BPM may be inferred only when marked `inferred-bpm`.
- Approval records classification decisions only. Spotify sorting is a separate plan/apply workflow.

## Routing and identity

- BPM bands are 118–<126, 126–<132, 132–<145, and 145–180 inclusive.
- Subgenre classifications route to their leaf; main-genre classifications route to the parent fallback.
- New files remain under neutral intake until identity and BPM verification passes.
- Existing files use Rekordbox BPM and are never sent through the preflight analyzer.
- Evaluate mismatches in this order: recording identity → version identity → BPM interpretation → genre → BPM placement.
- Identity uses exactly one of `same-recording`, `alternate-version`, `wrong-download`, `duplicate-copy`, `spotify-catalog-gap`, or `needs-evidence`.
- A same recording may then have `metadata-error`, `genre-error`, `bpm-routing-error`, `spotify-membership-error`, or `local-path-error` corrections.
- A wrong download never changes the intended Spotify track's classification. Preserve it for review and propose reacquisition.
- An alternate version receives its own Spotify identity or a local-only identity; never inherit another version's metadata.

## Approval and mutation

- Public workflow functions return data and never print, prompt, or exit.
- Consequential changes require an explicit apply action and the exact reviewed manifest hash.
- Research, validation, audits, reconciliation, and plan actions never mutate Spotify or local audio.
- Do not acquire, move ordinary library files, change Spotify, retag, or deduplicate without explicit user authorization for the apply action.
- Alignment order is Spotify add and verify → local move and hash verify → previous Spotify membership remove → final verification.
- Never overwrite or automatically delete audio. Refuse collisions and source files changed after review.
- Do not remove old empty music directories automatically.
- Spotify Web API cannot manage playlist folders; folder placement is manual in the Spotify UI.
- Neutral `music/intake` and `music/review` directories never appear in generated Rekordbox XML.

## Repository boundaries

- Business logic belongs in `src/dj_library`; `scripts/` contains only the five JSON adapters.
- Generated state belongs under `data/runs/<domain>/<run-id>/`; the runtime registry is `data/registry.json`.
- `context.md` is the durable home for project reasoning. Do not replace it with generated run data.
