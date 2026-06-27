import os
import re
import subprocess
import json
import shutil
import time
import argparse
from datetime import datetime
from pathlib import Path
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

# --- [설정 영역] ---
def find_upscayl_path():
    """Upscayl 실행 파일 경로를 자동으로 찾습니다."""
    # 1. PATH 환경 변수에서 찾기
    upscayl_path = shutil.which("upscayl-bin") or shutil.which("upscayl-bin.exe")
    if upscayl_path and os.path.exists(upscayl_path):
        return upscayl_path
    
    # 2. 일반적인 Windows 설치 경로 확인
    possible_paths = [
        # 사용자 로컬 AppData 경로
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "upscayl" / "upscayl-bin.exe",
        # Program Files 경로
        Path(os.environ.get("PROGRAMFILES", "")) / "upscayl" / "upscayl-bin.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "upscayl" / "resources" / "bin" / "upscayl-bin.exe",
        # Program Files (x86) 경로
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "upscayl" / "upscayl-bin.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "upscayl" / "resources" / "bin" / "upscayl-bin.exe",
        # 사용자 홈 디렉토리
        Path.home() / "AppData" / "Local" / "Programs" / "upscayl" / "upscayl-bin.exe",
    ]
    
    for path in possible_paths:
        if path.exists():
            return str(path)
    
    # 3. 찾지 못한 경우 None 반환
    return None

UPSCAYL_PATH = find_upscayl_path()
if UPSCAYL_PATH is None:
    print("⚠️ Upscayl 실행 파일을 찾을 수 없습니다.")
    print("다음 경로 중 하나에 설치되어 있는지 확인해주세요:")
    print("  - %LOCALAPPDATA%\\Programs\\upscayl\\upscayl-bin.exe")
    print("  - %PROGRAMFILES%\\upscayl\\upscayl-bin.exe")
    print("  - 또는 PATH 환경 변수에 등록되어 있는지 확인하세요.")
    exit(1)

# Upscayl 실행 파일이 있는 디렉토리에서 models 폴더 찾기
upscayl_dir = Path(UPSCAYL_PATH).parent
possible_model_paths = [
    upscayl_dir / "models",
    upscayl_dir / "resources" / "models",
    upscayl_dir.parent / "models",
    upscayl_dir.parent / "resources" / "models",
]

MODEL_PATH = None
for model_path in possible_model_paths:
    if model_path.exists() and model_path.is_dir():
        MODEL_PATH = str(model_path)
        break

if MODEL_PATH is None:
    # 기본값으로 상대 경로 사용 (사용자가 직접 설정 가능)
    MODEL_PATH = "models"
    print(f"⚠️ 모델 폴더를 자동으로 찾지 못했습니다. 기본값 '{MODEL_PATH}'을 사용합니다.")
    print(f"   필요시 스크립트에서 MODEL_PATH를 직접 설정해주세요.")

def find_available_models(model_path):
    """모델 폴더에서 사용 가능한 모델 목록을 찾습니다."""
    if not os.path.exists(model_path):
        return []
    
    models = []
    # 모델 폴더의 파일/폴더 목록 확인
    for item in os.listdir(model_path):
        item_path = os.path.join(model_path, item)
        # .bin 파일이나 폴더를 모델로 간주
        if os.path.isfile(item_path) and item.endswith('.bin'):
            models.append(item.replace('.bin', ''))
        elif os.path.isdir(item_path):
            # 폴더 내에 .bin 파일이 있는지 확인
            bin_files = [f for f in os.listdir(item_path) if f.endswith('.bin')]
            if bin_files:
                models.append(item)
    
    return sorted(models)

def get_model_speed_score(model_name):
    """모델 이름을 기반으로 속도 점수를 계산합니다. 점수가 낮을수록 빠름."""
    score = 100
    model_lower = model_name.lower()
    
    # 빠른 모델 키워드 (점수 감소)
    if 'x2' in model_lower:
        score -= 50
    elif 'x4' in model_lower:
        score -= 30
    
    if 'small' in model_lower or 'fast' in model_lower or 'lite' in model_lower:
        score -= 20
    
    # 느린 모델 키워드 (점수 증가)
    if 'x8' in model_lower:
        score += 30
    if 'large' in model_lower or 'ultra' in model_lower or 'balanced' in model_lower:
        score += 20
    if 'remacri' in model_lower or 'ultramix' in model_lower:
        score += 15
    
    # 모델 이름 길이 (짧을수록 간단한 모델일 가능성)
    if len(model_name) < 10:
        score -= 10
    
    return score

