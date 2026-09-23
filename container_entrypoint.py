"""Initialize the mounted data directory, then permanently drop root privileges."""

import os
import sys
from pathlib import Path

directory = Path(os.environ["RF_LINK_DATA_DIR"])
directory.mkdir(parents=True, exist_ok=True)
if os.geteuid() == 0:
    os.chown(directory, 10001, 10001)
    os.chmod(directory, 0o700)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
os.execv(sys.executable, [sys.executable, "-m", "rf_link_calculator.portal"])
