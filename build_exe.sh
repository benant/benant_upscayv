#!/bin/bash

echo "========================================"
echo "Upscayv 실행 파일 빌드"
echo "========================================"
echo ""

# PyInstaller 설치 확인
python -c "import PyInstaller" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "PyInstaller가 설치되어 있지 않습니다. 설치 중..."
    pip install pyinstaller
    if [ $? -ne 0 ]; then
        echo "PyInstaller 설치 실패!"
        exit 1
    fi
fi

echo ""
echo "실행 파일 빌드 중..."
echo ""

# PyInstaller로 실행 파일 생성
pyinstaller --onefile \
    --name upscayv \
    --console \
    --add-data "README.md:." \
    --hidden-import multiprocessing \
    --hidden-import concurrent.futures \
    upscayv.py

if [ $? -ne 0 ]; then
    echo ""
    echo "빌드 실패!"
    exit 1
fi

echo ""
echo "========================================"
echo "빌드 완료!"
echo "========================================"
echo "실행 파일 위치: dist/upscayv"
echo ""

