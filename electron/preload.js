const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('backendAPI', {
  baseUrl: 'http://127.0.0.1:49173',
  onBackendStatus: (cb) => ipcRenderer.on('backend-status', (_, d) => cb(d)),
  openExternal: (url) => ipcRenderer.invoke('shell:open-external', url),
  relaunch: () => ipcRenderer.invoke('app:relaunch'),
  saveOutputToSourceDir: (payload) => ipcRenderer.invoke('result:save-source-directory', payload),
  saveOutputToBatchDir: (payload) => ipcRenderer.invoke('result:save-batch-directory', payload)
});

contextBridge.exposeInMainWorld('windowControls', {
  minimize: () => ipcRenderer.invoke('window:minimize'),
  toggleMaximize: () => ipcRenderer.invoke('window:toggle-maximize'),
  close: () => ipcRenderer.invoke('window:close'),
  isMaximized: () => ipcRenderer.invoke('window:is-maximized')
});
