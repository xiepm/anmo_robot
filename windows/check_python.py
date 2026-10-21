"""Require the interpreter ABI used by the Windows binary dependency set."""
import sys
import sysconfig
raise SystemExit(0 if sys.version_info[:2] == (3, 12) and sysconfig.get_platform() == 'win-amd64' else 1)
