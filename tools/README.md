# tools/

这三个脚本是复现时实际用的驱动，跑在**宿主机**（Windows / Linux 均可），不是设备上。

| 文件 | 作用 |
|---|---|
| `gmsboot.py` | 把 `module/{boot,inject}.py` 送进容器，然后以 root 执行一次注入（调试用，等价于开机钩子） |
| `gmswrap.py` | 安装开机壳：备份原厂 `ubuntu_zygote`、铺 `module/` 里的文件、替换启动入口 |
| `gmsdiag.py` | 逐步骤探针：定位卡在 chroot / setresuid / setfsuid / SELinux `setcontext` 的哪一步 |

它们依赖两个薄封装（本仓库里已有同类实现，按你的环境挑用）：

- `gw.run(argv)` —— 通过 OpenClaw 网关执行容器命令（对应 `handoff/google-play-stepx/tools/gateway.py`）
- `rootrun.run_root(shell_code)` —— 借 sudo helper 的 apt hook 升到 uid 0
  （对应 `handoff/google-play-stepx/tools/root_bridge.py`）

`HANDOFF` 常量指向 `handoff/google-play-stepx/module`，可用环境变量覆盖：

```bash
export STEPX_HANDOFF=/path/to/stepx/handoff/google-play-stepx/module
```

## 一个必须知道的限制

`rootrun`（apt hook）拿到的 root **不能跑注入**：容器 seccomp 会拦 `chroot` 和 `ptrace`。
`gmsdiag.py` 就是用来证明这件事的。真正的注入必须从 `ubuntu_zygote` 那条血统出来
（`Seccomp: 0`、`CapBnd: 0x072c25ff`），也就是 `gmswrap.py` 装的那个开机壳。
