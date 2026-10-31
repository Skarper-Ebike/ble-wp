#!/usr/bin/env python3
"""Inject faults into a running emulator.

  python3 ctl.py drop          drop the link now (supervision timeout)
  python3 ctl.py reboot [s]    reboot the pack, off air for s seconds
  python3 ctl.py storm [s]     telemetry at 200 Hz for s seconds
  python3 ctl.py corrupt [s]   bad CRC on every telemetry frame for s seconds
  python3 ctl.py silent [s]    pack stops answering commands for s seconds
  python3 ctl.py status
"""
import os
import socket
import sys

host = os.environ.get("EMU_HOST", "127.0.0.1")
port = int(os.environ.get("EMU_CTL_PORT", 7701))
with socket.create_connection((host, port), timeout=3) as s:
    s.sendall((" ".join(sys.argv[1:]) + "\n").encode())
    print(s.makefile().read().strip())
