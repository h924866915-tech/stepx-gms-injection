# 在被锁死 BL 的 StepOS 设备上让 Google Play 真正跑起来

> 一份完整的技术复盘：为什么常规方案全部失效，StepX Companion 用什么手法绕过，
> 以及在 STEPX Neo（PZ261A / Android 16 / 未解锁 BL）上的一次完整复现。

---

## 1. 结论先行

Play 商店闪退、GMS 无法工作，根因只有一条：

```
java.lang.SecurityException: You either need MANAGE_USERS or CREATE_USERS permission to: query users
    at com.android.server.pm.UserManagerService.checkCreateUsersPermission
    at com.android.server.pm.UserManagerService.getUsers
    at nfb.j(PG:1013)  →  nfb.onCreate(PG:23)
Caused by: Unable to create application com.google.android.finsky.application.classic.ClassicApplication
```

`MANAGE_USERS` 的 protectionLevel 是 `signature|privileged`。侧载进 `/data/app` 的 Play 商店
拿不到它，于是 `Application.onCreate` 阶段直接死，连界面都加载不出来。

**而 StepX 的解法不是"装 GApps"，是"在运行时把 system_server 里的 PackageManager 状态改掉"。**
不写分区、不动 vbmeta、不重签名、不碰 bootloader。

---

## 2. 为什么所有"正常"办法都走不通

在动手之前，这些路已经逐条撞死在这台机器上，记录如下，省得后来人重复踩：

| 方案 | 实际结果 |
|---|---|
| `pm grant com.android.vending android.permission.MANAGE_USERS` | `SecurityException: ... is not a changeable permission type`（不是运行时权限） |
| `pm grant ... QUERY_USERS` | `... is managed by role` |
| 给全部 32 个未持有 role 逐个 `cmd role add-role-holder` + `set-bypassing-role-qualification true` | 一条 `QUERY_USERS: granted=true` 都没拿到 |
| 用平台签名重签 APK | 平台证书 `CN=StepOS Team, O=Step, L=ShangHai, C=CN` 是自签的，**私钥不可能拿到**；也不是 AOSP testkey |
| 把 APK 塞进 `priv-app` | `/`、`/system_ext`、`/product`、`/vendor`、`/odm` 全是 **erofs 只读**；dm-verity 在链上 |
| 解锁 BL 后 fastboot 刷 `system` | `ro.boot.flash.locked=1`、`verifiedbootstate=green`，所有 `fastboot flashing unlock*` 一律 `unknown command` |
| `adb root` | `ro.debuggable=0`、`ro.secure=1`、`ro.adb.secure=1` |
| DSU 装带 GMS 的 GSI | **装得进去，起不来**（详见 §7） |

所以唯一的出口是：**在进程内存里改状态**。

---

## 3. 上游的实现机制（`handoff/google-play-stepx`）

### 3.1 组件分工

```
runtime/google-play/*.apk          MindTheGapps 16.0.0-arm64 的三个 APK（gsf / gms / vending）
handoff/google-play-stepx/
├── target.example.json            目标描述：APK 路径、sha256、签名证书摘要、libc/libart 指纹
├── module/launcher.py             替换 /usr/local/bin/ubuntu_zygote 的壳
├── module/boot.py                 开机钩子：上下文切换 + 找 system_server + 装载载荷
├── module/inject.py               ptrace 注入器：JNI RPC + 加载内存 DEX
├── java/GmsProbe.java             真正在 system_server 进程里执行的代码
└── tools/*.py                     gateway / root_bridge / install_adapter / health / rollback ...
```

注意 `desktop/src/google/recovery.rs` **不是**安装器，它只负责调度和"修复预算"，真正干活的全在
`module/` 里。

### 3.2 三步走

**第一步：把 GApps 当普通用户应用装进 `/data`。** 这一步本身必然导致 Play 商店崩，是预期内的。

**第二步：拿到一个"干净"的 root 进程。**

StepOS 里跑着一个 OpenClaw Linux 容器，它的启动链是：

