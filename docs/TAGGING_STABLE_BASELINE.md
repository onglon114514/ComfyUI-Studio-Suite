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
- the Python environment bundled with or used to launch the current ComfyUI installation
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

## 2026-10-15 Update: Robustness + Plain-NL Mode (zh notes)

- 解析兼容：模型输出 JSON 残缺时自动修复（围栏/引号/尾逗号/截断补全，≤3 层闭合），修复路径带 `parse_status=repaired`；fallback 不再丢弃模型原文、不再硬塞 masterpiece/best quality。
- 纯自然语言模式：任务模组 bundle `chain_wd14_to_anima_train_caption_plain.json`（`caption_output_format=plain`），LLM 输出 3-6 句 prose 直接作第二行，与 WD14 首行拼接的基线格式不变。
- 长度：`caption_max_tokens` 可配（默认 1200）；caption <80 字符才回退模板句。
- 禁词/启动词：`config/tagging_terms.json` 用户可编辑（banned exact/substrings/artifact + trigger_prefix）；过滤只作用 LLM 侧，WD14 首行默认不动；WD14 层过滤用其 `exclude_tags` 槽位（示例工作流已修正误接线）。
- 推理（koboldcpp）：`gemma4_e4b_managed.kcpps` 开 `autofit` + `threads 16` + flash attention；35B-A3B 走"专家张量 CPU、其余全 GPU"分工（9/27 实测 33 T/s vs 20 层方案 11.5 T/s）。
- 观测：节点 `status` 现含 `ok/repaired/fallback` 标记，可批量统计掉格式率。
