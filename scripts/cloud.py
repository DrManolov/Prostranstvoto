#!/usr/bin/env python3
"""Build/run in cloud machines with a provided egress proxy; preserve TLS verification."""
import os
from pathlib import Path
import socket
import subprocess
import sys
from urllib.parse import urlsplit

root=Path(__file__).resolve().parent.parent
env=os.environ.copy()
env['DOCKER_CONFIG']=str(root/'runtime'/'docker')
proxy=urlsplit(env.get('HTTPS_PROXY',env.get('https_proxy','')))
if proxy.hostname:
    env['PFPLUS_PROXY_HOST']=proxy.hostname
    env['PFPLUS_PROXY_IP']=socket.gethostbyname(proxy.hostname)
command=sys.argv[1] if len(sys.argv)>1 else 'up'
if command=='build':
    args=['docker','build','--network','host']
    if proxy.hostname: args+=['--add-host',proxy.hostname+':'+env['PFPLUS_PROXY_IP']]
    for var in ['HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy']: args+=['--build-arg',var]
    cert=env.get('CODEX_PROXY_CERT') or env.get('SSL_CERT_FILE')
    if cert: args+=['--secret','id=proxy_ca,src='+cert]
    args+=['-t','prostranstvoto-web','.']
elif command=='up':
    args=['docker','compose','-f','compose.yaml']
    if proxy.hostname: args+=['-f','compose.cloud.yaml']
    args+=['up','-d','--wait','--wait-timeout','120','--no-build']
else:
    raise SystemExit('Употреба: python scripts/cloud.py build | up')
raise SystemExit(subprocess.call(args,cwd=root,env=env))
