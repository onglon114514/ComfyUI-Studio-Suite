# Backend Switch Guide

This project now supports backend selection at the node level through `backend_provider`.

## 1. Recommended current providers

- `clip_reuse`
  - planned for generation-capable TE families such as NewBie / Qwen-style pipelines
- `transformers_inproc`
  - planned in-process local runtime
- `llama_cpp_python_inproc`
  - planned in-process GGUF runtime
- `koboldcpp`
  - current verified path
- `llama_cpp_server`
  - recommended next local managed backend
- `lm_studio`
  - attach-existing preset
- `vllm`
  - attach-existing preset

## 2. How `gateway_url` behaves now

`gateway_url` is still kept for compatibility, but its meaning is now:

- if you keep `http://127.0.0.1:8765`
  - local direct execution stays enabled
  - provider-specific defaults may apply
- if you enter a real backend URL
  - that URL is used directly

## 3. Provider defaults

### `koboldcpp`

- mode: managed local
- default backend URL comes from config
- recommended executable location:
  - `runtime/koboldcpp/koboldcpp.exe`

### `llama_cpp_server`

- mode: managed local
- default backend URL comes from config
- recommended executable location:
  - `runtime/llama.cpp/llama-server.exe`
- recommended health path:
  - `/health`

### `lm_studio`

- mode: attach existing
- if `gateway_url` is left at the default `8765`, the node will fall back to:
  - `http://127.0.0.1:1234/v1`

### `vllm`

- mode: attach existing
- if `gateway_url` is left at the default `8765`, the node will fall back to:
  - `http://127.0.0.1:8000/v1`

### `custom_openai_compat`

- mode: attach existing
- no implicit URL
- user must fill the real endpoint URL

## 4. Planned in-process provider meaning

### `clip_reuse`

- intended for cases where the loaded Comfy pipeline already contains a generation-capable text-side LLM
- this does not mean plain CLIP is chat-capable
- valid target families include setups where the TE itself is effectively an LLM, such as some `NewBie` / `Qwen` designs

### `transformers_inproc`

- local execution directly inside ComfyUI through `transformers`
- no extra backend process window

### `llama_cpp_python_inproc`

- local GGUF execution directly inside ComfyUI through `llama-cpp-python`
- no extra backend process window
- current implemented scope: text tasks
- verified with `Gemma 4 E4B Q4` for `translate_anime_tags`
- visual tasks still need a multimodal llama-cpp-python chat handler before they should be considered supported
- uses `backend_profile` / `custom_model_path` exactly like the current managed backend path
- by default, Task Agent prefers its private runtime path:
  `runtime/python_libs/llama_cpp_python_cu130`
- this avoids overwriting the global `llama-cpp-python` used by TIPO
- default `llama_cpp_python_n_gpu_layers` is conservative (`0`) to avoid hard crashes on low-VRAM setups
- raise `llama_cpp_python_n_gpu_layers` gradually after the CPU path is confirmed working

## 5. Practical recommendation

For current release planning:

- officially recommend `Gemma4 + koboldcpp`
- build and verify `Gemma4 + llama_cpp_server`
- keep `LM Studio / vLLM` as attach-existing convenience options
- then move toward `transformers_inproc` / `llama_cpp_python_inproc` for the no-extra-window release path
