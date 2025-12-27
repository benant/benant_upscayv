@echo off
echo ========================================
echo Upscayv 실행 파일 빌드
echo ========================================
echo.

REM PyInstaller 설치 확인
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo PyInstaller가 설치되어 있지 않습니다. 설치 중...
    pip install pyinstaller
    if errorlevel 1 (
        echo PyInstaller 설치 실패!
        pause
        exit /b 1
    )
)

echo.
echo 실행 파일 빌드 중...
echo.

REM PyInstaller로 실행 파일 생성
pyinstaller --onefile ^
    --name upscayv ^
    --console ^
    --add-data "README.md;." ^
    --hidden-import multiprocessing ^
    --hidden-import concurrent.futures ^
    upscayv.py

if errorlevel 1 (
    echo.
    echo 빌드 실패!
    pause
    exit /b 1
)

echo.
echo ========================================
echo 빌드 완료!
echo ========================================
echo 실행 파일 위치: dist\upscayv.exe
echo.
pause

