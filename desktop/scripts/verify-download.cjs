// Verify archive extraction and actual installed binaries on the native runner.
const {spawnSync}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const metadata=require('../package.json');
const release=path.resolve(__dirname,'../release');
const smoke=path.resolve(__dirname,'toy-smoke.cjs');
function run(command,args,options={}){
  const result=spawnSync(command,args,{stdio:'inherit',timeout:240000,...options});
  if(result.error)throw result.error;
  if(result.status!==0)throw Error(`${path.basename(command)} failed (${result.status})`);
}
function asset(extension){
  const names=fs.readdirSync(release).filter(n=>n.startsWith(`Sidecar-Toy-${metadata.version}-`)&&n.endsWith(extension));
  if(names.length!==1)throw Error(`Expected one ${extension} artifact, got ${names.length}`);
  return path.join(release,names[0]);
}
function check(executable,reportRoot){
  fs.mkdirSync(reportRoot,{recursive:true});
  run(process.execPath,[smoke],{env:{...process.env,SIDECAR_DESKTOP_EXECUTABLE:executable,SIDECAR_DESKTOP_RELEASE:reportRoot}});
}
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'sidecar-download-'));
const checks=[];
try{
  if(process.platform==='darwin'){
    const unpack=path.join(temporary,'zip');
    run('/usr/bin/ditto',['-x','-k',asset('.zip'),unpack]);
    const app=path.join(unpack,metadata.productName+'.app');
    run('/usr/bin/codesign',['--verify','--deep','--strict',app]);
    check(path.join(app,'Contents/MacOS',metadata.productName),path.join(release,'zip-check'));
    checks.push('zip-extraction-signature-native-flow');
    run('/usr/bin/hdiutil',['verify',asset('.dmg')]);
    const mount=path.join(temporary,'mounted');fs.mkdirSync(mount);
    run('/usr/bin/hdiutil',['attach','-readonly','-nobrowse','-mountpoint',mount,asset('.dmg')]);
    try{
      const installed=path.join(temporary,'installed',metadata.productName+'.app');
      run('/usr/bin/ditto',[path.join(mount,metadata.productName+'.app'),installed]);
      run('/usr/bin/codesign',['--verify','--deep','--strict',installed]);
      check(path.join(installed,'Contents/MacOS',metadata.productName),path.join(release,'dmg-check'));
      checks.push('dmg-mounted-copy-native-flow');
    }finally{run('/usr/bin/hdiutil',['detach',mount]);}
  }else if(process.platform==='win32'){
    const zip=asset('.zip'),unpack=path.join(temporary,'zip');
    run('powershell.exe',['-NoProfile','-NonInteractive','-Command','Expand-Archive -LiteralPath $env:SIDECAR_ZIP -DestinationPath $env:SIDECAR_UNPACK'],{env:{...process.env,SIDECAR_ZIP:zip,SIDECAR_UNPACK:unpack}});
    check(path.join(unpack,metadata.productName+'.exe'),path.join(release,'zip-check'));
    checks.push('zip-extraction-native-flow');
    const installed=path.join(temporary,'installed');
    run(asset('.exe'),['/S',`/D=${installed}`]);
    check(path.join(installed,metadata.productName+'.exe'),path.join(release,'installer-check'));
    checks.push('nsis-install-native-flow');
    const uninstaller=fs.readdirSync(installed).find(n=>/^Uninstall.*\.exe$/i.test(n));
    if(!uninstaller)throw Error('Missing NSIS uninstaller');
    run(path.join(installed,uninstaller),['/S']);
  }else if(process.platform==='linux'){
    const image=asset('.AppImage');fs.chmodSync(image,0o755);
    run(image,['--appimage-extract'],{cwd:temporary,stdio:'ignore'});
    const unpack=path.join(temporary,'squashfs-root');
    run('sudo',['chown','root:root',path.join(unpack,'chrome-sandbox')]);
    run('sudo',['chmod','4755',path.join(unpack,'chrome-sandbox')]);
    check(path.join(unpack,metadata.name),path.join(release,'appimage-check'));
    checks.push('appimage-extracted-native-flow');
    const deb=asset('.deb');
    run('sudo',['apt-get','install','-y',deb]);
    const info=spawnSync('dpkg-deb',['-f',deb,'Package'],{encoding:'utf8'});
    if(info.status!==0)throw Error('Could not read Debian package name');
    const listing=spawnSync('dpkg',['-L',info.stdout.trim()],{encoding:'utf8'});
    const executable=listing.stdout.split('\n').find(n=>n.startsWith('/opt/')&&path.basename(n)===metadata.name);
    if(!executable)throw Error('Installed Linux executable not found');
    run('sudo',['chown','root:root',path.join(path.dirname(executable),'chrome-sandbox')]);
    run('sudo',['chmod','4755',path.join(path.dirname(executable),'chrome-sandbox')]);
    check(executable,path.join(release,'deb-check'));
    checks.push('deb-installed-native-flow');
  }else throw Error('Unsupported build platform');
  fs.writeFileSync(path.join(release,'download-validation.json'),JSON.stringify({platform:process.platform,arch:process.arch,version:metadata.version,status:'passed',checks},null,2)+'\n');
}finally{
  // Only this script's fresh temporary staging/installation is cleaned.
  try{fs.rmSync(temporary,{recursive:true,force:true,maxRetries:10,retryDelay:500});}catch(error){console.warn('Temporary installer files remained locked:',error.code);}
}
