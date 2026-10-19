"""Create private, local configuration. Existing files are never overwritten."""
import argparse
import base64
import os
from pathlib import Path
import secrets

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--runtime-owner',help='Linux container user and group, for example 1000:1000. Only the application directory and its fallback key are affected.')
options=parser.parse_args()
owner=None
if options.runtime_owner:
    try:
        owner=tuple(map(int,options.runtime_owner.split(':')))
        if len(owner)!=2 or min(owner)<0: raise ValueError
    except ValueError:
        parser.error('--runtime-owner must have the form UID:GID.')
    if not hasattr(os,'chown') or os.geteuid()!=0:
        parser.error('--runtime-owner requires the Linux bootstrap container to run as root.')
root=Path(__file__).resolve().parent.parent
runtime=root/'runtime'
if runtime.is_symlink():
    raise SystemExit('The runtime directory must not be a symbolic link.')
runtime.mkdir(exist_ok=True,mode=0o700)
if owner:
    os.chown(runtime,*owner)
    os.chmod(runtime,0o700)
    key=runtime/'secret.key'
    if key.is_symlink():
        raise SystemExit('The runtime secret key must not be a symbolic link.')
    if key.exists():
        os.chown(key,*owner)
        os.chmod(key,0o600)
# Do not recursively change ownership: runtime/pgdata belongs to PostgreSQL.
path=root/'.env'
if path.exists():
    print('Съществуващият .env е запазен.')
else:
    password=secrets.token_urlsafe(32)
    content=f'''POSTGRES_PASSWORD={password}
DATABASE_URL=postgresql://pfplus:{password}@127.0.0.1:5432/pfplus
DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}
TOKEN_ENCRYPTION_KEY={base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}
ALLOWED_HOSTS=localhost,127.0.0.1,testserver
DEBUG=0
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/google/callback/
PRIVACY_NOTICE=
'''
    with path.open('x') as f:
        os.chmod(path,0o600); f.write(content)
    print('Създадена е частна локална конфигурация .env.')
