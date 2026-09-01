# DJ Music Repository Instructions

## Project priorities

- This is a solo MVP and a learning project. Keep the code readable, direct, and easy to change.
- Follow KISS and YAGNI. Add an abstraction, dependency, state store, or compatibility layer only for a current demonstrated need.
- Prefer descriptive functions and simple orchestration. Do not design for a future UI, team, or agent platform before it exists.

## Source of truth

- Read `music_schema_knowledge.json` before classifying, routing, acquiring, or moving music.
- The hierarchy is BPM master → canonical main genre → optional subgenre.
- Canonical names create destinations. Aliases are lookup terms only.
- Spotify IDs, ISRCs, placement IDs, and file hashes are identity evidence; display names are not sufficient identity.
- BPM bands are 118–<126, 126–<132, 132–<145, and 145–180 inclusive.

## Exhaustive classification

- Research the exact recording and version, not merely the artist.
- Use Spotify metadata, Discogs, Last.fm, labels, stores, release descriptions, BPM, version information, and other credible evidence.
- Consider every plausible canonical main genre and subgenre in the applicable BPM placement before choosing one.
- Check every canonical name and alias globally before proposing taxonomy.
- Spelling, punctuation, abbreviations, translations, and lexical synonyms become aliases, not new genres.
- Do not create a subgenre merely because one source uses a different label.
- Before proposing a new subgenre, explain why the closest existing subgenres are inaccurate.
- When a defensible recognized or creator-defined style exists and all existing subgenres are poor fits, prefer the accurate new subgenre over forcing an incorrect classification.
- When evidence supports only a broad genre and no defensible new style, use the main-genre fallback and flag the weak evidence.
- Every valid track receives a canonical main genre. Use review flags for uncertainty instead of leaving it unclassified.
- Mark a metadata-derived BPM as `inferred-bpm`; do not present it as measured audio or Rekordbox BPM.
- Spotify, Discogs, and Last.fm provide evidence only. They never vote or make the final classification.
- The agent proposes classifications and taxonomy changes. The user accepts or corrects them.
- Only `scripts/catalog.py update --apply` changes taxonomy from accepted research.

## Deep module boundaries

- The six capability modules are `catalog.py`, `classification.py`, `spotify.py`,
  `local_library.py`, `acquisition.py`, and `rekordbox.py`.
- `config.py`, `artifacts.py`, and `matching.py` are the only shared modules. Do not recreate a
  generic `utils` directory or an `audio.py` module.
- Prefer atomic private functions behind small module-level public interfaces. Keep behavior in
  its owning capability unless extraction protects a genuinely shared policy or invariant.
- Catalog owns taxonomy mutation; Local Library owns canonical file moves; Acquisition owns
  intake inspection and Essentia; Rekordbox remains read-only.
- Future UI and agent layers must use public capability functions rather than private helpers.

## Local music and BPM

- New downloads stay under `music/intake/<spotify-id>/` until verification passes.
- Essentia analyzes only new intake files.
- Existing library files use Rekordbox BPM and are never reanalyzed with Essentia.
- A wrong or ambiguous download remains in intake for review; never route it just to complete the workflow.
- Rekordbox review is read-only and reports evidence. It does not decide or apply corrections.

## Mutation safety

- Preview is the default for downloads, Spotify writes, schema updates, folder creation, and file moves.
- Consequential changes require the matching explicit `--apply` command.
- Never overwrite or automatically delete audio.
- Refuse collisions, missing sources, and files changed after preview.
- Verify file hashes after moves.
- Never start Docker or another external service implicitly.
- Do not remove old empty music directories automatically.
- Spotify playlist folders cannot be managed through the Web API; their placement is manual.
- Intake and review directories never appear in Rekordbox XML.
