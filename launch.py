"""Interactive local launcher. Data and accounts remain in DATA_DIR."""
import argparse
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent


def available_port(host, preferred):
    for port in [preferred, *range(preferred + 1, min(preferred + 21, 65536))]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError('No free port found. Set APP_PORT to an available port.')


def prepare_runtime():
    executable = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not executable.exists():
        print('Creating a local Python environment...', flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], check=True)
    requirements = ROOT / 'requirements.txt'
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = ROOT / '.venv' / '.requirements-sha256'
    if not marker.exists() or marker.read_text().strip() != fingerprint:
        print('Installing dependencies (Internet access is needed on first launch)...', flush=True)
        subprocess.run([str(executable), '-m', 'pip', 'install', '-r', str(requirements)], check=True)
        marker.write_text(fingerprint, encoding='ascii')
    return executable


def open_when_ready(url, stop):
    deadline = time.monotonic() + 60
    while not stop.is_set() and time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + '/login', timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url + '/visualizations')
                    return
        except (OSError, ValueError):
            pass
        stop.wait(.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-browser', action='store_true', help='Do not open a browser tab')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise RuntimeError('Python 3.11 or newer is required; Python 3.11 is recommended.')
    os.chdir(ROOT)
    executable = prepare_runtime()
    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'
    env.setdefault('DATA_DIR', str(ROOT / 'data'))
    # Both account setup and the server use the same interpreter and data directory.
    check = subprocess.run([str(executable), '-c',
                            'from models import init_db,get_db,User; init_db(); '
                            'db=get_db(); count=db.query(User).count(); db.remove(); '
                            'raise SystemExit(0 if count else 3)'], env=env)
    if check.returncode == 3:
        print('First launch: choose your administrator username and a password of at least 12 characters.', flush=True)
        subprocess.run([str(executable), str(ROOT / 'init_admin.py')], env=env, check=True)
    elif check.returncode:
        raise RuntimeError('Unable to open the data directory. See the error above.')
    host = env.get('APP_HOST', '127.0.0.1')
    preferred = int(env.get('APP_PORT', '5000'))
    if not 1 <= preferred <= 65535:
        raise RuntimeError('APP_PORT must be between 1 and 65535.')
    port = available_port(host, preferred)
    env['APP_PORT'] = str(port)
    browser_host = '127.0.0.1' if host in ('0.0.0.0', 'localhost') else host
    url = f'http://{browser_host}:{port}'
    print(f'\nOpen: {url}\nKeep this window open. Press Ctrl+C to stop.\n', flush=True)
    stop = threading.Event()
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url, stop), daemon=True).start()
    try:
        subprocess.run([str(executable), str(ROOT / 'wsgi.py')], env=env, check=True)
    finally:
        stop.set()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nServer stopped.')
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'\nStartup failed: {error}\nSee README.md for manual startup.', file=sys.stderr)
        raise SystemExit(1)
