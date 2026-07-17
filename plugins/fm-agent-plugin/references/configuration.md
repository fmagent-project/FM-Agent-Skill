# Configuration fingerprint

Use one phase only when the project needs a deliberately flattened plan. Use
submodules to limit scope to project-relative directories. Extra edges must be
validated JSON and domain knowledge must be readable Markdown.

The fingerprint includes `one_phase`, submodules, extra-edge content hash, and
each knowledge-file content hash. It exists to prevent an incremental run from
reusing specifications built for a different scope or knowledge set.
