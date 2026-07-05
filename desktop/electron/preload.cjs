const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("latticeDesktop", {
  selectFolder: () => ipcRenderer.invoke("lattice:select-folder"),
});
