# In-Process Provider Plan

This document covers the path where Task Agent does not rely on an external HTTP backend process.

## 1. Why this path exists

The current managed backend path works, but it still has drawbacks for release use:

- users may need extra runtime files
- external process lifecycle is another failure surface
- Windows users notice backend launch behavior more easily
- local deployment feels less "Comfy-native"

For long-term usability, Task Agent should also support in-process execution.

## 2. Important distinction: not every text encoder is chat-capable

Do not assume every image-model text encoder can be used for dialogue.

### Not suitable by default

- plain CLIP encoders
- SDXL-style text encoders that only output embeddings

### Suitable in some model families

- `NewBie`-style pipelines where the "TE" is actually a reusable Gemma-family LLM
- `Qwen`-style pipelines where the "TE" itself has generation capability

In those cases, the Comfy-side "text encoder" is not only an embedding producer. It is a generation-capable language model that may be reused for task execution.

## 3. Proposed in-process provider split

### `clip_reuse`

Use when the loaded Comfy pipeline already contains a generation-capable text-side LLM.

Examples:

- NewBie Gemma
- Qwen-based TE designs

Intended use:

- tag translation
- tag expansion
- formatting / normalization
- training caption writing

Priority:

- compatibility path
- not the main performance-oriented runtime target
- useful when the graph already has such a model loaded and reuse is cheaper than loading another runtime

### `transformers_inproc`

Load a HuggingFace-compatible model directly inside the ComfyUI Python process.

Intended use:

- general local text task execution
- local visual LLM execution when supported by the model family

Priority:

- primary in-process target

### `llama_cpp_python_inproc`

Run GGUF through `llama-cpp-python` directly inside the ComfyUI Python process.

Intended use:

- no-extra-window local runtime
- easier "single node pack" local deployment
- compatible path for release users who do not want to run a separate backend manually

Priority:

- primary in-process target

## 4. Practical architectural rule

Short version:

- if the user wants the main local HF runtime, use `transformers_inproc`
- if the user wants the main local GGUF runtime, use `llama_cpp_python_inproc`
- if a pipeline already contains a generation-capable LLM and reuse is good enough, use `clip_reuse`
- keep `koboldcpp` and `llama_cpp_server` as compatible managed backends

## 5. Release implication

This means the long-term architecture should not be only:

- managed backend
- attach-existing backend

It should become:

- in-process provider
- managed backend
- attach-existing backend

## 6. Current implementation status

Current codebase status:

- provider architecture for managed / attach-existing is already present
- `llama_cpp_python_inproc` text execution is implemented as the first in-process runtime
- `llama_cpp_python_inproc` has been smoke-tested with `Gemma 4 E4B Q4` on the installed ComfyUI node path
- `transformers_inproc` is planned
- `clip_reuse` is planned as a lower-priority compatibility path
- visual llama-cpp-python execution has an initial Gemma4/mmproj path through `Gemma4ChatHandler`
- current visual output may still use `parse_fallback`, but the fallback now extracts JSON-like tag fields instead of polluting prompts with raw broken JSON

## 7. TIPO compatibility note

The existing TIPO extension also imports `llama_cpp` through `tipo-kgen`.

To avoid breaking that extension, Task Agent does not overwrite the global ComfyUI `llama-cpp-python` install by default. Instead, `llama_cpp_python_inproc` prefers the private runtime path:

```text
runtime/python_libs/llama_cpp_python_cu130
```

This allows:

- TIPO to keep using the globally installed `llama-cpp-python`
- Task Agent to use a newer private build that supports newer GGUF architectures such as `gemma4`

Current conservative default:

- `llama_cpp_python_n_gpu_layers = 0`

Reason:

- CPU mode is slower but avoids hard process crashes on low-VRAM machines.
- After CPU mode is verified, GPU layer count can be raised gradually.
