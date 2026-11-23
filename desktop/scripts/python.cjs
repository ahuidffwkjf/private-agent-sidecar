const {spawnSync}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const root=path.resolve(__dirname,'../..');
const desktop=path.join(root,'desktop');
const venv=path.join(desktop,'.venv');
const python=path.join(venv,process.platform==='win32'?'Scripts/python.exe':'bin/python');

function run(command,args){
  const result=spawnSync(command,args,{cwd:root,stdio:'inherit',windowsHide:true});
  if(result.error)throw result.error;
  if(result.status!==0)throw new Error(`${path.basename(command)} exited with ${result.status}`);
}
function findPython(){
  const candidates=process.env.SIDECAR_BUILD_PYTHON?[[process.env.SIDECAR_BUILD_PYTHON,[]]]:
    process.platform==='win32'?[['python',[]],['py',['-3']]]:[['python3',[]],['python',[]]];
  for(const [command,args] of candidates){
    const result=spawnSync(command,[...args,'-c','import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] < (3, 15) else 1)'],{stdio:'ignore',windowsHide:true});
    if(!result.error&&result.status===0)return {command,args};
  }
  throw new Error('Source builds require Python 3.10–3.14. Install Python or use a prebuilt desktop release.');
}
function sourceSnapshot(){
  const files={};
  function scan(directory){
    for(const entry of fs.readdirSync(directory,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))){
      if(entry.name==='__pycache__')continue;
      const filename=path.join(directory,entry.name);
      if(entry.isDirectory())scan(filename);
      else if(entry.isFile()&&!entry.name.endsWith('.pyc'))files[path.relative(root,filename).split(path.sep).join('/')]=crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
    }
  }
  scan(path.join(root,'agent_sidecar'));
  for(const name of ['backend.spec','backend_entry.py','requirements-build.txt'])files['desktop/'+name]=crypto.createHash('sha256').update(fs.readFileSync(path.join(desktop,name))).digest('hex');
  return files;
}
try {
  if(!fs.existsSync(python)){
    const {command,args}=findPython();run(command,[...args,'-m','venv',venv]);
  }
  const requirements=fs.readFileSync(path.join(desktop,'requirements-build.txt'),'utf8');
  const marker=path.join(venv,'sidecar-build-requirements.txt');
  if(!fs.existsSync(marker)||fs.readFileSync(marker,'utf8')!==requirements){
    run(python,['-m','pip','install','-r',path.join(desktop,'requirements-build.txt')]);
    fs.writeFileSync(marker,requirements);
  }
  if(process.argv[2]==='build'){
    const before=sourceSnapshot();
    run(python,['-m','PyInstaller','--noconfirm','--clean',
      '--distpath',path.join(desktop,'backend-dist'),'--workpath',path.join(desktop,'backend-build'),path.join(desktop,'backend.spec')]);
    if(JSON.stringify(before)!==JSON.stringify(sourceSnapshot()))throw new Error('Backend source changed during build. Retry after the workspace is stable; this build must not be distributed.');
    fs.writeFileSync(path.join(desktop,'backend-dist','SidecarBackend','source-manifest.json'),JSON.stringify({platform:process.platform,arch:process.arch,files:before},null,2)+'\n');
  }
  else if(process.argv[2]!=='setup')throw new Error('Expected setup or build');
} catch(e){console.error(e.message);process.exitCode=1;}
