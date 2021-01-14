@echo off

cd %~dp0
set PYARMOR_LICENSE=skybrushd.cml
Python\python.exe skybrushd-win32.py %*
