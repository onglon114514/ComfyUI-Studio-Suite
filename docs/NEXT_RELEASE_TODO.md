# Next Release TODO

This file tracks the items that should not be presented as finished in the next GitHub push.

## High Priority

- Stabilize Task Agent local LLM runtime for long unattended batch jobs.
- Keep the documented 4060-class baseline as the recommended first path:
  `WD14 tags -> chain_wd14_to_anima_train_caption.json -> llama_cpp_python_inproc -> Gemma 4 E4B Q4`.
- Re-test `llama_cpp_python_inproc` and external backend modes on a clean machine.
- Document that current LLM-assisted image-generation helper workflows are suitable for small runs first, not unattended large production batches.
- Investigate Prompt Studio editor open-time lag and remaining UI bugs.
- Verify Prompt Studio bundled storage works without the old disabled `weilin` directory.

## XY / LoRA Testing

- Add LoRA stack testing for custom stack nodes.
- Add LoRA block/layer weight testing.
- Add a more ergonomic grid-generation workflow, ideally reducing the current two-step "queue first, grid after images finish" process.
- Add example workflows for:
  - LoRA file comparison
  - LoRA file x strength grid
  - sampler/scheduler grid
  - FreeU grid

## Task Agent / Backend

- Mark the gateway path as legacy or optional in docs.
- Treat managed KoboldCpp auto-launch as optional until the PyInstaller self-extraction failure is understood.
- Continue direct-backend integration:
  - KoboldCpp
  - llama.cpp server
  - LM Studio
  - vLLM
  - OpenAI-compatible APIs
- Add a DeepSeek-compatible API example once the backend adapter path is tested.
- Re-check Qwen model profiles before recommending them publicly.
- Re-test vision tagging separately; it is not part of the low-VRAM baseline.

## Cleanup

- Remove or hide legacy compatibility proxy nodes that were only needed for old workflows.
- Separate user-local config from publishable example config more strictly.
- Re-run release doctor on a clean copy before tagging.
- Check package size before push; large optional Danbooru resources should remain outside Git.

## Documentation

- Update README screenshots or example diagrams for the new XY nodes.
- Add a "which node should I use" guide for:
  - Task Agent tag utilities
  - Prompt Studio
  - Independent Queue
  - XY Matrix
  - Smart Fill/Crop/Resize
- Clarify that Prompt Studio derives from a reworked Prompt All-in-One style editor and keep attribution notes current.
