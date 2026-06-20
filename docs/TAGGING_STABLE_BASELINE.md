# Tagging Stable Baseline

This is the conservative baseline tested before the next preview release.

## Recommended Path

Use this path for 4060-class community machines:

- WD14 generates the original Danbooru tag line.
- Task Agent uses `llama_cpp_python_inproc`.
- Model profile: `gemma4_e4b_q4`.
- Task bundle: `resources/task_bundles/chain_wd14_to_anima_train_caption.json`.
- Format module: `anima_train_v1`.
- Do not pass image input to the LLM in this baseline.
- Do not rewrite WD14 tags with the LLM.
- Keep the LLM loaded during a queue batch, then unload at the end with the cleanup node.

Expected training text:

```text
wd14, original, tags, kept, as, first, line

Natural-language caption generated from the WD14 tags.
```

## Tested Settings

- Backend provider: `llama_cpp_python_inproc`
- Context size: `4096`
- GPU layers: `0`
- Batch: `128`
- Threads: `8`
- Temperature: `0.25` for direct caption tests, `0.4` through the task bundle
- Max tokens: `320-520`

The CPU-only setting is slower, but it avoids GPU memory competition with ComfyUI image generation and was the most stable local baseline in testing.

## Test Result

Environment:

- Windows
- ComfyUI embedded Python from `D:\ComfyUI-aki-v2\python\python.exe`
- 8GB VRAM GPU class
- Gemma 4 E4B Q4 GGUF

Results:

- 5 consecutive direct caption requests completed successfully.
- Total time: about 103 seconds.
- Per item after model load: about 19-24 seconds.
- Output JSON parsed successfully for all 5 samples.
- Natural-language caption length: about 216-286 characters.
- No `masterpiece`, `best quality`, or `score_` quality tags appeared in the generated caption.
- In-process model unload released memory back close to the pre-run state.

Node-output validation:

- `positive_prompt` started with the exact WD14 input tag string.
- `positive_prompt` contained a blank line between WD14 tags and the natural-language caption.
- `negative_prompt` stayed empty for training caption output.
- `status` was `success`.

## Not Recommended As Baseline Yet

- Managed KoboldCpp auto-launch is not stable enough on the test machine. The current `koboldcpp.exe` failed during PyInstaller self-extraction with `Failed to extract VCRUNTIME140.dll: fopen: Permission denied`.
- Vision LLM tagging is not the 4060-class baseline yet. It should remain optional because it increases VRAM pressure and batch instability.
- `refine_wd14_tags` should not be used in the default training-caption chain for now. It can make the natural-language caption too generic. The stable bundle now uses `generate_natural_caption` only.
- 26B Q4 models are useful for quality experiments, but they should not be the default recommendation for unattended community batch tagging.

## Release Guidance

For the next preview release, describe this as the stable minimal path, not as a fully solved production batch system.

Recommended wording:

> For local low-VRAM usage, start with WD14 tags plus Gemma 4 E4B Q4 in-process text captioning. Keep WD14 tags unchanged and let the LLM add only the natural-language caption line. Use external backends or larger models only after this baseline works on your machine.
