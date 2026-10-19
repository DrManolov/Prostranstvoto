"""Create private, local configuration. Existing files are never overwritten."""
import base64
import os
from pathlib import Path
import secrets

root=Path(__file__).resolve().parent.parent
(root/'runtime').mkdir(exist_ok=True,mode=0o700)
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
