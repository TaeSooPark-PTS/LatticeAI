# v4.5.1 RC Artifact Manifest

Release date: 2026-06-13

## Screenshots

- `output/audits/v4.5.1-reimagining/screenshots/home-desktop.png`
- `output/audits/v4.5.1-reimagining/screenshots/home-mobile.png`
- `output/audits/v4.5.1-reimagining/screenshots/memory-map.png`
- `output/audits/v4.5.1-reimagining/screenshots/library-models.png`

## GIFs

- `output/audits/v4.5.1-reimagining/gifs/v4.5.1-reimagining-walkthrough.gif`

## Release Artifacts

- `dist/ltcai-4.5.1-py3-none-any.whl`
- `dist/ltcai-4.5.1.tar.gz`
- `ltcai-4.5.1.tgz`
- `dist/ltcai-4.5.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.1_aarch64.dmg`

## SHA-256

| Artifact | SHA-256 |
| --- | --- |
| `dist/ltcai-4.5.1-py3-none-any.whl` | `3d5ce1a0a85f7aba1f78587cd7e4a66c63dd5c03ddde7cee57624ec3f487899b` |
| `dist/ltcai-4.5.1.tar.gz` | `62f0e05ff32554cf599b76678de3136bc02e5af4775144e7347182eed0fb4675` |
| `ltcai-4.5.1.tgz` | `e755f40f87484d8a6e3f6bc95f48f0f78e1d0fcde3af8b14c709cf7fa71b2e4b` |
| `dist/ltcai-4.5.1.vsix` | `3badc5915dc31425fa383d5946f78e0914497aa9e523cb7fbdc81a295b8f4a2f` |
| `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.1_aarch64.dmg` | `689ffc9553facf3987a5d57016dfd24fc3872f9c0810e28621f96b340ca38ce0` |

## Validation

`npm run release:validate` passed for the exact v4.5.1 artifact set. Historical
files remain in `dist/` for prior releases, so all publish or upload steps must
use exact v4.5.1 filenames only.

## Notes

The artifact list is exact-version only. Publishing remains out of scope for
this RC unless the repository owner explicitly performs the manual registry
publish steps.
