#!/usr/bin/env python3
import sys
import importlib

print("Python executable:", sys.executable)
print("Python version:", sys.version)
print("\nChecking imports:")

packages = ['flask', 'requests', 'schedule', 'json', 'uuid', 'threading', 'sqlite3', 'datetime']

for package in packages:
    try:
        importlib.import_module(package)
        print(f"{package}: OK")
    except ImportError as e:
        print(f"{package}: {e}")

print("\nSite packages path:")
for path in sys.path:
    if 'site-packages' in path:
        print(f"  {path}")
