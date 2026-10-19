#!/usr/bin/env python3
"""Use the built application image's LibreOffice for local Python development."""
import os
from pathlib import Path
import subprocess
import sys

args=sys.argv[1:]
mounts={str(Path(args[-1]).resolve().parent)}
if '--outdir' in args: mounts.add(str(Path(args[args.index('--outdir')+1]).resolve()))
cmd=['docker','run','--rm','--network','none','--user',f'{os.getuid()}:{os.getgid()}']
for path in mounts: cmd+=['--volume',path+':'+path]
cmd+=['--entrypoint','libreoffice','prostranstvoto-web',*args]
raise SystemExit(subprocess.call(cmd))
