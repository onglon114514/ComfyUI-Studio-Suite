# Standalone Runtime Setup

This document is for the publishable "node package can run by itself" path.

## 1. Target deployment modes

### Mode A: managed local runtime

The custom node package starts the backend for the user.

Recommended for release:

- `koboldcpp`
- `llama.cpp server`

### Mode B: attach existing backend

The custom node package connects to an already running backend.

Supported adapter targets:

- `LM Studio`
- `vLLM`
- generic OpenAI-compatible endpoint

## 2. Why ComfyUI itself is not enough here

ComfyUI can host graph execution and diffusion models, but the current Task Agent logic expects:

- OpenAI-style `/v1/chat/completions`
- task-style text output
- optional multimodal message parts
- an independently unloadable text backend

`ComfyUI-GGUF` is useful for loading GGUF diffusion-side models, but it is not a drop-in chat backend for the current Task Agent pipeline.

## 3. Recommended release baseline

For the first release that other people can actually use with low friction:

1. Officially support `Gemma 4 E4B` first.
2. Keep `koboldcpp` as the verified baseline.
3. Add `llama.cpp server` as the preferred "clean standalone" path.
4. Keep `LM Studio` and `vLLM` as attach-existing advanced options.

## 4. Managed runtime folder layout

Recommended package layout:

```text
comfyui_studio_suite/
  runtime/
    koboldcpp/
      koboldcpp.exe
      ...other runtime files if needed
    llama.cpp/
      llama-server.exe
      ...DLLs / companion files from the llama.cpp release
```

Important:

- For `llama.cpp`, do not copy only the exe if the release package ships with required DLLs beside it.
- Copy the whole runtime folder contents when possible.

## 5. Node-side behavior

When the Task Agent node is set to:

- `backend_provider = koboldcpp`
  - it uses managed local launch
- `backend_provider = llama_cpp_server`
  - it uses managed local launch
- `backend_provider = lm_studio`
  - it switches to attach-existing mode
- `backend_provider = vllm`
  - it switches to attach-existing mode
- `backend_provider = custom_openai_compat`
  - it switches to attach-existing mode

## 6. Local setup flow for release users

### Option A: bundle runtime into the package

1. Put `koboldcpp` or `llama.cpp` runtime files into the package `runtime/` folder.
2. Set the backend provider in `config/task_agent_config.local.json`.
3. Launch ComfyUI.
4. Use Task Agent nodes directly inside the graph.

### Option B: use the helper script

Use:

```powershell
.\scripts\setup_managed_backend.ps1 -Provider llama_cpp_server -SourcePath "<path-to-llama-cpp-bundle>"
```

Or:

```powershell
.\scripts\setup_managed_backend.ps1 -Provider koboldcpp -SourcePath "<path-to-koboldcpp-folder>"
```

The script will:

- copy runtime files into the package
- update `config/task_agent_config.local.json`
- set the provider-specific default health path

## 7. Recommended next validation

Before release, validate these two matrices:

### Required

- `Gemma 4 E4B + koboldcpp`
- `Gemma 4 E4B + llama.cpp server`

### Optional

- `Gemma 4 E4B + LM Studio`
- `Gemma 4 E4B + vLLM`

## 8. Current practical conclusion

If the goal is "do not force users to manually run KoboldCpp first", the most practical mid-term solution is:

- keep Task Agent in direct local mode
- let the node package spawn a bundled `llama.cpp server` or `koboldcpp`
- expose provider selection inside the node
- document model recommendations instead of shipping models
