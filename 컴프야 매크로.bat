@echo off
chcp 65001 >nul
cd /d "%~dp0LeagueFullPlay"
if errorlevel 1 goto nofolder

call :findpy
if defined PY goto havepy

echo ==================================================
echo  파이썬이 없어서 자동으로 설치합니다. (처음 한 번, 2~5분)
echo  "이 앱이 디바이스를 변경하도록 허용..." 창이 뜨면 [예]를 눌러주세요.
echo ==================================================
where winget >nul 2>nul
if errorlevel 1 goto direct
echo [1/2] Windows 설치 도구(winget)로 설치하는 중...
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
call :findpy
if defined PY goto installed

:direct
echo [2/2] python.org 에서 설치 파일을 받는 중...
set "PYINST=%TEMP%\python-3.12.10-amd64.exe"
curl -L -s -o "%PYINST%" https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
if not exist "%PYINST%" powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile (Join-Path $env:TEMP 'python-3.12.10-amd64.exe')"
if not exist "%PYINST%" goto nopython
echo 설치하는 중... (창 없이 진행돼요. 1~3분 기다려주세요)
"%PYINST%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_tcltk=1 Include_pip=1 Include_test=0
del "%PYINST%" >nul 2>nul
call :findpy
if not defined PY goto nopython

:installed
echo 파이썬 설치 완료!

:havepy
%PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 goto notk
if exist ".installed" goto run
echo 처음 실행이라 필요한 부품을 설치합니다. 1~2분 정도 걸려요...
%PY% -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto installfail
echo ok> .installed
:run
%PY% app\launcher.py
if errorlevel 1 pause
exit /b

rem ---------- 파이썬 찾기: py 런처 -> python -> 사용자 폴더에 설치된 것 ----------
:findpy
set "PY="
py -3 --version >nul 2>nul
if not errorlevel 1 (
  set "PY=py -3"
  exit /b
)
python --version >nul 2>nul
if not errorlevel 1 (
  set "PY=python"
  exit /b
)
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%~D\python.exe" set PY="%%~D\python.exe"
exit /b

:nofolder
echo [오류] 이 파일 옆에 LeagueFullPlay 폴더가 없습니다. 압축을 푼 폴더 그대로 두고 실행해주세요.
pause
exit /b
:nopython
echo [오류] 파이썬을 자동으로 설치하지 못했습니다. 인터넷 연결을 확인하고 다시 실행하거나,
echo        LeagueFullPlay 폴더 안의 사용법.txt 1단계대로 직접 설치해주세요.
pause
exit /b
:notk
echo [오류] 설치된 파이썬에 화면 부품(tkinter)이 없습니다.
echo        사용법.txt 1단계대로 python.org 에서 파이썬을 다시 설치해주세요.
pause
exit /b
:installfail
echo [오류] 부품 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행해주세요.
pause
