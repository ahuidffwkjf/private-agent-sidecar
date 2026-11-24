const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('connectionSettings',{
  read:()=>ipcRenderer.invoke('connection:read'),
  importAccess:()=>ipcRenderer.invoke('connection:import'),
  save:value=>ipcRenderer.invoke('connection:save',value)
});
