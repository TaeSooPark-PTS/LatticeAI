# Lattice AI Extension

This extension package can be installed into:

- VS Code
- Cursor
- Antigravity

All three use the same VSIX package format.

## Build

```bash
cd vscode-extension
npm install
npm run build
npm run package:vsix
```

## Install to all three editors

```bash
cd vscode-extension
npm run install:all
```

The script installs the latest `.vsix` into `code`, `cursor`, and `antigravity` if each CLI is available.

## Publish

```bash
npm run publish:vscode
npm run publish:openvsx
```

Before publishing, log in with `vsce login <publisher>` and configure an Open VSX token for `ovsx`.
