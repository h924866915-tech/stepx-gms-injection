"""Diagnose which step of boot.py's child fails on this device."""
import base64

import gw
import rootrun

RUN2 = r'''
import os, ctypes
log = open('/tmp/stepx-gms/step.log', 'w', buffering=1)
def L(m):
    log.write(str(m) + '\n')
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
L('anchor=' + str(root_path))
rootfd = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY)
lib = ctypes.CDLL(None, use_errno=True)
L('parent uid=%d euid=%d' % (os.getuid(), os.geteuid()))
L('ctx before=' + open('/proc/self/attr/current').read().strip())
pid = os.fork()
if pid == 0:
    try:
        os.fchdir(rootfd)
        os.chroot('.')
        os.chdir('/')
        L('chroot ok, / has system=%s data=%s' % (os.path.isdir('/system'), os.path.isdir('/data')))
        os.setresgid(1000, 0, 0)
        os.setresuid(1000, 0, 0)
        L('setres ok uid=%d euid=%d' % (os.getuid(), os.geteuid()))
        r = lib.setfsgid(1000)
        L('setfsgid -> %d' % r)
        r = lib.setfsuid(1000)
        L('setfsuid -> %d' % r)
        for c in ('u:r:system_server_startup:s0', 'u:r:system_server:s0'):
            try:
                with open('/proc/self/attr/current', 'w') as s:
                    s.write(c)
                L('ctx now=' + open('/proc/self/attr/current').read().strip())
            except Exception as e:
                L('ctx fail %s -> %r' % (c, e))
        try:
            lib.setfsuid(0)
            L('setfsuid(0) ok')
        except Exception as e:
            L('setfsuid0 fail %r' % e)
        ss = []
        for item in os.listdir('/proc'):
            if item.isdigit():
                try:
                    if open('/proc/' + item + '/comm').read().strip() == 'system_server':
                        ss.append(item)
                except OSError:
                    pass
        L('system_server pids=' + repr(ss))
    except BaseException as e:
        L('child fail %r' % e)
    log.flush()
    os._exit(0)
os.waitpid(pid, 0)
log.close()
try:
    os.chmod('/tmp/stepx-gms/step.log', 0o644)
except OSError:
    pass
'''

STAGE = "/tmp/stepx-gms"


def main():
    payload = base64.b64encode(RUN2.encode()).decode()
    remote = (
        "import base64;open('" + STAGE + "/diag.py','wb').write(base64.b64decode('" + payload + "'));"
        "print('ok')\n"
    )
    print("stage:", gw.run(["python3", "-c", remote], timeout_ms=30000)[0])
    cmd = (
        "cd /tmp/stepx-gms && python3 diag.py; "
        "chmod -R a+rX /tmp/stepx-gms; true"
    )
    print("root:", rootrun.run_root(cmd, timeout=90)[0])
    print(gw.run(["cat", "/tmp/stepx-gms/step.log"])[1][-4000:])


if __name__ == "__main__":
    main()

