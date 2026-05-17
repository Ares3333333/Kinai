const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("kinaestheticDesktop", {
  platform: process.platform,
  openOverlay: () => ipcRenderer.invoke("overlay:open"),
  closeOverlay: () => ipcRenderer.invoke("overlay:close"),
  restartEngine: () => ipcRenderer.invoke("engine:restart"),
  engineStatus: () => ipcRenderer.invoke("engine:status")
});
