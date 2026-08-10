# DJ Music Repository Instructions

## Source of truth

- Read `music_schema_knowledge.json` before classifying, routing, acquiring, or moving music.
- The schema is a three-level taxonomy: BPM master folder → canonical main genre → optional subgenre.
- Use canonical names from the schema. Treat aliases as lookup terms only; never create a second folder or playlist for a synonym.

## Module 06: Codex-researched classification

- Module 06 is orchestrated by Codex and the user. Do not use the Discogs/Last.fm consensus classifier for this workflow.
- Fetch Spotify metadata read-only, then research the exact recording on the public web. Prefer exact track and release evidence, but artist-profile evidence is acceptable when track-level genre information is unavailable.
- BPM, ISRC, duration, release/version, label, store taxonomy, descriptions, related releases, and an available preview may all support the conclusion.
- If tags are broad or conflicting, search for more detail and then make a documented best judgment.
- Every valid Spotify track must receive at least a canonical main genre. Use a parent-genre fallback plus review flags instead of leaving a track uncertain.
- Propose a new recognized or creator-defined subgenre whenever it describes the recording better than existing nodes. Check every canonical name and alias first so synonyms are merged rather than duplicated.
- Produce provisional JSON and Markdown for joint Codex/user review. A later `continue` approves every listed classification, schema proposal, alias, and routing change unless the user supplies corrections.
- Approval preserves versioned review artifacts and generates Part 2 input. It never authorizes Spotify writes.
- Spotify sorting is a separate step: run a dry-run first and require a subsequent explicit instruction for live changes.

## Routing rules

- A subgenre classification routes only to its leaf folder/playlist.
- A main-genre classification routes to that parent genre's fallback folder/playlist.
- User approval, not confidence, is the eligibility gate for reviewed v2 results. Confidence and review flags remain visible for audit.
- Spotify Web API playlist folders cannot be read or created. Any Spotify folder placement is a manual UI task.

## Local library safety

- Do not reclassify tracks that are already correctly sorted unless the user explicitly requests an audit or an approved schema migration requires a path change.
- Never apply a folder migration without first producing and reviewing a collision-checked dry-run manifest.
- Do not delete old empty directories automatically.
- Do not run acquisition or live Spotify mutations without an explicit user instruction.