```
/system/bin/openclaw_env (uid 0, u:r:zygote:s0, Seccomp 0, CapBnd 0x72c25ff)
        └── /usr/local/bin/ubuntu_zygote → 容器总线
```

`launcher.py` 把自己伪装成 `ubuntu_zygote`：父进程立刻 `execv()` 原厂二进制（**保证系统正常启动**），
fork 出来的子进程留在不受限的上下文里继续干活。

**第三步：把自己变成 system_server，然后 ptrace 它。**

`boot.py` 的子进程：

```python
os.fchdir(rootfd); os.chroot('.'); os.chdir('/')      # 进入真实 Android 根
os.setresgid(1000, 0, 0); os.setresuid(1000, 0, 0)   # euid 仍是 0，权限不丢
lib.setfsgid(1000); lib.setfsuid(1000)
for ctx in ('u:r:system_server_startup:s0', 'u:r:system_server:s0'):
    open('/proc/self/attr/current', 'w').write(ctx)  # ← 关键：直接切 SELinux 域
lib.setfsuid(0)
```

然后在 `/proc/*/comm` 里找到**唯一**的 `system_server`，把 `STEPX_SYSTEM_SERVER_PID` 交给 `inject.py`。

`inject.py` 是整件事的核心，它做的是**进程内 RPC**：

```python
PTRACE_ATTACH → PTRACE_GETREGSET(0x4204) → 保存 x[31]/sp/pc/pstate
open('/proc/<pid>/mem', 'O_RDWR')
call(libc + 0x79170)                 # getpid
call(libc + 0xd77c0, ...)            # mmap 1 MiB arena, PROT 3, flags 0x22, fd -1
call(libart + 0x669a4c, [arena, 1, arena+8])   # JNI_GetCreatedJavaVMs
vm = u64(arena); vmt = u64(vm)
GetEnv = u64(vmt + 6*8)  with version 0x10006
```

拿到 `JNIEnv*` 之后就是一个标准 JNI 调用表：`FindClass(6)`、`GetStaticMethodID(113)`、
`NewStringUTF(167)`、`NewDirectByteBuffer(229)` …，最后：

```java
new InMemoryDexClassLoader(ByteBuffer, threadContextClassLoader)
    .loadClass("stepx.GmsProbe")
    .run()   // → 返回 GRANT_RESULT 文本
```

收尾：`ExceptionClear` → `PopLocalFrame` → 恢复寄存器 → `PTRACE_DETACH`。
那 1 MiB arena 泄漏到重启为止，无所谓。

> ART 在启动期做隐式检查时会主动触发 SIGSEGV。`inject.py` 对此做了处理：如果 fault 地址 < 4096
> 且落在 `boot`/`dalvik` 的可执行映射里，就带 signal 11 重新 `PTRACE_CONT`，最多 8 次。

### 3.3 GmsProbe 在 system_server 里改了什么

`java/GmsProbe.java` 反射走的是 `com.android.server.pm.PackageManagerService` 的内部结构：

```java
mPackages → AndroidPackage
  .setPrivileged(true)
  .setSystem(true)
  mBaseAppInfoFlags |= FLAG_PRIVILEGED
SigningInfo / getApkContentsSigners              // 先校验签名，不符直接 Certificate mismatch
PermissionManagerServiceInterface.grantRuntimePermission(...)
MutableAccessState.onStateMutated()
createAppData() / invalidatePackageInfoCache() / forceStopPackage()
ServiceManager.getService("permissionmgr")
```

然后逐条自检，输出 `check <pkg> <perm>=0`，**要求至少 120 条全为 0**，回复必须以
`GRANT_RESULT\nSYSTEM_GMS=true\n` 开头，否则父进程判定失败。

`inject.py` 在动手前还会校验目标进程的 native 库哈希：

```python
libc.so    sha256 == 76a754389a8242328cec7c1fed868289a27eb843d49175e1e0ada83f9b60f6da
libart.so  sha256 == 5d6a675fc9931595266623b7b682ec5c513128739f8194b64db289b3fddb0836
```

