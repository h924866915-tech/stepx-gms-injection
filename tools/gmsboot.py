"""Run the StepX GMS system_server injector from the openclaw container as root."""
import base64
import json
import os
import sys

import gw
import rootrun

HANDOFF = os.environ.get("STEPX_HANDOFF", r"..\..\handoff\google-play-stepx\module")
STAGE = "/tmp/stepx-gms"

RUN = r'''
import os, sys, ctypes, hashlib, json, base64, time, signal, struct, mmap, fcntl
STATE = '/var/lib/stepx-gms'
os.makedirs(STATE, mode=0o700, exist_ok=True)
root_path = None
for q in os.listdir('/proc'):
    if not q.isdigit():
        continue
    p = '/proc/%s/root' % q
    try:
        if (os.path.isfile(p + '/init.environ.rc')
                and os.path.isdir(p + '/system/priv-app')
                and os.path.isfile(p + '/system/build.prop')):
            root_path = p
            break
    except OSError:
        pass
assert root_path, 'no android root anchor'
print('ANCHOR', root_path, flush=True)
rootfd = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY)
lib = ctypes.CDLL(None, use_errno=True)
out = {}
src = open('/tmp/stepx-gms/boot.py').read()
exec(compile(src, 'boot.py', 'exec'), globals())
print('DONE', flush=True)
print(json.dumps(out)[:4000], flush=True)
'''


def stage():
    files = {}
    for name in ("boot.py", "inject.py"):
        with open(os.path.join(HANDOFF, name), "rb") as fh:
            files[name] = base64.b64encode(fh.read()).decode()
    files["run.py"] = base64.b64encode(RUN.encode()).decode()
    remote = (
        "import os,base64;d=" + repr(files) + ";"
        "os.makedirs('" + STAGE + "',exist_ok=True)\n"
        "for n,b in d.items():\n"
        " with open('" + STAGE + "/'+n,'wb') as f: f.write(base64.b64decode(b))\n"
        "print('staged', sorted(d))\n"
    )
    status, raw = gw.run(["python3", "-c", remote], timeout_ms=30000)
    print("stage:", status, str(raw)[:300])


def main():
    stage()
    cmd = (
        "mkdir -p /usr/local/lib/stepx-gms && "
        "cp /tmp/stepx-gms/inject.py /usr/local/lib/stepx-gms/inject.py && "
        "chmod 600 /usr/local/lib/stepx-gms/inject.py; "
        "rm -f /var/lib/stepx-gms/last.json /var/lib/stepx-gms/lock; "
        "mkdir -p /var/lib/stepx-gms && chmod 700 /var/lib/stepx-gms; "
        "cd /tmp/stepx-gms && python3 run.py > /tmp/stepx-gms/out.txt 2>&1; "
        "echo RC=$? >> /tmp/stepx-gms/out.txt; "
        "chmod -R a+rX /tmp/stepx-gms /var/lib/stepx-gms; true"
    )
    status, raw = rootrun.run_root(cmd, timeout=120)
    print("root:", status)
    print(str(raw)[-1500:])
    print("=== out.txt ===")
    print(gw.run(["cat", "/tmp/stepx-gms/out.txt"])[1][-5000:])
    print("=== last.json ===")
    print(gw.run(["cat", "/var/lib/stepx-gms/last.json"])[1][-3000:])


if __name__ == "__main__":
    main()

