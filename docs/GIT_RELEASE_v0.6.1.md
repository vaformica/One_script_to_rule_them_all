# Git Repository Summary — v0.6.1

## Commit message

```text
Fix Mac GUI class structure and add startup integrity validation
```

## GitHub release title

Beetle IDtracker Unified Pipeline v0.6.1 — Mac GUI Startup Repair

## Description of changes

- Corrected `diagnostics_page`, which had been placed outside the PyQt
  `Window` class.
- Restored `choose_key`, `test_save`, recursive scan, submission, and SSH
  helpers as proper `Window` methods.
- Restored all Jobs and Diagnostics handlers as proper `Window` methods.
- Added `validate_window_class()` so missing GUI methods are detected before
  the application window is constructed.
- Added `scripts/mac/replace_existing_install.sh` to back up and replace an
  existing Mac installation while preserving `config/user.json`.

## Upgrade steps

```bash
bash scripts/mac/replace_existing_install.sh
cd ~/Library/CloudStorage/Dropbox/Projects/One_script_to_rule_them_all
bash scripts/mac/install.sh
bash scripts/mac/launch.sh
```

## Validation performed

- Python compilation succeeded.
- AST inspection confirmed all required methods are members of `Window`.
