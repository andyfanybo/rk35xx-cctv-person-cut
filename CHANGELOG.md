# Changes

## 2.0.0-dev — 2026-10-18

- Establish an independent RK35xx CCTV person extraction repository from the v1.9.2 processing baseline.
- Centralize paths, binaries, model/runtime configuration, source roots and publication storage policy.
- Preserve serial processing, sparse sampling, resumable segments, stream-copy media, approximate boundaries, automatic delivery and cleanup.
- Isolate NPU/model preflight from per-segment retries; optionally validate model provenance metadata and target compatibility.
- Probe MPP decoding, offer explicit software decoding, and fall back from unsupported keyframe sampling to continuous sparse sampling.
- Add bounded diagnostics, optional storage probes, per-target model conversion entry point and a systemd deployment example.
- Remove historical strict-verification and batch-test entry points from the portable execution chain.
- Add isolated regression checks, generated CPU media smoke test, compatibility documentation and release source checks.
- License project-authored material under PolyForm Noncommercial License 1.0.0, copyright andyfanybo; preserve third-party license boundaries.

This is a development release. Hardware and operating-system support are limited to the evidence recorded in docs/VALIDATION.md.

### Web directory selection and installation follow-up

- Add server-side directory browsing and per-job frozen input/output locations; publication, recovery, downloads and cleanup retain the original job's directory.
- Add standalone/local one-command installation, configurable GitHub download acceleration, a temporary Mainland China pip mirror and isolated start/stop helpers.
- Document RK35xx cost and hardware-offload rationale; add reviewed screenshots and an original processing diagram.
- Expand tests to directory boundaries, installation protection and mirror fallback; validate an isolated real install and browser interaction.
