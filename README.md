# pms-rodaje-imagen

Imagen pública `ghcr.io/cdanielp/pms-rodaje-h3` para el estudio de Rodaje (clase 4 de Prompt Models).

- Base: `runpod/comfyui:1.3.3-comfyuiv0.30.0-cuda13.0` (CUDA 13).
- ComfyUI 0.44.2 y los nodos de `modelos.json` en sus commits fijados, con todas sus dependencias.
- No trae modelos, keys ni datos personales. Los modelos (unos 72.6 GB) se bajan al prender la máquina a `/workspace/pms_models`.
- Se construye con GitHub Actions (`.github/workflows/imagen.yml`) usando el mismo `instalar_rodaje.py` del kit en modo `--imagen`.
