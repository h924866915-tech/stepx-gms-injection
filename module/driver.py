"""定位真实 Android 根并执行 boot.py 的注入流程。

与原厂 launcher.py 的区别：不依赖 os.getppid()，直接在 /proc/*/root 里按特征文件找锚点，
这样无论从哪条链启动都能跑。
"""
import os, ctypes, hashlib, json, base64, time, signal, struct, mmap, fcntl  # noqa: F401

STATE = '/var/lib/stepx-gms'
os.makedirs(STATE, mode=0o700, exist_ok=True)

root_path = None
for q in os.listdir('/proc'):
    if not q.isdigit():
        continue
    p = '/proc/%s/root' % q
    try:
        # openclaw_env 的 root 就是真实 Android 根
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
