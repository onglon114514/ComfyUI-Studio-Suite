# Backend Provider Plan

Current recommended backend architecture:

## 1. Backend mode split

- `inprocess`
  - task execution happens inside the ComfyUI Python process
- `managed_process`
  - node package starts and stops the backend itself
- `attach_existing`
  - node package connects to an already running OpenAI-compatible backend

## 2. Provider split

### In-process providers

- `clip_reuse`
  - for pipelines where the loaded "text encoder" is actually a reusable generation-capable LLM
  - examples in local Comfy ecosystem: `NewBie Gemma`, `Qwen TE`-style setups
  - compatibility path, not the main performance-oriented runtime target
- `transformers_inproc`
  - main in-process HF runtime target
- `llama_cpp_python_inproc`
  - main in-process GGUF runtime target

### Managed providers

- `koboldcpp`
- `llama_cpp_server`

### Attach providers

- `custom_openai_compat`
- `lm_studio`
- `vllm`

For the attach providers above, the current implementation can already work through `base_url` as long as the backend is OpenAI-compatible.

## 3. Why `llama_cpp_server` was the first external-runtime priority

The task engine already sends requests in this shape:

- `POST /v1/chat/completions`
- OpenAI-style `messages`
- multimodal `image_url` content parts for visual tasks

This is already aligned with llama.cpp server's OpenAI-compatible API design, so adapting it is much cheaper than rewriting the task engine around an in-process Python binding.

## 4. Why in-process providers are still needed

Even with managed backends working, in-process providers remain important because:

- they avoid extra backend windows and process juggling
- they fit ComfyUI-native deployment better
- they match existing ecosystem patterns where some nodes already run LLMs in-process
- some pipelines can reuse an already loaded generation-capable TE instead of loading a second backend

## 5. Why current `ComfyUI-GGUF` is not enough

`ComfyUI-GGUF` in the current local environment is a GGUF model loader for native ComfyUI model nodes:

- `Unet Loader (GGUF)`
- `CLIPLoader (GGUF)`

It is not a drop-in replacement for the current task backend because it does not provide:

- a generic chat-completions task interface
- the OpenAI-compatible HTTP service layer used by Task Agent
- a ready-made LLM task execution node for your existing tagging pipeline

## 6. Release recommendation

For the first publishable backend matrix:

- Officially supported:
  - `koboldcpp`
  - `llama_cpp_server`
- Planned first-class next step:
  - `transformers_inproc`
  - `llama_cpp_python_inproc`
  - `clip_reuse` for generation-capable TE families as a lower-priority compatibility path
- Experimental / attach-existing:
  - `LM Studio`
  - `vLLM`
  - any OpenAI-compatible endpoint
