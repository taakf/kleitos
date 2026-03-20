"""Compatibility wrapper — redirects to axion-menubar.py"""
import os, sys
script_dir = os.path.dirname(os.path.abspath(__file__))
os.execv(sys.executable, [sys.executable, os.path.join(script_dir, "axion-menubar.py")] + sys.argv[1:])
