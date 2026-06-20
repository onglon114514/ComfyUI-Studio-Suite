# Prompt Studio Storage

This directory contains default Prompt Studio data shipped with the node package.

## Contents

- `autocomplete/autocomplete.txt`
  - default autocomplete vocabulary
- `group_tags/*.yaml`
  - Prompt Studio group-tag/category data
- `local_complete_tags/*.csv`
  - local tag completion/translation CSV resources
- `prompt_data/*.json`
  - default prompt history, favorites, UI state, and NewBie XML wizard settings

## Runtime Behavior

Prompt Studio reads this package-local storage first.

If a legacy disabled `weilin-comfyui-prompt-all-in-one-page-unlock` directory exists, it is only used as a fallback for older local installs.

New writes go to this package-local `prompt_studio/storage` directory.

## Release Note

Keep user-private prompts out of public release packages if they include private character names, paid prompt presets, internal project references, or other non-public data.
