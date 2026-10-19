"""Isolated browser workflow. Never inserts synthetic records into the real database."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit,urlunsplit
from urllib.request import urlopen
import uuid

root=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(root)); os.environ.setdefault('DJANGO_SETTINGS_MODULE','pfplus.settings')
import django
django.setup()
import psycopg
from psycopg import sql
from django.conf import settings

config=settings.DATABASES['default']
if config['ENGINE']!='django.db.backends.postgresql': raise SystemExit('Smoke тестът изисква PostgreSQL.')
name='pfplus_smoke_'+uuid.uuid4().hex[:12]
conn=psycopg.connect(host=config['HOST'],port=config['PORT'],user=config['USER'],password=config['PASSWORD'],dbname=config['NAME'],autocommit=True)
server=None
with tempfile.TemporaryDirectory(prefix='smoke-',dir=root/'runtime') as folder:
    folder=Path(folder)
    conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    env=os.environ.copy()
    parts=urlsplit(os.environ.get('DATABASE_URL') or __import__('dotenv').dotenv_values(root/'.env')['DATABASE_URL'])
    env['DATABASE_URL']=urlunsplit((parts.scheme,parts.netloc,'/'+name,parts.query,parts.fragment))
    env['PFPLUS_RUNTIME']=str(folder)
    env['SOFFICE']=str(root/'scripts'/'soffice-docker.py')
    username='smoke-admin'; password=secrets.token_urlsafe(32)
    credentials=folder/'login.json'; credentials.write_text(json.dumps({'username':username,'password':password})); os.chmod(credentials,0o600)
    env['PFPLUS_SMOKE_CREDENTIALS']=str(credentials)
    try:
        def manage(*args): subprocess.run([sys.executable,'manage.py',*args],cwd=root,env=env,check=True,stdout=subprocess.DEVNULL)
        manage('migrate','--noinput'); manage('import_history'); manage('init_roles'); manage('collectstatic','--noinput')
        setup='''import json,os
from django.contrib.auth import get_user_model
from ledger.models import FinancialRule,Official
c=json.load(open(os.environ['PFPLUS_SMOKE_CREDENTIALS']))
get_user_model().objects.create_superuser(c['username'],'',c['password'])
FinancialRule.objects.update(confirmed=True,accounting_reference='ИЗОЛИРАН ТЕСТ')
Official.objects.update(confirmed=True,accounting_reference='ИЗОЛИРАН ТЕСТ')
'''
        manage('shell','-c',setup)
        with (folder/'server.log').open('w') as log:
            server=subprocess.Popen([sys.executable,'manage.py','runserver','127.0.0.1:8001','--noreload','--nostatic'],cwd=root,env=env,stdout=log,stderr=log)
            for _ in range(60):
                try:
                    if urlopen('http://127.0.0.1:8001/login/',timeout=1).status==200: break
                except OSError: time.sleep(0.25)
            else: raise RuntimeError('Тестовият сървър не стартира.')
            subprocess.run(['node','scripts/browser_smoke.js'],cwd=root,env=env,check=True)
    finally:
        if server: server.terminate(); server.wait(timeout=10)
        conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        conn.close()
