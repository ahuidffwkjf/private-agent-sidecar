const path=require('node:path');
function validateConnection(value){
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
function backendCommand({packaged,resources,root,platform=process.platform}){
  if(packaged)return {file:path.join(resources,'backend',platform==='win32'?'SidecarBackend.exe':'SidecarBackend'),args:[]};
  return {file:path.join(root,'desktop','.venv',platform==='win32'?'Scripts/python.exe':'bin/python'),args:['-m','agent_sidecar.desktop']};
}
module.exports={validateConnection,trustedNavigation,backendCommand};
