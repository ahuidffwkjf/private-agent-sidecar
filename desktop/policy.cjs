const path=require('node:path');
const {fileURLToPath}=require('node:url');
function validateConnection(value){
  if(value?.mode==='ecs')return {mode:'ecs'};
  if(value?.mode==='local')return {mode:'local'};
  if(value?.mode!=='remote'||typeof value.url!=='string')throw new Error('请选择本机工作区或现有 Sidecar 服务');
  let url;try{url=new URL(value.url);}catch{throw new Error('请输入完整的 Sidecar 地址');}
  if(url.username||url.password||url.search||url.hash)throw new Error('地址不能包含密码、查询参数或片段');
  const loopback=['127.0.0.1','localhost','[::1]'].includes(url.hostname);
  if(url.protocol!=='https:'&&!(url.protocol==='http:'&&loopback))throw new Error('远程服务必须使用 HTTPS；HTTP 仅允许本机回环地址');
  if(url.pathname!=='/'&&url.pathname!=='/ui')throw new Error('请输入服务根地址，不含路径前缀');
  return {mode:'remote',url:url.origin};
}
function trustedNavigation(candidate,base){
  try{const a=new URL(candidate),b=new URL(base);return a.origin===b.origin&&['http:','https:'].includes(a.protocol)&&!a.username&&!a.password;}catch{return false;}
}
function trustedLocalFileNavigation(candidate,expected){
  try{
    const actual=new URL(candidate),target=new URL(expected);
    if(actual.protocol!=='file:'||target.protocol!=='file:'||actual.host||target.host||actual.search||actual.hash)return false;
    // Node and Chromium encode unreserved characters such as '~' differently.
    return fileURLToPath(actual)===fileURLToPath(target);
  }catch{return false;}
}
function backendCommand({packaged,resources,root,platform=process.platform}){
  if(packaged)return {file:path.join(resources,'backend',platform==='win32'?'SidecarBackend.exe':'SidecarBackend'),args:[]};
  return {file:path.join(root,'desktop','.venv',platform==='win32'?'Scripts/python.exe':'bin/python'),args:['-m','agent_sidecar.desktop']};
}
function validateReady(value){
  function localOrigin(raw){
    const url=new URL(raw);
    if(url.protocol!=='http:'||url.hostname!=='127.0.0.1'||url.username||url.password||url.pathname!=='/'||url.search||url.hash||!url.port)throw new Error('服务启动地址无效');
    return url.origin;
  }
  const origin=localOrigin(value.url);
  const users=value.users||[];
  if(!Array.isArray(users)||users.length>4)throw new Error('演示用户列表无效');
  const names=new Set(),origins=new Set();
  const parsed=users.map(user=>{
    if(!['Alice','Bob','Carol','Dave'].includes(user.name)||names.has(user.name))throw new Error('演示用户身份无效');
    const url=localOrigin(user.url);names.add(user.name);
    if(origins.has(url))throw new Error('演示用户地址重复');
    origins.add(url);return {name:user.name,url};
  });
  if(parsed.length&&(!origins.has(origin)||parsed.length!==4))throw new Error('演示用户列表不完整');
  return {origin,users:parsed};
}
module.exports={validateConnection,trustedNavigation,trustedLocalFileNavigation,backendCommand,validateReady};
