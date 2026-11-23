const {app,BrowserWindow,Menu,dialog,ipcMain,session,shell}=require('electron');
const {spawn}=require('node:child_process');
const {randomBytes}=require('node:crypto');
const fs=require('node:fs');
const path=require('node:path');
const readline=require('node:readline');
const {validateConnection,trustedNavigation,backendCommand}=require('./policy.cjs');

app.setName('Sidecar');
if(process.env.SIDECAR_SMOKE_DATA_DIR){
  if(!path.isAbsolute(process.env.SIDECAR_SMOKE_DATA_DIR))throw new Error('Smoke data directory must be absolute');
  app.setPath('userData',process.env.SIDECAR_SMOKE_DATA_DIR);
}
const root=path.resolve(__dirname,'..');
let window,settingsWindow,backend,config={mode:'local'},origin='',quitting=false,backendReady=false;
const settingsFile=()=>path.join(app.getPath('userData'),'connection.json');
const settingsURL=require('node:url').pathToFileURL(path.join(__dirname,'settings.html')).href;

function writeSettings(value){
  const filename=settingsFile();fs.mkdirSync(path.dirname(filename),{recursive:true,mode:0o700});
  const temporary=filename+'.tmp';fs.writeFileSync(temporary,JSON.stringify(value,null,2),{mode:0o600});
  fs.renameSync(temporary,filename);
}
function connectionSettings(){
  if(settingsWindow&&!settingsWindow.isDestroyed()){settingsWindow.focus();return;}
  settingsWindow=new BrowserWindow({width:560,height:460,minWidth:480,minHeight:420,parent:window,
    title:'连接设置',autoHideMenuBar:true,webPreferences:{preload:path.join(__dirname,'preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true}});
  settingsWindow.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  settingsWindow.webContents.on('will-navigate',(event,url)=>{if(url!==settingsURL)event.preventDefault();});
  settingsWindow.loadFile(path.join(__dirname,'settings.html'));
  settingsWindow.on('closed',()=>{settingsWindow=null;});
}
function settingsSender(event){
  if(!settingsWindow||event.sender!==settingsWindow.webContents||event.senderFrame!==settingsWindow.webContents.mainFrame||event.senderFrame.url!==settingsURL)throw new Error('Settings IPC is restricted to the settings window');
}
function createMenu(){
  const mac=process.platform==='darwin';
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(mac?[{label:'Sidecar',submenu:[{role:'about'},{type:'separator'},{label:'连接设置…',accelerator:'CmdOrCtrl+,',click:connectionSettings},{type:'separator'},{role:'hide'},{role:'hideOthers'},{role:'unhide'},{type:'separator'},{role:'quit'}]}]:[]),
    {label:'文件',submenu:[{label:'连接设置…',accelerator:mac?undefined:'Ctrl+,',click:connectionSettings},{label:'打开数据目录',click:()=>shell.openPath(app.getPath('userData'))},{type:'separator'},mac?{role:'close'}:{role:'quit'}]},
    {label:'编辑',submenu:[{role:'undo'},{role:'redo'},{type:'separator'},{role:'cut'},{role:'copy'},{role:'paste'},{role:'selectAll'}]},
    {label:'视图',submenu:[{label:'重新加载',accelerator:'CmdOrCtrl+R',click:()=>window?.reload()},{role:'resetZoom'},{role:'zoomIn'},{role:'zoomOut'},{role:'togglefullscreen'}]},
    {label:'窗口',submenu:[{role:'minimize'},{role:'zoom'},...(mac?[{role:'front'}]:[])]}
  ]));
}
async function stopBackend(){
  const child=backend;backend=null;backendReady=false;
  if(!child||child.exitCode!==null)return;
  await new Promise(resolve=>{
    const timeout=setTimeout(()=>{child.kill('SIGKILL');resolve();},7000);
    child.once('exit',()=>{clearTimeout(timeout);resolve();});
    try{child.stdin.end('shutdown\n');}catch{child.kill();}
  });
}
async function startBackend(){
  const token=randomBytes(32).toString('hex');
  const command=backendCommand({packaged:app.isPackaged,resources:process.resourcesPath,root});
  if(!fs.existsSync(command.file))throw new Error('找不到本机后端。源码运行请先执行 npm run setup；发行版请重新安装完整安装包。');
  const logs=path.join(app.getPath('userData'),'logs');fs.mkdirSync(logs,{recursive:true,mode:0o700});
  const fd=fs.openSync(path.join(logs,'backend.log'),'w',0o600);
  const child=spawn(command.file,command.args,{cwd:app.isPackaged?process.resourcesPath:root,windowsHide:true,stdio:['pipe','pipe',fd],env:{...process.env,PYTHONUTF8:'1',PYTHONUNBUFFERED:'1',PYTHONDONTWRITEBYTECODE:'1'}});
  fs.closeSync(fd);backend=child;
  const ready=await new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>reject(new Error('本机服务启动超时，请查看数据目录中的 logs/backend.log')),60000);
    const lines=readline.createInterface({input:child.stdout});
    const fail=()=>{clearTimeout(timeout);reject(new Error('本机服务已停止，请查看 logs/backend.log'));};
    child.once('error',error=>{clearTimeout(timeout);reject(error);});
    child.once('exit',fail);
    lines.once('line',line=>{
      clearTimeout(timeout);lines.close();
      try{
        if(line.length>4096)throw new Error('服务启动响应过长');
        const value=JSON.parse(line),url=new URL(value.url);
        if(url.protocol!=='http:'||url.hostname!=='127.0.0.1'||url.username||url.password||url.pathname!=='/'||url.search||url.hash||!url.port)throw new Error('服务启动地址无效');
        child.removeListener('exit',fail);resolve(url.origin);
      }catch(error){reject(error);}
    });
    // Session token is private IPC data; never command-line arguments or query strings.
    child.stdin.on('error',()=>{});
    child.stdin.write(JSON.stringify({token,data_dir:path.join(app.getPath('userData'),'workspace')})+'\n');
  });
  backendReady=true;
  child.once('exit',()=>{
    if(!quitting&&backendReady){backendReady=false;dialog.showMessageBox(window,{type:'error',message:'本机服务已停止',detail:'已有数据仍保存在本机。请退出并重新启动应用。'});}
  });
  return {origin:ready,token};
}
async function start(){
  try {if(fs.existsSync(settingsFile()))config=validateConnection(JSON.parse(fs.readFileSync(settingsFile(),'utf8')));}
  catch {config={mode:'local'};await dialog.showMessageBox({type:'warning',message:'连接设置无效，已切换到本机工作区。'});}
  const isolated=session.fromPartition(`sidecar-${randomBytes(12).toString('hex')}`);
  isolated.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false));
  isolated.setPermissionCheckHandler(()=>false);
  window=new BrowserWindow({width:1180,height:800,minWidth:920,minHeight:620,title:'Sidecar',backgroundColor:'#f5f5f5',
    icon:path.join(__dirname,'assets/icon.png'),show:false,autoHideMenuBar:process.platform!=='darwin',
    webPreferences:{session:isolated,nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true,allowRunningInsecureContent:false}});
  createMenu();window.once('ready-to-show',()=>window.show());
  window.on('closed',()=>{window=null;});
  window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  for(const eventName of ['will-navigate','will-redirect'])window.webContents.on(eventName,(event,url)=>{if(!trustedNavigation(url,origin))event.preventDefault();});
  window.webContents.on('will-attach-webview',event=>event.preventDefault());
  await window.loadFile(path.join(__dirname,'assets/loading.html'));window.show();
  try {
    if(config.mode==='local'){
      const result=await startBackend();origin=result.origin;
      await isolated.cookies.set({url:origin,name:'sidecar_desktop_session',value:result.token,httpOnly:true,sameSite:'strict',path:'/'});
    }else origin=config.url;
    await window.loadURL(origin+'/ui?desktop=1');
  } catch(error){
    await stopBackend();
    if(quitting)return;
    const response=await dialog.showMessageBox(window,{type:'error',message:'无法启动 Sidecar',detail:error.message,buttons:['连接设置','退出']});
    if(response.response===0)connectionSettings();else app.quit();
  }
}
if(!app.requestSingleInstanceLock())app.quit();
else {
  app.on('second-instance',()=>{if(window){if(window.isMinimized())window.restore();window.show();window.focus();}});
  app.whenReady().then(()=>{
    ipcMain.handle('connection:read',event=>{settingsSender(event);return config;});
    ipcMain.handle('connection:save',async(event,value)=>{
      settingsSender(event);const next=validateConnection(value);
      if(next.mode==='remote'){
        const answer=await dialog.showMessageBox(settingsWindow,{type:'question',message:'连接到此 Sidecar 服务？',detail:next.url+'\n该服务将提供应用页面和数据，仅连接你信任的服务。',buttons:['取消','连接'],defaultId:0,cancelId:0});
        if(answer.response!==1)return {saved:false};
      }
      writeSettings(next);app.relaunch();app.quit();return {saved:true};
    });
    return start();
  }).catch(error=>{dialog.showErrorBox('Sidecar 启动失败',error.message);app.quit();});
  app.on('activate',()=>{if(window){window.show();window.focus();}});
  app.on('window-all-closed',()=>app.quit());
  app.on('before-quit',event=>{if(!quitting){event.preventDefault();quitting=true;stopBackend().finally(()=>app.quit());}});
}
