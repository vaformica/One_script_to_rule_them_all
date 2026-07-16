# Git-Ready Release Summary — v0.7.1

## Semantic version

`0.7.1`

## Git commit message

```text
Normalize threshold numeric types for IDtracker.ai 6.0.10
```

## GitHub release title

Beetle IDtracker Unified Pipeline v0.7.1 — Homogeneous Numeric TOML Patch

## Bug fixed

The v0.7.0 GUI could preserve the maximum intensity threshold as an integer
while writing the edited minimum as a float:

```toml
intensity_ths = [25.0, 255]
```

IDtracker.ai 6.0.10 uses an older TOML parser that rejects arrays mixing
integer and floating-point values. It therefore reported:

```text
Not a homogeneous array
```

v0.7.1 normalizes every threshold pair to one numeric TOML type:

```toml
intensity_ths = [25, 255]
```

or, when floating-point values are required:

```toml
area_ths = [185.0, inf]
```

## Validation improvements

- Mac-side validation rejects mixed integer/float threshold pairs.
- Firebird preflight validation rejects the same error before `sbatch`.
- Flat and nested per-ROI threshold arrays remain supported.
- Background remains the minimum intensity threshold; the maximum is preserved.

## Upgrade

Install the update on both systems.

Mac:

```bash
bash scripts/mac/install.sh
```

Firebird:

```bash
bash scripts/firebird/install.sh
```

Submit a new run after upgrading. Do not reuse the failed run directory.
