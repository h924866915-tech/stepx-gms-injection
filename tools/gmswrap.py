"""Install the StepX GMS boot wrapper around /usr/local/bin/ubuntu_zygote."""
import base64
import os

import gw
import rootrun

HANDOFF = os.environ.get("STEPX_HANDOFF", r"..\..\handoff\google-play-stepx\module")
STAGE = "/tmp/stepx-gms"

DRIVER = r'''
import os, ctypes, hashlib, json, base64, time, signal, struct, mmap, fcntl
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
open('/tmp/stepx-gms/driver.log', 'a').write('anchor %s\n' % root_path)
rootfd = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY)
lib = ctypes.CDLL(None, use_errno=True)
out = {}
src = open('/usr/local/lib/stepx-gms/boot.py').read()
exec(compile(src, 'boot.py', 'exec'), globals())
'''

REPAIR = '''#!/bin/sh
sleep 8
exec python3 /usr/local/lib/stepx-gms/driver.py
'''

WRAPPER = '''#!/bin/sh
# StepX GMS repair hook: the factory zygote always starts first and unchanged.
/usr/local/lib/stepx-gms/repair.sh >/tmp/stepx-gms/boot.log 2>&1 </dev/null &
exec /usr/local/bin/ubuntu_zygote.real "$@"
'''


def stage():
    files = {}
    for name in ("boot.py", "inject.py"):
        with open(os.path.join(HANDOFF, name), "rb") as fh:
            files[name] = base64.b64encode(fh.read()).decode()
    files["driver.py"] = base64.b64encode(DRIVER.encode()).decode()
    files["repair.sh"] = base64.b64encode(REPAIR.encode()).decode()
    files["wrapper"] = base64.b64encode(WRAPPER.encode()).decode()
    remote = (
        "import os,base64;d=" + repr(files) + ";"
        "os.makedirs('" + STAGE + "',exist_ok=True)\n"
        "for n,b in d.items():\n"
        " with open('" + STAGE + "/'+n,'wb') as f: f.write(base64.b64decode(b))\n"
        "print('staged', sorted(d))\n"
    )
    print("stage:", gw.run(["python3", "-c", remote], timeout_ms=30000)[0])


def install():
    sh = f'''
set -e
cp -a /usr/local/bin/ubuntu_zygote /usr/local/bin/ubuntu_zygote.real
cp -a /usr/local/bin/ubuntu_zygote /usr/local/bin/ubuntu_zygote.stepx-stock-20261021
mkdir -p /usr/local/lib/stepx-gms
cp {STAGE}/boot.py {STAGE}/inject.py {STAGE}/driver.py /usr/local/lib/stepx-gms/
cp {STAGE}/repair.sh /usr/local/lib/stepx-gms/repair.sh
chmod 700 /usr/local/lib/stepx-gms
chmod 600 /usr/local/lib/stepx-gms/*.py
chmod 700 /usr/local/lib/stepx-gms/repair.sh
cp {STAGE}/wrapper /usr/local/bin/ubuntu_zygote.new
chmod 755 /usr/local/bin/ubuntu_zygote.new
mv /usr/local/bin/ubuntu_zygote.new /usr/local/bin/ubuntu_zygote
ls -l /usr/local/bin/ubuntu_zygote /usr/local/bin/ubuntu_zygote.real
ls -l /usr/local/lib/stepx-gms/
sh -n /usr/local/bin/ubuntu_zygote && echo WRAPPER_SYNTAX_OK
'''
    status, raw = rootrun.run_root(
        "{ " + sh + " } > /tmp/stepx-gms/install.log 2>&1; chmod 644 /tmp/stepx-gms/install.log; true",
        timeout=60)
    print("root:", status)
    print(gw.run(["cat", "/tmp/stepx-gms/install.log"])[1][-3000:])


if __name__ == "__main__":
    stage()
    install()

