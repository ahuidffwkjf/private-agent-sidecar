// Use macOS' built-in image creator; no extra downloaded DMG tool is necessary.
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {spawnSync}=require('node:child_process');
if(process.platform==='darwin'){
  const root=path.resolve(__dirname,'..');
  const {version,productName}=require('../package.json');
  const folder=process.arch==='arm64'?'mac-arm64':'mac';
  const release=process.env.SIDECAR_DESKTOP_RELEASE?path.resolve(process.env.SIDECAR_DESKTOP_RELEASE):path.join(root,'release');
  const source=path.join(release,folder,productName+'.app');
  const destination=path.join(release,`Sidecar-Toy-${version}-mac-${process.arch}.dmg`);
  if(!fs.existsSync(source))throw new Error('Build the current architecture application before creating its DMG');
  const staging=fs.mkdtempSync(path.join(os.tmpdir(),'sidecar-dmg-'));
  try{
    fs.cpSync(source,path.join(staging,productName+'.app'),{recursive:true,dereference:false,verbatimSymlinks:true});
    fs.symlinkSync('/Applications',path.join(staging,'Applications'));
    const result=spawnSync('/usr/bin/hdiutil',['create','-volname',productName,'-srcfolder',staging,'-format','UDZO','-ov',destination],{stdio:'inherit',timeout:180000});
    if(result.error)throw result.error;
    if(result.status!==0)throw new Error(`hdiutil failed (${result.status})`);
    const verify=spawnSync('/usr/bin/hdiutil',['verify',destination],{stdio:'inherit',timeout:60000});
    if(verify.error)throw verify.error;
    if(verify.status!==0)throw new Error('DMG integrity verification failed');
  }finally{fs.rmSync(staging,{recursive:true,force:true});}
}
