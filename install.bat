@echo off
cd /d "%~dp0"
echo Installing packages...
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
if errorlevel 1 (echo. & echo Install failed. Make sure Python is installed with "Add to PATH". & pause & exit /b 1)
echo Adding to Windows startup...
(echo @echo off& echo call "%~dp0start_widget.bat") > "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\calendar_widget.bat"
echo Starting widget...
call "%~dp0start_widget.bat"
echo Done!
pause
