const { app, BrowserWindow } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");

const origin = process.env.LATTICEAI_DESKTOP_BACKEND_ORIGIN || "http://127.0.0.1:8765";
let backend = null;

function startBackend() {
  if (process.env.LATTICEAI_DESKTOP_NO_BACKEND) return;
  const command = process.env.LATTICEAI_DESKTOP_BACKEND_CMD || "python3 ltcai_cli.py --host 127.0.0.1 --port 8765";
  const [bin, ...args] = command.split(/\s+/).filter(Boolean);
  if (!bin) return;
  backend = spawn(bin, args, {
    cwd: process.env.LATTICEAI_DESKTOP_BACKEND_CWD || path.resolve(__dirname, "../.."),
    env: { ...process.env, LATTICEAI_HOST: "127.0.0.1", LATTICEAI_PORT: "8765", LATTICEAI_TUNNEL: "false" },
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
