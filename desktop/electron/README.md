# Electron compatibility shell

Electron is an experimental compatibility client. The supported desktop build
is the Tauri application in `src-tauri/`; release artifacts and validation use
that path. Electron shares the standard local backend at `127.0.0.1:4825` and
can be launched for development with `npm run desktop:electron`.

Override the backend only for development with
`LATTICEAI_DESKTOP_BACKEND_ORIGIN` or `LATTICEAI_DESKTOP_BACKEND_CMD`.
