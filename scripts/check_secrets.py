"""Scan tracked/staged file contents without printing matched secrets. Requires Git."""
import re
import subprocess
import sys
patterns = [r'AIza[0-9A-Za-z_-]{30,}', r'gh[pousr]_[A-Za-z0-9]{20,}', r'github_pat_[A-Za-z0-9_]{20,}', r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', r'(?i)(?:api[_-]?key|password|token|secret)\s*[=:]\s*[\"\x27]?(?!CHANGE_ME|REPLACE_|YOUR_|\$|\{|\"|\x27|$)[A-Za-z0-9+/_.-]{20,}']
def main():
    files = subprocess.check_output(['git', 'ls-files', '-z']).decode().split('\0')
    failures = []
    for name in filter(None, files):
        if name in {'.env.example', 'scripts/check_secrets.py'}: continue
        if name == '.env' or name.startswith('data/') or name.endswith(('.pem', '.key', '.dump')):
            failures.append(name); continue
        try: data = subprocess.check_output(['git', 'show', ':'+name], stderr=subprocess.DEVNULL).decode('utf-8')
        except (subprocess.CalledProcessError, UnicodeDecodeError): continue
        if any(re.search(p, data) for p in patterns): failures.append(name)
    for name in failures: print(f'Possible secret or private data in tracked file: {name}')
    print(f'Scanned tracked index: {len(failures)} flagged files. Manual review still required.')
    return bool(failures)
if __name__ == '__main__': sys.exit(main())
