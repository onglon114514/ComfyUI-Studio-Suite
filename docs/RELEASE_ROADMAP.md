# Release Roadmap

This roadmap is for pushing the current local development line toward a usable public release.

## Phase 1: current shippable baseline

Goal:

- let other people run the Task Agent nodes with minimum manual setup

Scope:

- direct local execution inside ComfyUI
- managed backend selection in-node
- `koboldcpp` as verified baseline
- `llama.cpp server` as preferred standalone target
- attach-existing support for `LM Studio`, `vLLM`, and generic OpenAI-compatible URLs

Exit condition:

- another machine can use the package without manually starting a separate gateway window

## Phase 2: release hardening

Goal:

- make deployment predictable for non-developers

Required work:

- validate `Gemma 4 E4B + llama.cpp server`
- keep `Gemma 4 E4B + koboldcpp` as fallback
- add more user-facing setup docs
- remove or hide legacy fields that confuse current workflows
- finish Prompt Studio integration cleanup
- prepare in-process provider adapters for generation-capable TE reuse and local `transformers` / `llama-cpp-python`

Exit condition:

- release users can choose one of two documented local runtimes and get the node working from docs alone

## Phase 3: backend abstraction cleanup

Goal:

- stop treating every backend like a special-case patch

Required work:

- separate provider adapters more cleanly
- centralize health checks, launch rules, and model capability metadata
- add capability flags:
  - text-only
  - vision
  - managed local
  - attach existing
  - in-process
  - reusable generation-capable clip-side llm
- expose clearer backend status inside ComfyUI

Exit condition:

- adding a new backend does not require touching task logic everywhere

## Phase 4: long-term integrated architecture

Goal:

- move from "one tagging helper" to a reusable ComfyUI-native LLM subsystem

Required work:

- modular context/task assembly
- reusable task modules for:
  - translation
  - tag expansion
  - format normalization
  - training caption generation
  - reverse prompt generation
- richer visual-model tagging path
- optional no-backend-knowledge UX for end users

Exit condition:

- Task Agent becomes a stable subsystem instead of a workflow-specific patch layer

## Recommended current decision

For the next concrete milestone:

1. Treat `llama.cpp server` as the main independent-runtime target.
2. Keep `koboldcpp` as the verified compatibility fallback.
3. Add in-process providers as the Comfy-native long-term path.
4. Do not try to force ComfyUI-GGUF into the chat-backend role.
5. Publish supported-model guidance instead of trying to ship models.
