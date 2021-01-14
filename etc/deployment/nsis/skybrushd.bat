@echo off
setlocal enableDelayedExpansion

rem Bypass "Terminate Batch Job" prompt
if "%~1"=="__FIXED_CTRL_C__" (
    rem Remove the FIXED_CTRL_C parameter
	shift
) else (
    rem Run ourselves with <NUL and FIXED_CTRL_C
    CALL <NUL %0 __FIXED_CTRL_C__ %*
	GOTO :EOF
)

rem Get arguments except the FIXED_CTRL_C one
set _tail=

:loop
if "%1" == "" goto end
set _tail=%_tail% %1
shift
goto loop

:end
cd %~dp0
Python\python.exe skybrushd-win32.py %_tail%
