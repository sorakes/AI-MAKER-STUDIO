# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com) and the project uses [semantic versioning](https://semver.org).

## [1.0.0] - 2026-11

First public release.

### Studio
- Node canvas with script, cast/product, image, video, transition, frame, approval and edit nodes.
- Connected shots: an image feeding a video becomes its first frame, and a video feeding the next continues from its last frame.
- Per-node model picker with an **Automatic** mode that chooses the right model for each connection.
- Multi-reference video (frame, cast, product and camera drawing together with the text), with warnings when a model ignores references.
- Camera direction drawn on the first frame, read by a vision model and written into the prompt.
- Video editing models through a temporary public link, and region editing (inpaint) on images.
- Final edit with ffmpeg: cuts, editorial transitions, music and voice-over.
- Characters and Products: sheets, references, 8 poses/views, spritesheet and a shared library.
- Inventory, tabs per area and a token gauge.
- Interface in English, Portuguese and Spanish.

### Admin
- Users, groups, base and per-node permissions.
- Monthly token budgets per group.
- Microsoft sign-in (Entra ID) with automatic group rules.
- Encrypted API keys and models added from the OpenRouter catalog.
- Branding, editable prompts, logs with CSV export, files and dashboard.

### Infrastructure
- Docker image published to GHCR for `linux/amd64` and `linux/arm64`.
- Docker Compose setup with a Cloudflare quick tunnel for video editing.
- Test suite and CI on Python 3.12.

[1.0.0]: https://github.com/sorakes/AI-MAKER-STUDIO/releases/tag/v1.0.0