**不匹配就拒绝注入**——因为偏移是写死的，哈希不一致意味着偏移全错，硬跑等于让 system_server 崩。

所以这套东西**不是通用的**，它是针对特定固件编译期指纹做过适配的。

---

## 4. 本机复现：三个真实拦路虎

### 4.1 好消息：哈希一模一样

```
libc.so    76a75438…  ✓ 与 target.json 一致
libart.so  5d6a675f…  ✓ 与 target.json 一致
```

尽管 `ro.build.fingerprint` 的 build id 是 `/6`（仓库里写的是 `/172`、`/12`），
**native 库逐字节相同**，硬编码偏移全部有效。

### 4.2 坑一：`apt` 钩子拿到的 root 是残废的

StepOS 的 OpenClaw 容器里有个 sudo helper（`/etc/openclaw_sudo.conf`，端口 8900，
`expected_uid=1000`，白名单只有 `apt / apt-get / dpkg / dpkg-deb / dpkg-query / dpkg-divert`）。
用 `APT::Update::Pre-Invoke::=` 可以拿到 uid 0 —— 但那个进程是：

```
uid=0  ctx=u:r:zygote:s0
CapEff:   00000000072c25ff
CapBnd:   00000000040425ff      ← 边界比有效集小得多
NoNewPrivs: 1
Seccomp:  2   Seccomp_filters: 1
```

实测：

```
chroot('/')      → EPERM
PTRACE_TRACEME   → EACCES
```

**容器 seccomp 过滤器把 `chroot` 和 `ptrace` 都挡了。** 这条链走不通，必须换入口。

### 4.3 坑二：真正的入口是 `ubuntu_zygote`

对照同一个容器里的两个进程：

```
/proc/<pid>/exe = /system/bin/openclaw_env     Seccomp 0  CapBnd 0x072c25ff
/proc/<pid>/exe = /usr/local/bin/ubuntu_zygote Seccomp 0  CapBnd 0x072c25ff
```

**没有 seccomp。** 这就是上游为什么要把 `launcher.py` 装到 `/usr/local/bin/ubuntu_zygote` ——
它要的就是这条不受限的血统。

`launcher.py` 靠 `os.getppid()` 拿父进程的 root：

```python
host = '/proc/' + str(os.getppid()) + '/root'
rootfd = os.open(host, os.O_RDONLY | os.O_DIRECTORY)
```

`openclaw_env` 的 `/proc/<pid>/root` 就是**真实 Android 根**（有 `/init.environ.rc`、
`/system/priv-app`、`/system/build.prop`）。我们的 `driver.py` 不依赖 ppid，
直接在 `/proc/*/root` 里扫这三个特征文件，更稳。

### 4.4 坑三：签名证书和权限重算

第一次注入回报 `status: success`，`MANAGE_USERS=0` 全绿，但 Play 商店还是崩——
只是崩点换了：

```
SecurityException: reading content://com.google.android.gsf.gservices/prefix
  from uid=10192 requires com.google.android.providers.gsf.permission.READ_GSERVICES
Caused by: Failed to load configurations for FinskyApp
```

查下来两件事：

1. **设备上原本那个 `com.google.android.gsf` 是另一套证书**（与 GMS 同证书 `d1b21c60`），
   而仓库版 gsf 与 vending 同证书（`98deb0ca`）。`adb install -r` 直接报
   `INSTALL_FAILED_UPDATE_INCOMPATIBLE`，必须卸载重装。
2. 证书统一之后 **`READ_GSERVICES` 仍然是 `granted=false`**，而 `WRITE_GSERVICES` 是 `true`。
   原因：vending 的 install 权限是在 gsf **还是旧证书时**算出来并持久化的，
   换证书不会触发重算 → **重装一次 vending** 即可。

```
com.google.android.providers.gsf.permission.READ_GSERVICES:  granted=false → true
com.google.android.providers.gsf.permission.WRITE_GSERVICES: granted=true
```

还有一个容易忽略的点：**注入是纯内存的，不落盘。** 所以：

