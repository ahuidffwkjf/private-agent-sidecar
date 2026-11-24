const {app,BrowserWindow,Menu,dialog,ipcMain,session,shell}=require('electron');
const {spawn}=require('node:child_process');
const {randomBytes}=require('node:crypto');
const fs=require('node:fs');
const path=require('node:path');
const readline=require('node:readline');
const {validateConnection,trustedNavigation,trustedLocalFileNavigation,backendCommand,validateReady}=require('./policy.cjs');

app.setName('Sidecar Toy');
app.setPath('userData',path.join(app.getPath('appData'),'Sidecar Toy'));
if(process.env.SIDECAR_SMOKE_DATA_DIR){
  if(!path.isAbsolute(process.env.SIDECAR_SMOKE_DATA_DIR))throw new Error('Smoke data directory must be absolute');
  app.setPath('userData',process.env.SIDECAR_SMOKE_DATA_DIR);
}
const root=path.resolve(__dirname,'..');
let window,settingsWindow,backend,config={mode:'ecs'},origin='',quitting=false,backendReady=false;
let localUsers=[],selectedUser='Alice';
const settingsFile=()=>path.join(app.getPath('userData'),'connection.json');
const accessFile=()=>path.join(app.getPath('userData'),'ecs-access.private.json');
const settingsURL=require('node:url').pathToFileURL(path.join(__dirname,'settings.html')).href;

