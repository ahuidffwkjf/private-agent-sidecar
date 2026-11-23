// Finder metadata on generated .app/framework folders is not valid signed data.
// Only remove these two artifact attributes; never touch quarantine or OS policy.
const path=require('node:path');
const {spawnSync}=require('node:child_process');
module.exports=async context=>{
  if(process.platform!=='darwin')return;
  const app=path.join(context.appOutDir,context.packager.appInfo.productFilename+'.app');
  for(const attribute of ['com.apple.FinderInfo','com.apple.ResourceFork']){
    const result=spawnSync('/usr/bin/xattr',['-dr',attribute,app],{encoding:'utf8'});
    if(result.error)throw result.error;
    if(result.status!==0&&!String(result.stderr).split('\n').filter(Boolean).every(line=>line.includes('No such xattr')))throw new Error('Could not clear generated signing metadata: '+attribute);
  }
};
