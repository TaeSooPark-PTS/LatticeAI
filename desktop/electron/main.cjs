const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");

// Experimental compatibility shell. Tauri is the primary desktop client; use
// the same backend port as every other Lattice client to avoid split defaults.
const origin = process.env.LATTICEAI_DESKTOP_BACKEND_ORIGIN || "http://127.0.0.1:4825";
let backend = null;

ipcMain.handle("lattice:select-folder", async (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  const options = {
    title: "Choose a folder for Lattice AI",
    properties: ["openDirectory"],
  };
  const result = win ? await dialog.showOpenDialog(win, options) : await dialog.showOpenDialog(options);
  if (result.canceled || !result.filePaths.length) return null;
  return result.filePaths[0];
});

function startBackend() {
  if (process.env.LATTICEAI_DESKTOP_NO_BACKEND) return;
  const command = process.env.LATTICEAI_DESKTOP_BACKEND_CMD || "python3 -m latticeai.cli.entrypoint --host 127.0.0.1 --port 4825";
  const [bin, ...args] = command.split(/\s+/).filter(Boolean);
  if (!bin) return;
  backend = spawn(bin, args, {
    cwd: process.env.LATTICEAI_DESKTOP_BACKEND_CWD || path.resolve(__dirname, "../.."),
    env: { ...process.env, LATTICEAI_HOST: "127.0.0.1", LATTICEAI_PORT: "4825", LATTICEAI_TUNNEL: "false" },
    stdio: "ignore",
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1024,
    minHeight: 720,
    title: "Lattice AI",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.loadURL(`${origin}/app`);
}

app.whenReady().then(() => {
  startBackend();
  createWindow();
});

app.on("window-all-closed", () => {
  if (backend) backend.kill();
  if (process.platform !== "darwin") app.quit();
});