function writeSettings(value){
  const filename=settingsFile();fs.mkdirSync(path.dirname(filename),{recursive:true,mode:0o700});
  const temporary=filename+'.tmp';fs.writeFileSync(temporary,JSON.stringify(value,null,2),{mode:0o600});
  fs.renameSync(temporary,filename);
}
function connectionSettings(){
  if(settingsWindow&&!settingsWindow.isDestroyed()){settingsWindow.focus();return;}
  settingsWindow=new BrowserWindow({width:580,height:650,minWidth:480,minHeight:560,parent:window,
    title:'连接设置',autoHideMenuBar:true,webPreferences:{preload:path.join(__dirname,'preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true}});
  settingsWindow.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  settingsWindow.webContents.on('will-navigate',(event,url)=>{if(!trustedLocalFileNavigation(url,settingsURL))event.preventDefault();});
  settingsWindow.loadFile(path.join(__dirname,'settings.html'));
  settingsWindow.on('closed',()=>{settingsWindow=null;});
}
function settingsSender(event){
  if(!settingsWindow||event.sender!==settingsWindow.webContents||event.senderFrame!==settingsWindow.webContents.mainFrame||!trustedLocalFileNavigation(event.senderFrame.url,settingsURL))throw new Error('Settings IPC is restricted to the settings window');
}
function createMenu(){
  const mac=process.platform==='darwin';
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(mac?[{label:'Sidecar Toy',submenu:[{role:'about'},{type:'separator'},{label:'连接设置…',accelerator:'CmdOrCtrl+,',click:connectionSettings},{type:'separator'},{role:'hide'},{role:'hideOthers'},{role:'unhide'},{type:'separator'},{role:'quit'}]}]:[]),
    {label:'文件',submenu:[{label:'连接设置…',accelerator:mac?undefined:'Ctrl+,',click:connectionSettings},{label:'打开数据目录',click:()=>shell.openPath(app.getPath('userData'))},{type:'separator'},mac?{role:'close'}:{role:'quit'}]},
    ...(localUsers.length?[{label:'演示用户',submenu:localUsers.map((user,index)=>({label:user.name,type:'radio',checked:user.name===selectedUser,accelerator:`CmdOrCtrl+${index+1}`,click:()=>switchUser(user)}))}]:[]),
    {label:'编辑',submenu:[{role:'undo'},{role:'redo'},{type:'separator'},{role:'cut'},{role:'copy'},{role:'paste'},{role:'selectAll'}]},
    {label:'视图',submenu:[{label:'重新加载',accelerator:'CmdOrCtrl+R',click:()=>window?.reload()},{role:'resetZoom'},{role:'zoomIn'},{role:'zoomOut'},{role:'togglefullscreen'}]},
    {label:'窗口',submenu:[{role:'minimize'},{role:'zoom'},...(mac?[{role:'front'}]:[])]}
  ]));
}
async function switchUser(user){
  if(!window||!localUsers.some(u=>u.name===user.name&&u.url===user.url))return;
  selectedUser=user.name;origin=user.url;createMenu();
  window.setTitle('Sidecar Toy · '+selectedUser);
  try{await window.loadURL(origin+'/ui?desktop=1&toy=1');}
  catch{dialog.showErrorBox('用户切换失败','本地演示服务未响应，请退出应用后重新启动。');}
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
  if(config.mode==='ecs'&&!fs.existsSync(accessFile()))throw new Error('请先在连接设置中导入 ECS 接入文件。');
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
        const value=validateReady(JSON.parse(line));
        child.removeListener('exit',fail);resolve(value);
      }catch(error){reject(error);}
    });
    // Session token is private IPC data; never command-line arguments or query strings.
    child.stdin.on('error',()=>{});
    child.stdin.write(JSON.stringify({token,data_dir:path.join(app.getPath('userData'),config.mode==='ecs'?'ecs-workspace':'workspace'),mode:config.mode==='ecs'?'ecs':'toy',...(config.mode==='ecs'?{access_file:accessFile()}:{})})+'\n');
  });
  backendReady=true;
  child.once('exit',()=>{
    if(!quitting&&backendReady){backendReady=false;dialog.showMessageBox(window,{type:'error',message:'本机服务已停止',detail:'已有数据仍保存在本机。请退出并重新启动应用。'});}
  });
  return {...ready,token};
}
async function start(){
  try {if(fs.existsSync(settingsFile()))config=validateConnection(JSON.parse(fs.readFileSync(settingsFile(),'utf8')));}
  catch {config={mode:'ecs'};await dialog.showMessageBox({type:'warning',message:'连接设置无效，请重新配置 ECS 接入。'});}
  const isolated=session.fromPartition(`sidecar-${randomBytes(12).toString('hex')}`);
  if(config.mode!=='remote')await isolated.setProxy({mode:'direct'});
  isolated.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false));
  isolated.setPermissionCheckHandler(()=>false);
  window=new BrowserWindow({width:1180,height:800,minWidth:920,minHeight:620,title:'Sidecar Toy',backgroundColor:'#f5f5f5',
    icon:path.join(__dirname,'assets/icon.png'),show:false,autoHideMenuBar:process.platform!=='darwin',
    webPreferences:{session:isolated,nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true,allowRunningInsecureContent:false}});
  createMenu();window.once('ready-to-show',()=>window.show());
  window.on('closed',()=>{window=null;});
  window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  window.webContents.on('page-title-updated',event=>{event.preventDefault();window?.setTitle('Sidecar Toy'+(localUsers.length?' · '+selectedUser:''));});
  for(const eventName of ['will-navigate','will-redirect'])window.webContents.on(eventName,(event,url)=>{if(!trustedNavigation(url,origin))event.preventDefault();});
  window.webContents.on('will-attach-webview',event=>event.preventDefault());
  await window.loadFile(path.join(__dirname,'assets/loading.html'));window.show();
  if(config.mode==='ecs'&&!fs.existsSync(accessFile())){connectionSettings();return;}
  try {
    if(config.mode!=='remote'){
      const result=await startBackend();origin=result.origin;
      localUsers=result.users;
      for(const url of localUsers.length?localUsers.map(u=>u.url):[origin])await isolated.cookies.set({url,name:'sidecar_desktop_session',value:result.token,httpOnly:true,sameSite:'strict',path:'/'});
      createMenu();
    }else origin=config.url;
    await window.loadURL(origin+(config.mode==='ecs'?'/ui?desktop=1&toy=1&online=1':localUsers.length?'/ui?desktop=1&toy=1':'/ui?desktop=1'));
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
    ipcMain.handle('connection:read',event=>{settingsSender(event);return {...config,ecsConfigured:fs.existsSync(accessFile())};});
    ipcMain.handle('connection:import',async event=>{
      settingsSender(event);
      const selected=await dialog.showOpenDialog(settingsWindow,{properties:['openFile'],filters:[{name:'ECS 接入文件',extensions:['json']}]});
      if(selected.canceled)return {configured:fs.existsSync(accessFile())};
      const filename=selected.filePaths[0];
      if(fs.statSync(filename).size>32768)throw new Error('接入文件过大');
      const access=JSON.parse(fs.readFileSync(filename,'utf8'));
      const expected=['schema','relay_url','relay_token','ca_pem','experiment_id'].sort();
      if(JSON.stringify(Object.keys(access).sort())!==JSON.stringify(expected)||access.schema!=='sidecar-ecs-access/1')throw new Error('不是有效的 ECS 接入文件');
      const relayURL=new URL(access.relay_url);
      if(relayURL.protocol!=='https:'||relayURL.username||relayURL.password||relayURL.search||relayURL.hash||relayURL.pathname!=='/'||!relayURL.port)throw new Error('接入文件必须使用带端口的 HTTPS 服务地址');
      if(typeof access.relay_token!=='string'||access.relay_token.length<32||access.relay_token.length>4096||typeof access.experiment_id!=='string'||!access.experiment_id||access.experiment_id.length>100)throw new Error('接入字段无效');
      const certificate=new (require('node:crypto').X509Certificate)(access.ca_pem);
      if(!certificate.ca||Date.parse(certificate.validTo)<=Date.now())throw new Error('接入证书无效或已过期');
      const answer=await dialog.showMessageBox(settingsWindow,{type:'question',message:'信任此 ECS 接入配置？',detail:access.relay_url+'\nCA SHA-256: '+certificate.fingerprint256,buttons:['取消','导入'],defaultId:0,cancelId:0});
      if(answer.response!==1)return {configured:fs.existsSync(accessFile())};
      fs.mkdirSync(path.dirname(accessFile()),{recursive:true,mode:0o700});
      fs.writeFileSync(accessFile()+'.tmp',JSON.stringify(access),{mode:0o600});fs.renameSync(accessFile()+'.tmp',accessFile());
      return {configured:true};
    });
    ipcMain.handle('connection:save',async(event,value)=>{
      settingsSender(event);const next=validateConnection(value);
      if(next.mode==='ecs'&&!fs.existsSync(accessFile()))throw new Error('请先导入管理员提供的 ECS 接入文件');
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