- 每次开机必须重跑（这就是 `boot.py` 里绑 `boot_id` 去重的原因）
- **重装 GMS / vending / gsf 任意一个，内存里的 privileged 标志会被冲掉**，重启一次即可恢复

---

## 5. 完整复现步骤

> 前提：目标设备上已存在可用的 OpenClaw 容器（`/system/bin/openclaw_env` +
> `/usr/local/bin/ubuntu_zygote`），并且宿主机已配好 `adb`。
> 文中 `[TARGET]`、`[ASSET_DIR]`、`[SERIAL]` 按实际替换。

### 5.1 安装正确签名的三个 APK

```bash
adb -s [SERIAL] install -r -d [ASSET_DIR]/com.google.android.gms@mtg16-20261021.apk
adb -s [SERIAL] install -r -d [ASSET_DIR]/com.android.vending@mtg16-20261021.apk
# gsf 若报 INSTALL_FAILED_UPDATE_INCOMPATIBLE，先卸载
adb -s [SERIAL] uninstall com.google.android.gsf
adb -s [SERIAL] install -r -d [ASSET_DIR]/com.google.android.gsf@mtg16-20261021.apk
```

### 5.2 让 vending 按新证书重算权限

```bash
adb -s [SERIAL] install -r -d [ASSET_DIR]/com.android.vending@mtg16-20261021.apk
adb -s [SERIAL] shell "dumpsys package com.android.vending | grep -iE 'GSERVICES.*granted'"
# 期望：READ_GSERVICES: granted=true
```

### 5.3 装开机壳

```bash
# 容器内执行（root）
cp -a /usr/local/bin/ubuntu_zygote /usr/local/bin/ubuntu_zygote.real
cp -a /usr/local/bin/ubuntu_zygote /usr/local/bin/ubuntu_zygote.stepx-stock-20261021
mkdir -p /usr/local/lib/stepx-gms
cp boot.py inject.py driver.py repair.sh /usr/local/lib/stepx-gms/
chmod 700 /usr/local/lib/stepx-gms && chmod 600 /usr/local/lib/stepx-gms/*.py
cp wrapper.sh /usr/local/bin/ubuntu_zygote && chmod 755 /usr/local/bin/ubuntu_zygote
sh -n /usr/local/bin/ubuntu_zygote   # 语法自检
```

`wrapper.sh` 的写法要点——**原厂二进制永远第一个跑**：

```sh
#!/bin/sh
/usr/local/lib/stepx-gms/repair.sh >/tmp/stepx-gms/boot.log 2>&1 </dev/null &
exec /usr/local/bin/ubuntu_zygote.real "$@"
```

`&` 先 fork（子进程继承不受限的血统），`exec` 再替换主进程 → 对系统启动零影响。
`repair.sh` 里 `sleep 8` 等 Android 起来，`boot.py` 自己还有 `sleep(12)`。

### 5.4 重启并验证

```bash
adb -s [SERIAL] reboot
# 等 sys.boot_completed=1 之后再等 ~40s 让注入跑完
```

读结果（root 权限，文件是 0600）：

```json
{
  "status": "success",
  "detached": true,
  "calls": 36,
  "identity": "<boot_id>",
  "GRANT_RESULT": "GRANT_RESULT\nSYSTEM_GMS=true\ncheck com.android.vending android.permission.MANAGE_USERS=0\n..."
}
```

### 5.5 打开商店

```bash
adb -s [SERIAL] shell am start -W -n \
  com.android.vending/com.google.android.finsky.activities.MainActivity
adb -s [SERIAL] shell "dumpsys window | grep mCurrentFocus"
```

期望焦点落在 `com.android.vending/...UnauthenticatedMainActivity`，界面显示 Google Play 登录页。

---

## 6. 结果

修复前后对比（同一台设备，同一份 APK）：

