# Project instructions

- This is an independent, noncommercial, source-available CCTV video extraction repository. Preserve the PolyForm Noncommercial license and andyfanybo attribution; do not change to a commercial-use license without explicit owner instruction.
- Keep third-party code, libraries and weights under their original terms. Do not commit runtime binaries, models, recordings, person images, credentials, job contents or local deployment identifiers.
- Efficiency comes first. Preserve approximate cut boundaries, stream-copy video/audio, sparse person detection and automatic output. Do not add packet hashes, packet SQLite indexes, waveform checks, strict synchronization or manual approval gates to processing.
- Originals are read-only. Work/cache directories must be separate from sources and publication. Final names preserve source names and never overwrite. Cleanup requires confirmed delivery and must protect shared caches and original/published files.
- One worker and a shared NPU lock. Do not compete with an existing installation for hardware. Explicit hardware smoke tests must acquire the same lock as the active installation.
- Keep per-segment and per-sample recovery. Model/platform incompatibility must fail preflight; isolated video failures may retain a whole segment after three attempts.
- Long recordings: optional first-six-hours relative night policy, normally 60-second coarse sampling, then selected interval (default 30 seconds). Do not infer recording wall clock.
- Keep any-Origin behavior. Do not introduce domain allow/block lists.
- Prefer capability probes and explicit configuration over hardcoded chip/kernel/path assumptions. Failed mounts must retain SSD caches. Do not replace atomic no-overwrite publication with overwriting rename.
- Run bounded isolated tests only. Never start long batch jobs, restore historical production tasks, or deploy this development tree without an explicit deployment request.
- `tests/` uses temporary fixtures and mocks by default. GitHub CI does not establish hardware compatibility. Record only observed validation in docs/VALIDATION.md.
- The owner explicitly authorizes documentation screenshots of the local 8423 task/statistics panel without video content. Mask filenames, task IDs and private filesystem paths in the browser before capturing. Store only reviewed images under docs/images, with provenance; do not publish raw jobs or recordings.
- Each job freezes its input/publication directories and storage policy. Changing defaults must never redirect old downloads, recovery, publication or cleanup to a different directory.
