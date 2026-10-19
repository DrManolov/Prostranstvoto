#!/usr/bin/env python3
"""Consistent PostgreSQL dump plus private documents; secrets must be backed up separately."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

root=Path(__file__).resolve().parent.parent
backups=root/'runtime'/'backups'; backups.mkdir(parents=True,exist_ok=True,mode=0o700)
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
archive=backups/f'pfplus-{stamp}.tar.gz'
with tempfile.TemporaryDirectory(dir=backups) as temp:
    temp=Path(temp); dump=temp/'database.dump'
    with dump.open('wb') as f:
        subprocess.run(['docker','compose','exec','-T','db','pg_dump','-U','pfplus','-d','pfplus','-Fc'],cwd=root,stdout=f,check=True)
    manifest={'database_sha256':hashlib.sha256(dump.read_bytes()).hexdigest(),'created_at_utc':stamp,
              'raw_configuration_keys_included':False,'restore_requires':'Original .env, including TOKEN_ENCRYPTION_KEY; keep it in a separate secure backup.'}
    (temp/'manifest.json').write_text(json.dumps(manifest,indent=2))
    with archive.open('xb') as output:
        os.chmod(archive,0o600)
        with tarfile.open(fileobj=output,mode='w:gz') as tar:
            tar.add(dump,arcname='database.dump'); tar.add(temp/'manifest.json',arcname='manifest.json')
            tar.add(root/'data'/'originals',arcname='originals')
            if (root/'runtime'/'private').exists(): tar.add(root/'runtime'/'private',arcname='private')
print('Архивът е създаден:',archive)
