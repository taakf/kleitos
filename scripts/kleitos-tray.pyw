"""Compatibility wrapper — redirects to axion-tray.pyw"""
import os, sys
script_dir = os.path.dirname(os.path.abspath(__file__))
os.execv(sys.executable, [sys.executable, os.path.join(script_dir, "axion-tray.pyw")] + sys.argv[1:])
