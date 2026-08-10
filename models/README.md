# Local models

All downloaded and user-selected model files live under this directory. Model assets are ignored by Git and must be registered with a fixed revision or local SHA-256 before loading.

Package-manager caches are redirected to `models/.cache`; no model may use a user-global Hugging Face or Torch cache.

Managed layout:

- `registry/<model-id>/<revision-or-hash>/`: verified files from the built-in registry.
- `custom/<custom-id>/`: validated copies of user-selected aesthetic `.safetensors` files.
- `.staging/`: temporary local-import files.
- `.quarantine/`: files rejected because of size, SHA-256, schema, or path safety.
- `.cache/`: project-local package/model-library caches only.

Each ready installation has an atomic `.installation.json`. Do not move a `.part` file into place manually; use the model API so size, SHA-256, container schema, and dependencies are checked.

The downloader never fetches repository Python files and never enables `trust_remote_code`. Local replacements are copied here after safetensors metadata and tensor shapes pass the supported schema; the original external path is not used for inference.
