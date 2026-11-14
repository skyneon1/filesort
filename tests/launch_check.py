# Copyright (c) 2026 FileSort. All rights reserved. See LICENSE.
"""Start the built program and make sure it stays up:  python tests/launch_check.py <program>"""
import subprocess
import sys
import time

proc = subprocess.Popen([sys.argv[1]])
time.sleep(10)
if proc.poll() is not None:
    sys.exit(f"FAILED: program exited on its own with code {proc.returncode}")
proc.terminate()
print("OK: program started and was still running after 10 seconds")
