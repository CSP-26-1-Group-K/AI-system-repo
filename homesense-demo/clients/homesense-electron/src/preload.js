const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('homeSense', {
  getDefaultServer: () => ipcRenderer.invoke('config:get-default-server'),
});
