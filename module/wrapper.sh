#!/bin/sh
# StepX GMS boot hook.
#
# 装到 /usr/local/bin/ubuntu_zygote（原厂二进制先备份为 ubuntu_zygote.real）。
# 设计要点：原厂 zygote 永远第一个启动，且是 exec 替换主进程，对系统启动零影响；
# 修复动作跑在 fork 出来的子进程里，那条血统没有容器 seccomp 限制。
/usr/local/lib/stepx-gms/repair.sh >/tmp/stepx-gms/boot.log 2>&1 </dev/null &
exec /usr/local/bin/ubuntu_zygote.real "$@"
