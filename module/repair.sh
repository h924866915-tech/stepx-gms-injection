#!/bin/sh
# 等 Android 起来再动手；boot.py 自己还有 12 秒的 sleep。
sleep 8
exec python3 /usr/local/lib/stepx-gms/driver.py
