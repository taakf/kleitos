@echo off
:: ============================================================================
:: COMPATIBILITY SHIM — This file forwards to Axion.bat
:: The product has been renamed from Kleitos to Axion by 4Labs.
:: Please use Axion.bat going forward.
:: ============================================================================
echo.
echo   This launcher has been renamed to Axion.bat
echo   Redirecting automatically...
echo.
call "%~dp0Axion.bat" %*
