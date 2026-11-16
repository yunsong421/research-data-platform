"""Runtime files stay in the private data directory."""
import os
from pathlib import Path
import secrets

DATA_DIR = Path(os.environ.get('DATA_DIR', Path(__file__).resolve().parent / 'data')).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)


def session_secret():
    configured = os.environ.get('APP_SECRET_KEY')
    if configured:
        return configured
    path = DATA_DIR / '.session-secret'
    try:
        with path.open('x', encoding='ascii') as stream:
            stream.write(secrets.token_hex(32))
        path.chmod(0o600)
    except FileExistsError:
        pass
    return path.read_text(encoding='ascii').strip()
