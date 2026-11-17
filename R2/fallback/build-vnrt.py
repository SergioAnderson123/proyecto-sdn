"""Compila con las API ONOS instaladas y conserva intacto el OAR original."""
from pathlib import Path
import io
import subprocess
import sys
import re
import zipfile

repo=Path(__file__).resolve().parents[2]
source=repo/'R2/fallback/src/main/java/pe/edu/sdn/mab/FallbackMab.java'
original=repo/'R2/fallback/fallback-mab-1.0.0.oar'
version=sys.argv[1] if len(sys.argv)>1 else '1.0.3'
if not re.fullmatch(r'1\.0\.[123]',version):raise SystemExit('Unexpected candidate version')
output=repo/('R2/fallback/fallback-mab-vnrt-'+version+'.oar')
if output.exists():raise SystemExit('Candidate OAR already exists; inspect before rebuild')
subprocess.run(['docker','exec','onos','mkdir','-p','/tmp/sdn-mab-build'],check=True,stdin=subprocess.DEVNULL)
subprocess.run(['docker','cp',str(source),'onos:/tmp/sdn-mab-build/FallbackMab.java'],check=True,stdin=subprocess.DEVNULL)
script='set -e; cp=$(find /root/onos -name "*.jar" -printf "%p:" ); javac -proc:none --release 11 -cp "$cp" -d /tmp/sdn-mab-build /tmp/sdn-mab-build/FallbackMab.java'
p=subprocess.run(['docker','exec','onos','sh','-c',script],capture_output=True,text=True,stdin=subprocess.DEVNULL)
if p.returncode:
    print(p.stderr[:3000]);raise SystemExit('Compilation failed; no application installed')
subprocess.run(['docker','cp','onos:/tmp/sdn-mab-build/pe/edu/sdn/mab/FallbackMab.class','/tmp/sdn-mab-candidate.class'],check=True,stdin=subprocess.DEVNULL)
clazz=Path('/tmp/sdn-mab-candidate.class').read_bytes()
with zipfile.ZipFile(original) as old,zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as new:
    jarpath=next(x for x in old.namelist() if x.endswith('.jar'))
    patched=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(old.read(jarpath))) as jar,zipfile.ZipFile(patched,'w',zipfile.ZIP_DEFLATED) as target:
        for info in jar.infolist():
            data=jar.read(info.filename)
            if info.filename=='pe/edu/sdn/mab/FallbackMab.class':data=clazz
            elif info.filename=='META-INF/MANIFEST.MF':data=data.replace(b'Bundle-Version: 1.0.0',('Bundle-Version: '+version).encode())
            target.writestr(info,data)
    for info in old.infolist():
        data=patched.getvalue() if info.filename==jarpath else old.read(info.filename)
        path=info.filename.replace('1.0.0',version)
        if info.filename.endswith('.xml'):data=data.replace(b'1.0.0',version.encode())
        new.writestr(path,data)
print('COMPILED_OAR',output,'bytes',output.stat().st_size)
print('ORIGINAL_OAR_UNCHANGED',original)
