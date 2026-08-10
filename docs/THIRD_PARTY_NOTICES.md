# Third-party dependency inventory

This inventory records the locked runtime, all Python and npm dependencies, and every registered model asset. The generated machine-readable report is `docs/THIRD_PARTY_DEPENDENCIES.json`; `scripts/test.ps1` fails when it drifts from the lockfiles, installed metadata, or model registry.

Exact package counts, transitive versions, notice-file hashes, package integrity values, model revisions, and artifact SHA-256 values are preserved in the generated report, `uv.lock`, `frontend/package-lock.json`, and `backend/dataset_audit_studio/model_adapters/registry.json`.

| Component | Pinned or locked version | License | Source |
|---|---|---|---|
| uv | 0.11.29 | Apache-2.0 OR MIT | <https://github.com/astral-sh/uv> |
| Python | 3.11.15 | PSF-2.0 | <https://www.python.org/downloads/release/python-31115/> |
| Node.js | 24.18.0 | MIT and bundled notices | <https://nodejs.org/dist/v24.18.0/> |
| FastAPI | `uv.lock` | MIT | <https://github.com/fastapi/fastapi> |
| SQLAlchemy | `uv.lock` | MIT | <https://github.com/sqlalchemy/sqlalchemy> |
| Alembic | `uv.lock` | MIT | <https://github.com/sqlalchemy/alembic> |
| Pillow | `uv.lock` | HPND | <https://github.com/python-pillow/Pillow> |
| pillow-avif-plugin | `uv.lock` | BSD-3-Clause | <https://github.com/fdintino/pillow-avif-plugin> |
| PyAV | `uv.lock` | BSD-3-Clause | <https://github.com/PyAV-Org/PyAV> |
| ImageHash | `uv.lock` | BSD-2-Clause | <https://github.com/JohannesBuchner/imagehash> |
| OpenCV Python Headless | `uv.lock` | Apache-2.0 | <https://github.com/opencv/opencv-python> |
| NumPy | `uv.lock` | BSD-3-Clause | <https://github.com/numpy/numpy> |
| Uvicorn | `uv.lock` | BSD-3-Clause | <https://github.com/encode/uvicorn> |
| Pydantic | `uv.lock` | MIT | <https://github.com/pydantic/pydantic> |
| Safetensors | 0.8.0 (`uv.lock`) | Apache-2.0 | <https://github.com/huggingface/safetensors> |
| timm | `uv.lock` | Apache-2.0 | <https://github.com/huggingface/pytorch-image-models> |
| HTTPX2 | `uv.lock` | BSD-3-Clause | <https://pypi.org/project/httpx2/> |
| React / React DOM | 19.2.7 | MIT | <https://github.com/facebook/react> |
| Lucide React | 1.25.0 | ISC | <https://github.com/lucide-icons/lucide> |
| Vite | 8.1.5 | MIT | <https://github.com/vitejs/vite> |
| TypeScript | 7.0.2 | Apache-2.0 | <https://github.com/microsoft/TypeScript> |
| Playwright CLI (QA only) | 0.1.17 | Apache-2.0 | <https://www.npmjs.com/package/@playwright/cli> |

## Registered model assets

No model weight is bundled with the source tree. The local downloader installs only explicitly requested files and verifies every file against the registry before use.

| Model asset | Fixed source | Declared license |
|---|---|---|
| LSE14 5K scorer | <https://huggingface.co/lse14/lse14-scorer/tree/655377cb813d35291a2010031f724e778b7d80dd> | Apache-2.0 |
| JTP-3 Hydra | <https://huggingface.co/RedRocket/Hydra/tree/a7a4606cf07b742ec402c60fb641db898eaeeb2e> | Apache-2.0 |
| Waifu Scorer V3 | <https://huggingface.co/Eugeoter/waifu-scorer-v3/tree/c2a747fd61d310a90e9cbbf8fc590c522f234424> | OpenRAIL |
| OpenAI CLIP ViT-L/14 | <https://github.com/openai/CLIP> | MIT |
| UniversalFakeDetect head | <https://github.com/WisconsinAIVision/UniversalFakeDetect/tree/76a0e3e60a8a06458707a625d269ba815a2e5919> | MIT |
| SigLIP2 So400m NaFlex | <https://huggingface.co/google/siglip2-so400m-patch16-naflex/tree/cc24074f717b612951c2dead130904ab9b65a81e> | Apache-2.0 |
| DINOv2 Large | <https://huggingface.co/facebook/dinov2-large/tree/47b73eefe95e8d44ec3623f8890bd894b6ea2d6c> | Apache-2.0 |
| TorchVision VGG19 IMAGENET1K_V1 | <https://download.pytorch.org/models/vgg19-dcbb9e9d.pth> | BSD-3-Clause |
| Kaloscope v2 LSNet checkpoint | <https://huggingface.co/heathcliff01/Kaloscope2.0/tree/8d93704a0c260c23187f30f591c9e29c79807d44> | Apache-2.0 |
| Watermark Detection SigLIP2 | <https://huggingface.co/prithivMLmods/Watermark-Detection-SigLIP2/tree/ce32ed8fe48872dd7d15e1db2602a0d50ddceae0> | Apache-2.0 |
| PP-OCRv5 server detection | <https://huggingface.co/PaddlePaddle/PP-OCRv5_server_det_safetensors/tree/cbea9f3c3254c6ff7b0016cfbf90549e1ad4c5bb> | Apache-2.0 |
| PP-OCRv5 server recognition | <https://huggingface.co/PaddlePaddle/PP-OCRv5_server_rec_safetensors/tree/542979d7cc3791732bb12af35313a6840952d79f> | Apache-2.0 |

The vendored LSNet architecture under `backend/dataset_audit_studio/components/artist_style/_lsnet/` is derived from <https://github.com/spawner1145/comfyui-lsnet> commit `416d945e65b81ced93f1e762349d790ca92106b1`, licensed GPL-3.0. The project is distributed under GPL-3.0-only; the local PyTorch SKA fallback is a project modification of that architecture integration.

Other repository source code is not part of a model installation. In particular, Hydra Python files and PP-OCR `ocr_pipeline.py` are excluded. The application uses only local, allowlisted adapters; `remote_code_allowed` is fixed to `false` for every registry entry. User-provided replacement weights remain subject to the user's own rights and license obligations.

Redistribution must preserve the upstream license and notice files identified by `docs/THIRD_PARTY_DEPENDENCIES.json`. Model weights are downloaded separately and remain subject to their declared model licenses; custom weights remain subject to the user's own rights and obligations.
