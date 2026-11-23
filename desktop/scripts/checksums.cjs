const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const root=process.env.SIDECAR_DESKTOP_RELEASE?path.resolve(process.env.SIDECAR_DESKTOP_RELEASE):path.resolve(__dirname,'../release');
async function main(){
  const {version}=require('../package.json');
  const files=fs.readdirSync(root).filter(name=>name.startsWith(`Sidecar-Toy-${version}-`)&&/\.(dmg|zip|exe|AppImage|deb)$/.test(name)).sort();
  if(!files.length)throw new Error('No desktop installers found');
  const lines=[];
  for(const name of files){
    const hash=crypto.createHash('sha256');
    for await(const chunk of fs.createReadStream(path.join(root,name)))hash.update(chunk);
    lines.push(`${hash.digest('hex')}  ${name}`);
  }
  fs.writeFileSync(path.join(root,'SHA256SUMS'),lines.join('\n')+'\n');
  console.log(`Wrote checksums for ${files.length} desktop artifacts`);
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