def get_fastest_model(models):
    """모델 목록에서 가장 빠른 모델을 반환합니다."""
    if not models:
        return None
    
    # 속도 점수로 정렬 (점수가 낮을수록 빠름)
    sorted_models = sorted(models, key=get_model_speed_score)
    return sorted_models[0]

def test_encoder(encoder_name, error_keywords, debug=False):
    """인코더가 실제로 사용 가능한지 테스트합니다."""
    try:
        # AMD AMF의 경우 더 큰 해상도와 적절한 파라미터 필요
        if encoder_name == 'h264_amf':
            # AMF는 최소 해상도 요구사항이 있을 수 있으므로 더 큰 해상도로 테스트
            test_cmd = [
                'ffmpeg', '-hide_banner', '-f', 'lavfi', '-i', 'testsrc=duration=0.1:size=320x240:rate=1',
                '-c:v', 'h264_amf', '-quality', 'speed', '-rc', 'cqp', '-qp_i', '23', '-qp_p', '23',
                '-frames:v', '1', '-f', 'null', '-'
            ]
        elif encoder_name == 'h264_nvenc':
            # NVIDIA NVENC는 적절한 파라미터와 함께 테스트
            test_cmd = [
                'ffmpeg', '-hide_banner', '-f', 'lavfi', '-i', 'testsrc=duration=0.1:size=320x240:rate=1',
                '-c:v', 'h264_nvenc', '-preset', 'fast', '-rc', 'cbr', '-b:v', '1M',
                '-frames:v', '1', '-f', 'null', '-'
            ]
        else:
            # 기타 인코더는 작은 해상도로 테스트
            test_cmd = [
                'ffmpeg', '-hide_banner', '-f', 'lavfi', '-i', 'testsrc=duration=0.1:size=64x64:rate=1',
                '-c:v', encoder_name, '-frames:v', '1', '-f', 'null', '-'
            ]
        
        test_result = subprocess.run(
            test_cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        # 디버깅 모드일 때 전체 에러 메시지 출력
        if debug:
            if test_result.returncode != 0:
                debug_print(f"  [디버그] {encoder_name} 테스트 실패 (returncode: {test_result.returncode}):")
                # stderr에서 실제 에러 부분만 추출 (Input 정보 제외)
                error_lines = [line for line in test_result.stderr.split('\n') 
                             if any(keyword in line.lower() for keyword in ['error', 'failed', 'cannot', 'not found', 'unable', 'no', 'missing'])]
                if error_lines:
                    for line in error_lines[:8]:  # 최대 8줄까지
                        debug_print(f"    {line.strip()}")
                else:
                    # 에러 라인이 없으면 마지막 부분 출력
                    debug_print(f"    {test_result.stderr[-500:]}")
            else:
                debug_print(f"  [디버그] {encoder_name} 테스트 성공!")
        
        # 성공했고 (returncode == 0), 에러 메시지에 관련 에러가 없어야 사용 가능
        if test_result.returncode == 0:
            # stderr를 소문자로 변환하여 에러 키워드 확인
            stderr_lower = test_result.stderr.lower()
            if not any(err.lower() in stderr_lower for err in error_keywords):
                return True
            elif debug:
                debug_print(f"  [디버그] {encoder_name} 테스트는 성공했지만 에러 키워드가 감지되었습니다.")
        
    except subprocess.TimeoutExpired:
        if debug:
            debug_print(f"  [디버그] {encoder_name} 테스트 시간 초과 (10초)")
    except FileNotFoundError:
        if debug:
            debug_print(f"  [디버그] {encoder_name} 테스트 실패: FFmpeg을 찾을 수 없습니다.")
    except Exception as e:
        if debug:
            debug_print(f"  [디버그] {encoder_name} 테스트 예외: {e}")
    return False

def detect_video_encoder():
    """GPU 하드웨어 인코더를 우선적으로 감지합니다. NVIDIA > AMD > CPU 순서."""
    try:
        # FFmpeg에서 사용 가능한 인코더 목록 확인
        result = subprocess.run(
            ['ffmpeg', '-hide_banner', '-encoders'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode != 0:
            print("  [경고] FFmpeg 인코더 목록을 가져올 수 없습니다. CPU 인코딩을 사용합니다.")
            return "libx264"
        
        # 1. NVIDIA GPU (h264_nvenc) 확인
        if 'h264_nvenc' in result.stdout:
            print("  [검색] NVIDIA NVENC 인코더를 감지했습니다. 테스트 중...")
            nvenc_errors = [
                'No NVENC capable devices found',
                'No capable devices found',
                'NVENC not available',
                'Cannot load',
                'No such filter',
                'not found',
                'unable to find'
            ]
            if test_encoder('h264_nvenc', nvenc_errors, debug=DEBUG_MODE):
                print("  [성공] NVIDIA GPU 인코더(h264_nvenc)를 사용할 수 있습니다!")
                return "h264_nvenc"
            else:
                # NVIDIA 인코더가 있지만 테스트 실패 - 디버깅 정보 출력
                print("  [경고] NVIDIA GPU 인코더(h264_nvenc)가 감지되었지만 초기화에 실패했습니다.")
                print("  [조치] 다음을 확인해주세요:")
                print("    1. NVIDIA 드라이버가 최신인지 확인")
                print("    2. FFmpeg이 NVENC를 지원하는 빌드인지 확인 (ffmpeg -encoders | findstr nvenc)")
                print("    3. GPU가 다른 프로그램에서 사용 중이 아닌지 확인")
                print("  [대안] CPU 인코딩을 사용합니다.")
        else:
            print("  [정보] FFmpeg에서 NVIDIA NVENC 인코더를 찾을 수 없습니다.")
            print("  [조치] FFmpeg이 NVENC를 지원하는 빌드인지 확인하세요.")
            print("    - NVENC 지원 빌드: https://www.gyan.dev/ffmpeg/builds/")
            print("    - 또는 'ffmpeg -encoders | findstr nvenc' 명령으로 확인")
        
        # 2. AMD GPU (h264_amf) 확인
        if 'h264_amf' in result.stdout:
            print("  [검색] AMD AMF 인코더를 감지했습니다. 테스트 중...")
            amf_errors = [
                'No capable devices found',
                'AMF not available',
                'Cannot load',
                'No such filter',
                'Failed to initialize',
                'AMF runtime'
            ]
            # AMD iGPU도 지원하므로 테스트 (디버깅 모드 활성화)
            if test_encoder('h264_amf', amf_errors, debug=DEBUG_MODE):
                print("  [성공] AMD GPU 인코더(h264_amf)를 사용할 수 있습니다!")
                return "h264_amf"
            else:
                # AMD 인코더가 있지만 테스트 실패 - 디버깅 정보 출력
                print("  [정보] AMD GPU 인코더(h264_amf)가 감지되었지만 초기화에 실패했습니다.")
                print("  [정보] 드라이버가 최신인지 확인하거나, CPU 인코딩을 사용합니다.")
        
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        print(f"  [경고] 인코더 감지 중 오류 발생: {e}")
        print("  [대안] CPU 인코딩을 사용합니다.")
    
    # 3. GPU 인코더를 사용할 수 없으면 CPU 인코더 사용
    print("  [정보] CPU 인코더(libx264)를 사용합니다.")
    return "libx264"

# 전역 디버그 모드 플래그
DEBUG_MODE = False

def debug_print(*args, **kwargs):
    """디버그 모드일 때만 메시지를 출력합니다."""
    if DEBUG_MODE:
        print(*args, **kwargs)

def check_ffmpeg():
    """FFmpeg이 설치되어 있고 사용 가능한지 확인합니다."""
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            ffmpeg_path = shutil.which('ffmpeg')
            return True, ffmpeg_path
        return False, None
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return False, None

def get_cpu_info():
    """CPU 정보를 가져옵니다."""
    try:
        cpu_count = multiprocessing.cpu_count()
        return cpu_count
    except Exception:
        return 4  # 기본값

def get_gpu_info():
    """NVIDIA GPU 정보를 가져옵니다."""
    try:
        # nvidia-smi 명령어로 GPU 개수 확인
        result = subprocess.run(
            ['nvidia-smi', '--list-gpus'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            gpu_count = len(result.stdout.strip().split('\n'))
            return gpu_count
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass
    
    # nvidia-smi가 없거나 실패한 경우, 환경 변수나 다른 방법으로 확인
    try:
        # CUDA_VISIBLE_DEVICES 환경 변수 확인
        cuda_devices = os.environ.get('CUDA_VISIBLE_DEVICES', '')
        if cuda_devices:
            return len([d for d in cuda_devices.split(',') if d.strip()])
    except Exception:
        pass
    
    return 0

def calculate_optimal_workers(cpu_count, gpu_count, has_gpu_encoder):
    """CPU와 GPU 정보를 기반으로 최적의 워커 수를 계산합니다."""
    if gpu_count > 0:
        # GPU가 있는 경우: GPU 개수에 맞춰 워커 수 설정
        # Upscayl이 GPU를 사용하므로, GPU 개수만큼 병렬 처리 가능
        # 하지만 CPU도 일부 사용하므로 약간의 여유를 둠
        if gpu_count == 1:
            # 단일 GPU: CPU 코어 수의 50-75% 정도 사용
            recommended = max(1, min(cpu_count // 2, 4))
        else:
            # 다중 GPU: GPU 개수에 맞춰 설정하되, CPU 코어 수를 초과하지 않음
            recommended = min(gpu_count, cpu_count)
        
        # GPU가 있으면 최소 2개는 사용
        recommended = max(2, recommended)
    else:
        # GPU가 없는 경우: CPU 기반 처리
        # CPU 코어 수의 75% 정도 사용 (시스템 응답성 유지)
        recommended = max(1, int(cpu_count * 0.75))
    
    # 최대값 제한 (너무 많은 워커는 오히려 성능 저하)
    recommended = min(recommended, cpu_count, 8)
    
    return recommended

def upscale_single_frame(args):
    """단일 프레임을 업스케일링하는 함수 (병렬 처리용)."""
    frame_file, input_dir_abs, output_dir_abs, upscayl_path, model_path_abs, selected_model, scale_factor, ffmpeg_path = args
    
    input_path = os.path.join(input_dir_abs, frame_file)
    output_path = os.path.join(output_dir_abs, frame_file)
    
    # Upscayl 명령어
    upscale_cmd = f'"{upscayl_path}" -i "{input_path}" -o "{output_path}" -s {scale_factor} -m "{model_path_abs}" -n {selected_model}'
    
    # 환경 변수 설정
    env = os.environ.copy()
    if ffmpeg_path:
        ffmpeg_dir = os.path.dirname(ffmpeg_path)
        current_path = env.get('PATH', '')
        if ffmpeg_dir not in current_path:
            env['PATH'] = f"{ffmpeg_dir};{current_path}"
    
    # Upscayl 실행
    result = subprocess.run(
        upscale_cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=os.getcwd()
    )
    
    # 결과 반환
    return {
        'frame_file': frame_file,
        'returncode': result.returncode,
        'stdout': result.stdout.decode('utf-8', errors='ignore') if result.stdout else '',
        'stderr': result.stderr.decode('utf-8', errors='ignore') if result.stderr else '',
        'output_path': output_path
    }

TEMP_DIR = "temp_frames"
UPSCALED_DIR = "upscaled_frames"

# 명령줄 인자 파싱
def parse_arguments():
    """명령줄 인자를 파싱합니다."""
    parser = argparse.ArgumentParser(
        description='Upscayv - AI Video Upscaler by Upscayl',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예제:
  python upscayv.py              # 일반 모드로 실행
  python upscayv.py --debug       # 디버그 모드로 실행
  python upscayv.py -d            # 디버그 모드로 실행 (짧은 옵션)
        """
    )
    parser.add_argument(
        '-d', '--debug',
        action='store_true',
        help='디버그 모드 활성화 (상세한 디버그 메시지 출력)'
    )
    return parser.parse_args()

# 전역 변수 (워커 프로세스에서도 접근 가능하도록 모듈 레벨에 선언)
VIDEO_ENCODER = None
ffmpeg_path = None

RES_OPTIONS = {
    "1": ("HD", 1280, 720),
    "2": ("FHD", 1920, 1080),
    "3": ("4K", 3840, 2160),
    "4": ("8K", 7680, 4320)
}

def build_output_filename(video_filename, res_name, model_index):
    """업스케일 결과 파일명을 생성합니다. 예: 17-2_FHD_M7_20250627_143052.mp4"""
    stem = os.path.splitext(video_filename)[0]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{stem}_{res_name}_M{model_index}_{timestamp}.mp4"

def is_upscaled_output(filename):
    """업스케일 결과물 파일명인지 확인합니다."""
    if not filename.endswith('.mp4'):
        return False
    stem = os.path.splitext(filename)[0]
    res_pattern = '|'.join(re.escape(name) for name, _, _ in RES_OPTIONS.values())
    return re.match(rf'.+_({res_pattern})_M\d+_\d{{8}}_\d{{6}}$', stem) is not None

def get_resolution_name(width, height):
    """해상도에 맞는 표준 해상도 이름을 반환합니다."""
    # 해상도 매칭 (약간의 오차 허용)
    resolution_map = [
        ((7680, 4320), "8K"),
        ((3840, 2160), "4K"),
        ((1920, 1080), "FHD"),
        ((1280, 720), "HD"),
    ]
    
    for (w, h), name in resolution_map:
        # 정확히 일치하거나 약간의 오차 허용 (±10픽셀)
        if abs(width - w) <= 10 and abs(height - h) <= 10:
            return name
    
    # 매칭되지 않으면 해상도만 반환
    return None

def get_video_info(video_path):
    cmd = f'ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,nb_frames -of json "{video_path}"'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    
    w = int(data['streams'][0]['width'])
    h = int(data['streams'][0]['height'])
    fps_raw = data['streams'][0]['r_frame_rate']
    num, den = map(int, fps_raw.split('/'))
    fps = num / den
    # 총 프레임 수 (진행 바 표시용)
    total_frames = int(data['streams'][0].get('nb_frames', 0))
    
    return w, h, fps, total_frames

def cleanup():
    """작업용 임시 폴더 삭제"""
    if os.path.exists(TEMP_DIR): shutil.rmtree(TEMP_DIR)
    if os.path.exists(UPSCALED_DIR): shutil.rmtree(UPSCALED_DIR)
    print("🧹 임시 파일 정리가 완료되었습니다.")

def run_upscale():
    # 1. 파일 선택
    video_files = [f for f in os.listdir('.') if f.endswith('.mp4') and not is_upscaled_output(f)]
    if not video_files:
        print("❌ MP4 파일을 찾을 수 없습니다."); return
    
    if len(video_files) == 1:
        selected_video = video_files[0]
    else:
        for i, f in enumerate(video_files): print(f"[{i+1}] {f}")
        selected_video = video_files[int(input("\n번호 선택: "))-1]
    
    print(f"\n5. 📁 선택된 파일: {selected_video}")

    # 2. 정보 및 해상도 선택
    width, height, fps, total_frames = get_video_info(selected_video)
    current_res_name = get_resolution_name(width, height)
    if current_res_name:
        print(f"\n6. 📺 현재 영상: {width}x{height} ({current_res_name}) - {fps} fps, 총 {total_frames} 프레임")
    else:
        print(f"\n6. 📺 현재 영상: {width}x{height} (비표준 해상도) - {fps} fps, 총 {total_frames} 프레임")
    
    # 원본 영상의 비율 계산
    aspect_ratio = width / height
    
    # 목표 해상도 선택 메뉴 생성
    res_menu = ", ".join([f"{key}:{name}({w}x{h})" for key, (name, w, h) in RES_OPTIONS.items()])
    res_name, target_w, target_h = RES_OPTIONS.get(input(f"7. 목표 해상도 ({res_menu}): "), RES_OPTIONS["2"])
    
    # 원본 비율을 유지하면서 목표 해상도에 맞춤
    # 목표 해상도의 비율
    target_aspect = target_w / target_h
    
    if aspect_ratio > target_aspect:
        # 원본이 더 넓음 (가로가 더 긴 경우) - 높이를 기준으로 너비 계산
        final_height = target_h
        final_width = int(target_h * aspect_ratio)
        # 최대 너비 제한 (목표 해상도보다 크지 않도록)
        if final_width > target_w:
            final_width = target_w
            final_height = int(target_w / aspect_ratio)
    else:
        # 원본이 더 높음 (세로가 더 긴 경우) - 너비를 기준으로 높이 계산
        final_width = target_w
        final_height = int(target_w / aspect_ratio)
        # 최대 높이 제한 (목표 해상도보다 크지 않도록)
        if final_height > target_h:
            final_height = target_h
            final_width = int(target_h * aspect_ratio)
    
    # 짝수로 맞춤 (비디오 인코딩 호환성)
    final_width = final_width - (final_width % 2)
    final_height = final_height - (final_height % 2)
    
    # 최종 해상도 정보 표시
    print(f"\n📐 원본 비율 유지: {width}x{height} → {final_width}x{final_height} (비율: {aspect_ratio:.2f})")
    
    scale_factor = 4 if final_width / width > 2 else 2

    # 3. 폴더 초기화
    cleanup()
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(UPSCALED_DIR, exist_ok=True)

    try:
        # 4. 프레임 추출
        print(f"\n[1/3] 🎞️ 프레임 추출 중...")
        subprocess.run(f'ffmpeg -i "{selected_video}" -q:v 2 "{TEMP_DIR}/frame_%05d.png"', shell=True, check=True)

        # 5. AI 업스케일링 (폴더 전체를 배치로 처리)
        print(f"\n[2/3] 🤖 AI 업스케일링 시작 ({res_name})...")
        
        # 업스케일링 작업 시작 시간 기록
        upscale_start_time = time.time()
        
        # 모델 폴더에서 사용 가능한 모델 찾기
        model_path_abs = os.path.abspath(MODEL_PATH) if os.path.exists(MODEL_PATH) else MODEL_PATH
        available_models = find_available_models(model_path_abs)
        
        if not available_models:
            raise Exception(f"모델 폴더에서 사용 가능한 모델을 찾을 수 없습니다: {model_path_abs}")
        
        # 가장 빠른 모델을 기본값으로 설정
        fastest_model = get_fastest_model(available_models)
        default_index = available_models.index(fastest_model) + 1 if fastest_model in available_models else 1
        
        # 사용자가 모델 선택
        if len(available_models) == 1:
            selected_model = available_models[0]
            model_index = 1
            print(f"\n📦 사용할 모델: {selected_model} (자동 선택)")
        else:
            print(f"\n📦 사용 가능한 모델:")
            for i, model in enumerate(available_models, 1):
                marker = " ⚡ (가장 빠름)" if model == fastest_model else ""
                print(f"  [{i}] {model}{marker}")
            
            while True:
                try:
                    choice = input(f"\n모델 선택 (1-{len(available_models)}, 기본값: {default_index}): ").strip()
                    if not choice:
                        choice = str(default_index)
                    choice_num = int(choice)
                    if 1 <= choice_num <= len(available_models):
                        selected_model = available_models[choice_num - 1]
                        model_index = choice_num
                        break
                    else:
                        print(f"❌ 1부터 {len(available_models)} 사이의 숫자를 입력하세요.")
                except ValueError:
                    print("❌ 숫자를 입력하세요.")
                except KeyboardInterrupt:
                    print("\n\n작업이 취소되었습니다.")
                    return
            
            print(f"✅ 선택된 모델: {selected_model}")
        
        # temp_frames 폴더의 모든 PNG 파일 확인
        frame_files = sorted([f for f in os.listdir(TEMP_DIR) if f.endswith('.png')])
        if not frame_files:
            raise Exception(f"{TEMP_DIR} 폴더에 프레임 파일이 없습니다.")
        
        # 절대 경로로 변환
        input_dir_abs = os.path.abspath(TEMP_DIR)
        output_dir_abs = os.path.abspath(UPSCALED_DIR)
        
        # CPU/GPU 정보 확인 및 최적 워커 수 계산
        cpu_count = get_cpu_info()
        gpu_count = get_gpu_info()
        has_gpu_encoder = VIDEO_ENCODER in ['h264_nvenc', 'h264_amf']
        
        print(f"\n[시스템 정보]")
        print(f"  CPU 코어 수: {cpu_count}")
        if gpu_count > 0:
            print(f"  GPU 개수: {gpu_count}")
        else:
            print(f"  GPU: 감지되지 않음")
        
        # 최적 워커 수 계산
        recommended_workers = calculate_optimal_workers(cpu_count, gpu_count, has_gpu_encoder)
        
        # 사용자에게 워커 수 확인
        print(f"\n[병렬 처리 설정]")
        print(f"  권장 워커 수: {recommended_workers}")
        try:
            worker_input = input(f"  사용할 워커 수 (기본값: {recommended_workers}, Enter로 기본값 사용): ").strip()
            if worker_input:
                num_workers = int(worker_input)
                if num_workers < 1:
                    print("  ⚠️ 워커 수는 1 이상이어야 합니다. 기본값을 사용합니다.")
                    num_workers = recommended_workers
                elif num_workers > cpu_count * 2:
                    print(f"  ⚠️ 워커 수가 너무 많습니다. CPU 코어 수({cpu_count})의 2배를 초과합니다.")
                    confirm = input(f"  계속하시겠습니까? (y/n, 기본값: n): ").strip().lower()
                    if confirm != 'y':
                        num_workers = recommended_workers
            else:
                num_workers = recommended_workers
        except (ValueError, KeyboardInterrupt):
            print("  기본값을 사용합니다.")
            num_workers = recommended_workers
        
        print(f"  ✅ {num_workers}개의 워커로 병렬 처리합니다.")
        
        # Upscayl 명령어: 폴더 전체를 배치로 처리
        # 각 파일에 대해 절대 경로 + 파일명으로 출력 지정
        debug_print(f"\n[디버그] 입력 폴더: {input_dir_abs}")
        debug_print(f"[디버그] 출력 폴더: {output_dir_abs}")
        debug_print(f"[디버그] 모델: {selected_model}")
        debug_print(f"[디버그] 스케일: {scale_factor}x")
        
        # 첫 번째 프레임에 대한 명령어 예시 출력
        if frame_files:
            first_frame = frame_files[0]
            first_input = os.path.join(input_dir_abs, first_frame)
            first_output = os.path.join(output_dir_abs, first_frame)
            upscale_cmd_example = f'"{UPSCAYL_PATH}" -i "{first_input}" -o "{first_output}" -s {scale_factor} -m "{model_path_abs}" -n {selected_model}'
            debug_print(f"\n[디버그] Upscayl 명령어 예시: {upscale_cmd_example}")
        
        # 병렬 처리 준비: 각 프레임에 대한 작업 인자 생성
        work_args = [
            (
                frame_file,
                input_dir_abs,
                output_dir_abs,
                UPSCAYL_PATH,
                model_path_abs,
                selected_model,
                scale_factor,
                ffmpeg_path
            )
            for frame_file in frame_files
        ]
        
        # 병렬 처리 실행
        # 작업 큐를 사용하여 워커가 작업을 완료하면 즉시 다음 작업을 가져오도록 최적화
        failed_frames = []
        completed_count = 0
        
        with tqdm(total=len(frame_files), desc="Upscaling", unit="frame") as pbar:
            with ProcessPoolExecutor(max_workers=num_workers) as executor:
                # 작업 큐: 워커 수의 2-3배만큼 미리 제출하여 GPU를 지속적으로 활용
                # 나머지는 워커가 작업을 완료할 때마다 동적으로 제출
                work_queue = iter(work_args)
                future_to_frame = {}
                
                # 초기 작업 제출: 워커 수의 2배만큼 미리 제출
                initial_batch_size = min(num_workers * 2, len(work_args))
                for i, args in enumerate(work_args):
                    if i >= initial_batch_size:
                        break
                    future = executor.submit(upscale_single_frame, args)
                    future_to_frame[future] = args[0]
                
                # 완료된 작업 처리 및 새 작업 제출
                pending_count = initial_batch_size
                
                while future_to_frame:
                    # 완료된 작업 처리
                    for future in as_completed(future_to_frame):
                        frame_file = future_to_frame.pop(future)
                        try:
                            result = future.result()
                            
                            # 첫 번째 완료된 프레임에 대한 디버그 정보 출력
                            if completed_count == 0:
                                debug_print(f"\n[디버그] 첫 번째 프레임 처리 완료: {result['frame_file']}")
                                debug_print(f"[디버그] 종료 코드: {result['returncode']}")
                                if result['stderr']:
                                    debug_print(f"[디버그] stderr:\n{result['stderr'][:500]}")
                            
                            # 에러 확인
                            if result['returncode'] != 0:
                                failed_frames.append({
                                    'frame': result['frame_file'],
                                    'returncode': result['returncode'],
                                    'stderr': result['stderr']
                                })
                                print(f"\n❌ 프레임 {result['frame_file']} 업스케일링 실패 (종료 코드: {result['returncode']})")
                                if result['stderr']:
                                    print(f"에러: {result['stderr'][-300:]}")
                            
                            # 출력 파일 확인
                            if not os.path.exists(result['output_path']):
                                failed_frames.append({
                                    'frame': result['frame_file'],
                                    'returncode': -1,
                                    'stderr': f"업스케일된 파일이 생성되지 않았습니다: {result['output_path']}"
                                })
                                print(f"\n❌ 업스케일된 파일이 생성되지 않았습니다: {result['output_path']}")
                            
                            completed_count += 1
                            pbar.update(1)
                            
                            # 워커가 작업을 완료하면 즉시 다음 작업 제출 (GPU 유휴 시간 최소화)
                            try:
                                next_args = next(work_queue)
                                next_future = executor.submit(upscale_single_frame, next_args)
                                future_to_frame[next_future] = next_args[0]
                                pending_count += 1
                            except StopIteration:
                                # 더 이상 제출할 작업이 없음
                                pass
                            
                        except Exception as e:
                            failed_frames.append({
                                'frame': frame_file,
                                'returncode': -1,
                                'stderr': str(e)
                            })
                            print(f"\n❌ 프레임 {frame_file} 처리 중 예외 발생: {e}")
                            pbar.update(1)
                            
                            # 예외가 발생해도 다음 작업 제출 시도
                            try:
                                next_args = next(work_queue)
                                next_future = executor.submit(upscale_single_frame, next_args)
                                future_to_frame[next_future] = next_args[0]
                                pending_count += 1
                            except StopIteration:
                                pass
                        
                        # 한 번에 하나씩 처리하므로 break
                        break
        
        # 실패한 프레임이 있으면 에러 발생
        if failed_frames:
            error_msg = f"{len(failed_frames)}개의 프레임 업스케일링 실패:\n"
            for fail in failed_frames[:5]:  # 최대 5개만 표시
                error_msg += f"  - {fail['frame']}: {fail['stderr'][:100]}\n"
            if len(failed_frames) > 5:
                error_msg += f"  ... 외 {len(failed_frames) - 5}개\n"
            raise Exception(error_msg)
        
        final_count = len([f for f in os.listdir(UPSCALED_DIR) if f.endswith('.png')])
        if final_count < len(frame_files):
            print(f"\n⚠️ 경고: 예상 {len(frame_files)}개 프레임 중 {final_count}개만 생성되었습니다.")

        # 업스케일링 작업 완료 시간 계산 및 표시
        upscale_end_time = time.time()
        upscale_elapsed = upscale_end_time - upscale_start_time
        hours = int(upscale_elapsed // 3600)
        minutes = int((upscale_elapsed % 3600) // 60)
        seconds = int(upscale_elapsed % 60)
        
        if hours > 0:
            time_str = f"{hours}시간 {minutes}분 {seconds}초"
        elif minutes > 0:
            time_str = f"{minutes}분 {seconds}초"
        else:
            time_str = f"{seconds}초"
        
        print(f"\n⏱️ 업스케일링 작업 완료: {time_str} ({upscale_elapsed:.2f}초)")
        if len(frame_files) > 0:
            avg_time_per_frame = upscale_elapsed / len(frame_files)
            print(f"   평균 프레임당 처리 시간: {avg_time_per_frame:.2f}초")

        # 6. 최종 합성 (GPU 가속 사용)
        print(f"\n[3/3] 🎬 영상 합성 및 인코딩 중 (Encoder: {VIDEO_ENCODER})...")
        output_name = build_output_filename(selected_video, res_name, model_index)
        
        # 인코더별 추가 파라미터 설정
        encoder_params = ""
        if VIDEO_ENCODER == "h264_amf":
            # AMD AMF 인코더에 적절한 파라미터 추가
            encoder_params = "-quality speed -rc cqp -qp_i 23 -qp_p 23"
        elif VIDEO_ENCODER == "h264_nvenc":
            # NVIDIA NVENC 인코더에 적절한 파라미터 추가 (선택사항)
            encoder_params = "-preset fast"
        
        merge_cmd = (
            f'ffmpeg -y -framerate {fps} -i "{UPSCALED_DIR}/frame_%05d.png" -i "{selected_video}" '
            f'-vf "scale={final_width}:{final_height}:flags=lanczos" '
            f'-c:v {VIDEO_ENCODER} {encoder_params} -pix_fmt yuv420p -c:a copy -map 0:v:0 -map 1:a:0? "{output_name}"'
        )
        subprocess.run(merge_cmd, shell=True, check=True)

        print(f"\n✅ 성공! 결과물: {output_name}")

    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
    finally:
        # 7. 마무리 정리 (자동으로 임시 파일 삭제)
        cleanup()

if __name__ == "__main__":
    # Windows에서 multiprocessing을 사용할 때 필요
    multiprocessing.freeze_support()
    
    # 명령줄 인자 파싱 및 디버그 모드 설정
    args = parse_arguments()
    DEBUG_MODE = args.debug
    
    if DEBUG_MODE:
        print("🐛 디버그 모드가 활성화되었습니다.\n")
    
    # FFmpeg 확인
    ffmpeg_available, ffmpeg_path = check_ffmpeg()
    if not ffmpeg_available:
        print("⚠️ FFmpeg을 찾을 수 없습니다.")
        print("   FFmpeg이 설치되어 있고 PATH 환경 변수에 등록되어 있는지 확인하세요.")
        print("   확인 방법: 터미널에서 'ffmpeg -version' 입력")
        exit(1)
    else:
        print(f"1. 🎬 FFmpeg: {ffmpeg_path}")
    
    # Upscayl 경로 표시
    print(f"2. 🖼️ Upscayl: {UPSCAYL_PATH}")
    if MODEL_PATH and os.path.exists(MODEL_PATH):
        print(f"3. 📦 모델 경로: {MODEL_PATH}")
    
    # 비디오 인코더 감지 (메인 프로세스에서만 실행)
    VIDEO_ENCODER = detect_video_encoder()
    
    encoder_info = {
        'h264_nvenc': '(NVIDIA GPU 가속)',
        'h264_amf': '(AMD GPU 가속)',
        'libx264': '(CPU 인코딩)'
    }
    print(f"4. 📹 비디오 인코더: {VIDEO_ENCODER} {encoder_info.get(VIDEO_ENCODER, '(알 수 없음)')}") 
    
    # 전역 변수 업데이트 (워커 프로세스에서도 접근 가능하도록)
    globals()['VIDEO_ENCODER'] = VIDEO_ENCODER
    globals()['ffmpeg_path'] = ffmpeg_path
    
    run_upscale()