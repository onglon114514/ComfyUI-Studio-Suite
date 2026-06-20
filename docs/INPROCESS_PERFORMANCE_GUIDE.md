# In-Process Performance Guide

This guide covers the current `llama_cpp_python_inproc` runtime.

## Current baseline

Smoke-tested profile:

- `gemma4_e4b_q4`
- `context_size = 1024`
- `max_tokens = 80`
- `llama_cpp_python_n_batch = 512`

Measured on the current local machine:

- CPU mode, `n_gpu_layers = 0`, unload after run:
  - about 30 seconds for a small translation task
- CPU mode, `n_gpu_layers = 0`, keep loaded:
  - first run about 29 seconds
  - second run about 9.6 seconds
- partial GPU offload, `n_gpu_layers = 10`, keep loaded:
  - first run about 27.6 seconds
  - second run about 9.0 seconds

Main conclusion:

- For repeated text-only tasks, `unload_after_run = false` is the most important speed improvement.
- For workflows where the LLM prompt output immediately feeds a sampler or another high-VRAM image model, use `unload_after_run = true`.
- Small GPU offload helps, but the improvement is smaller than avoiding reload cost.

## Node parameters

Task Agent nodes now expose these advanced runtime parameters:

- `llama_cpp_python_n_gpu_layers`
  - `0` means CPU mode.
  - `10` has been smoke-tested with Gemma 4 E4B Q4.
  - `-1` means full offload, but can hard-crash on low VRAM and should be tested only in a separate benchmark process first.
- `llama_cpp_python_n_batch`
  - Default: `512`.
  - Try `256`, `512`, `1024`.
  - Higher is not always faster if memory pressure increases.
- `llama_cpp_python_threads`
  - `0` means let llama.cpp choose.
  - Set manually only if CPU scheduling is poor.

Recommended starting point:

- `Gemma 4 E4B Q4`
- `context_size = 1024` or `2048` for short tag tasks
- `llama_cpp_python_n_gpu_layers = 10`
- `llama_cpp_python_n_batch = 512`
- `unload_after_run = false` for repeated text/caption batches
- `unload_after_run = true` when ComfyUI needs VRAM back for image generation immediately, especially if the prompt output goes straight into a sampler

## Benchmark script

Run from the node package root environment:

```powershell
cd ComfyUI/custom_nodes/comfyui_studio_suite
python scripts/benchmark_inprocess_llama_cpp.py --profile gemma4_e4b_q4 --context-size 1024 --max-tokens 80 --layers 0,10 --batches 512 --keep-loaded --repeats 2
```

Use this before trying aggressive values:

```powershell
cd ComfyUI/custom_nodes/comfyui_studio_suite
python scripts/benchmark_inprocess_llama_cpp.py --profile gemma4_e4b_q4 --context-size 1024 --max-tokens 80 --layers 0,10,20 --batches 512,1024 --keep-loaded --repeats 2
```

Do not start with `--layers -1` inside ComfyUI. Test it in the benchmark script first.

## 26B note

`Gemma 4 26B A4B Q4` may be usable, but it needs separate testing.

Suggested first test:

```powershell
cd ComfyUI/custom_nodes/comfyui_studio_suite
python scripts/benchmark_inprocess_llama_cpp.py --profile gemma4_26b_q4 --context-size 1024 --max-tokens 80 --layers 0 --batches 256 --keep-loaded --repeats 2
```

If that works, try:

- `--layers 10`
- `--batches 512`
- `context_size 2048`

Do not assume 26B in-process performance will match KoboldCpp. KoboldCpp still remains the recommended backend when it is already available and stable.

## Visual status

Current `llama_cpp_python_inproc` has an initial visual path for Gemma4-style GGUF + mmproj.

Smoke-tested profile:

- `gemma4_e4b_hauhau_q4_vision`
- `mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-f16.gguf`
- `llama_cpp_python_chat_format = gemma4`
- `context_size = 1024`
- `n_gpu_layers = 0`

Observed result:

- image encoding/decoding works through `Gemma4ChatHandler`
- a small icon image produced sensible icon/vector tags
- the model may still emit imperfect JSON, so visual tasks can return `parse_fallback`
- fallback now extracts JSON-like tag arrays, so `formatted_prompt` remains usable

Current recommendation:

- use in-process vision for lightweight local tagging tests
- use external KoboldCpp or another mature vision backend for production batches until more image/task tests are done

External backend options remain useful:

- `koboldcpp` with a vision-capable GGUF + mmproj
- or another OpenAI-compatible vision backend

for higher-throughput image tagging or visual captioning tasks.
