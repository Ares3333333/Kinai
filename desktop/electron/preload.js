const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("kinaestheticDesktop", {
  platform: process.platform,
  urls: () => ipcRenderer.invoke("desktop:urls"),
  openOverlay: () => ipcRenderer.invoke("overlay:open"),
  closeOverlay: () => ipcRenderer.invoke("overlay:close"),
  openPlayer: () => ipcRenderer.invoke("player:open"),
  openRoute: (route) => ipcRenderer.invoke("route:open", route),
  restartEngine: () => ipcRenderer.invoke("engine:restart"),
  engineStatus: () => ipcRenderer.invoke("engine:status")
});
