# Migration Plan

## Current main package boundary

The current main package should include:

1. `task_agent_core`
2. `queue_nodes`
3. `smart_fill_crop_resize_node`

## Current non-mainline reference source

1. `weilin-comfyui-prompt-all-in-one-page-unlock`
   - extract ideas and UX only
   - do not merge wholesale

## Recommended order

1. stabilize `task_agent_core`
2. keep `queue_nodes` as the batch / isolation module
3. keep `smart_fill_crop_resize_node` as the image prep module
4. design `prompt_studio`
5. migrate useful `weilin` behaviors into `prompt_studio`
6. reduce old package dependency to zero

## Future architecture

- `task_agent_core`
- `prompt_studio`
- `queue_tools`
- `image_prep`