| | 修复前 | 修复后 |
|---|---|---|
| `ClassicApplication.onCreate` | `SecurityException: MANAGE_USERS` 崩溃 | 正常 |
| `Failed to load configurations for FinskyApp` | `READ_GSERVICES` 拒绝 | 正常 |
| `mCurrentFocus` | `com.stepos.ailauncher`（闪退回桌面） | `com.android.vending/.finsky.unauthenticated.activity.UnauthenticatedMainActivity` |
| `am start` 结果 | 进程起完立即死 | `Status: ok`, `LaunchState: COLD` |

`/var/lib/stepx-gms/last.json` 中注入自检的关键条目：

```
check com.android.vending android.permission.MANAGE_USERS=0
check com.android.vending android.permission.INTERACT_ACROSS_USERS=0
check com.android.vending android.permission.GET_ACCOUNTS_PRIVILEGED=0
check com.android.vending android.permission.WRITE_GSERVICES=0          ← 注入给的
check com.google.android.gms android.permission.WRITE_GSERVICES=0
check com.google.android.gms android.permission.SET_TIME_ZONE=0
```

剩下的**唯一**未解项：`com.google.android.gms` 的一个后台
`chimera.IntentOperation` 会崩（`Service without Chimera impl`），不影响商店本体，
登录流程是否受它影响待验证。

---

## 7. 附带记录：DSU 路线为什么死了

本来最干净的路是 DSU 装一份带 GMS 的 GSI，双系统验证后再合并。这台机器上**装得进、起不来**：

- `ro.boot.dynamic_partitions=true`，`/system/bin/gsi_tool` 存在，`com.android.dynsystem` 正常
- 但 `InstallationAsyncTask.verifyAndPrepare` 里写死了：
  ```java
  String ext = mUrl.substring(mUrl.lastIndexOf('.') + 1);
  if (!ext.equals("gz") && !ext.equals("gzip") && !ext.equals("zip"))
      throw new UnsupportedFormatException("Unsupported file format: " + mUrl);
  ```
  **`.img` 直接被拒**，这个 StepOS 构建里没有 `.img` 分支 → 必须打成 zip（entry 名 `system.img`）
- `system_app` 读不了 `/data/local/tmp`（`shell_data_file`），得放到 `/storage/emulated/0/Download/`
- 之后安装一路通畅：`3387408384/3387408384, total_progress: 100%` → `status: READY`
- **点重启之后**：`bootstat: Canonical boot reason: reboot,dynsystem`，然后……回到了 StepX 自己，
  `gsi_tool status` 变回 `installed`/`disabled`

原因：设备 vbmeta 链到 `CN=StepOS Team`，`framework-res.apk` 里**根本没有 `config_dsuPublicKeys`**，
锁着的 bootloader 直接拒绝了 Google 签名的 GSI 并安全回退。

好消息是回滚通道极稳：`gsi_tool disable` / `gsi_tool wipe` **普通 shell (uid 2000) 就能执行**，
不需要 root。整个过程中设备没有变砖。

---

## 8. 回滚

```bash
# 容器内（root）
cp -a /usr/local/bin/ubuntu_zygote.stepx-stock-20261021 /usr/local/bin/ubuntu_zygote
rm -rf /usr/local/lib/stepx-gms /var/lib/stepx-gms
reboot
```

卸载 GApps：

```bash
adb shell pm uninstall com.android.vending
adb shell pm uninstall com.google.android.gms
adb shell pm uninstall com.google.android.gsf
```

---

## 9. 边界与免责

- 这套注入**依赖固件级指纹**（libc / libart 哈希）。换个固件版本，`inject.py` 的偏移就是错的，
  它会自己拒绝执行——但请你自己也先核对一遍再跑。
- 修改了容器启动链（`/usr/local/bin/ubuntu_zygote`）。壳的写法保证原厂二进制先启，但**请在能物理接触
  设备的前提下操作**。
- 本文只针对**自己持有的设备**做研究记录。分发 GApps 二进制涉及 Google 的授权条款，本仓库不附带。

## 10. 参考

- StepX Companion — `handoff/google-play-stepx/`（AGPL-3.0）
- MindTheGapps 16.0.0-arm64
- AOSP `UserManagerService.checkCreateUsersPermission` / `PackageManagerService` 内部结构
