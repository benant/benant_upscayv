import os
import subprocess
import json
import shutil
import time
from pathlib import Path
from tqdm import tqdm

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
        else:
            # NVIDIA NVENC는 작은 해상도도 가능
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
                print(f"  [디버그] {encoder_name} 테스트 실패 (returncode: {test_result.returncode}):")
                # stderr에서 실제 에러 부분만 추출 (Input 정보 제외)
                error_lines = [line for line in test_result.stderr.split('\n') 
                             if any(keyword in line.lower() for keyword in ['error', 'failed', 'cannot', 'not found', 'unable'])]
                if error_lines:
                    for line in error_lines[:5]:  # 최대 5줄만
                        print(f"    {line}")
                else:
                    # 에러 라인이 없으면 마지막 부분 출력
                    print(f"    {test_result.stderr[-500:]}")
            else:
                print(f"  [디버그] {encoder_name} 테스트 성공!")
        
        # 성공했고 (returncode == 0), 에러 메시지에 관련 에러가 없어야 사용 가능
        if test_result.returncode == 0 and not any(err in test_result.stderr for err in error_keywords):
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        if debug:
            print(f"  [디버그] {encoder_name} 테스트 예외: {e}")
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
            return "libx264"
        
        # 1. NVIDIA GPU (h264_nvenc) 확인
        if 'h264_nvenc' in result.stdout:
            nvenc_errors = [
                'No NVENC capable devices found',
                'No capable devices found',
                'NVENC not available',
                'Cannot load',
                'No such filter'
            ]
            if test_encoder('h264_nvenc', nvenc_errors):
                return "h264_nvenc"
        
        # 2. AMD GPU (h264_amf) 확인
        if 'h264_amf' in result.stdout:
            amf_errors = [
                'No capable devices found',
                'AMF not available',
                'Cannot load',
                'No such filter',
                'Failed to initialize',
                'AMF runtime'
            ]
            # AMD iGPU도 지원하므로 테스트 (디버깅 모드 활성화)
            if test_encoder('h264_amf', amf_errors, debug=True):
                return "h264_amf"
            else:
                # AMD 인코더가 있지만 테스트 실패 - 디버깅 정보 출력
                print("  [정보] AMD GPU 인코더(h264_amf)가 감지되었지만 초기화에 실패했습니다.")
                print("  [정보] 드라이버가 최신인지 확인하거나, CPU 인코딩을 사용합니다.")
        
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass
    
    # 3. GPU 인코더를 사용할 수 없으면 CPU 인코더 사용
    return "libx264"

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

TEMP_DIR = "temp_frames"
UPSCALED_DIR = "upscaled_frames"

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

VIDEO_ENCODER = detect_video_encoder()

encoder_info = {
    'h264_nvenc': '(NVIDIA GPU 가속)',
    'h264_amf': '(AMD GPU 가속)',
    'libx264': '(CPU 인코딩)'
}
print(f"4. 📹 비디오 인코더: {VIDEO_ENCODER} {encoder_info.get(VIDEO_ENCODER, '(알 수 없음)')}") 

RES_OPTIONS = {
    "1": ("HD", 1280, 720),
    "2": ("FHD", 1920, 1080),
    "3": ("4K", 3840, 2160),
    "4": ("8K", 7680, 4320)
}

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
    video_files = [f for f in os.listdir('.') if f.endswith('.mp4') and not f.startswith('output_')]
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
    
    # 목표 해상도 선택 메뉴 생성
    res_menu = ", ".join([f"{key}:{name}({w}x{h})" for key, (name, w, h) in RES_OPTIONS.items()])
    res_name, target_w, target_h = RES_OPTIONS.get(input(f"7. 목표 해상도 ({res_menu}): "), RES_OPTIONS["2"])
    scale_factor = 4 if target_w / width > 2 else 2

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
        
        # Upscayl 명령어: 폴더 전체를 배치로 처리
        # 각 파일에 대해 절대 경로 + 파일명으로 출력 지정
        print(f"\n[디버그] 입력 폴더: {input_dir_abs}")
        print(f"[디버그] 출력 폴더: {output_dir_abs}")
        print(f"[디버그] 모델: {selected_model}")
        print(f"[디버그] 스케일: {scale_factor}x")
        
        # 환경 변수에 ffmpeg 경로 추가
        env = os.environ.copy()
        if ffmpeg_path:
            ffmpeg_dir = os.path.dirname(ffmpeg_path)
            current_path = env.get('PATH', '')
            if ffmpeg_dir not in current_path:
                env['PATH'] = f"{ffmpeg_dir};{current_path}"
        
        # 각 파일을 개별적으로 처리하되, 출력은 절대 경로 + 파일명으로 지정
        with tqdm(total=len(frame_files), desc="Upscaling", unit="frame") as pbar:
            for idx, frame_file in enumerate(frame_files):
                input_path = os.path.join(input_dir_abs, frame_file)
                output_path = os.path.join(output_dir_abs, frame_file)
                
                # Upscayl 명령어: 절대 경로 + 파일명으로 출력 지정
                upscale_cmd = f'"{UPSCAYL_PATH}" -i "{input_path}" -o "{output_path}" -s {scale_factor} -m "{model_path_abs}" -n {selected_model}'
                
                # 첫 번째 프레임 처리 시 명령어 출력
                if idx == 0:
                    print(f"\n[디버그] Upscayl 명령어 예시: {upscale_cmd}")
                
                # Upscayl 실행
                result = subprocess.run(
                    upscale_cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    cwd=os.getcwd()
                )
                
                # bytes를 텍스트로 변환 (인코딩 오류 무시)
                try:
                    stdout_text = result.stdout.decode('utf-8', errors='ignore') if result.stdout else ''
                    stderr_text = result.stderr.decode('utf-8', errors='ignore') if result.stderr else ''
                except Exception:
                    stdout_text = result.stdout.decode('cp949', errors='ignore') if result.stdout else ''
                    stderr_text = result.stderr.decode('cp949', errors='ignore') if result.stderr else ''
                
                # 첫 번째 프레임 처리 시 상세 출력
                if idx == 0:
                    print(f"[디버그] 종료 코드: {result.returncode}")
                    if stderr_text:
                        print(f"[디버그] stderr:\n{stderr_text[:500]}")
                
                if result.returncode != 0:
                    print(f"\n❌ 프레임 {frame_file} 업스케일링 실패 (종료 코드: {result.returncode})")
                    if stderr_text:
                        print(f"에러: {stderr_text[-300:]}")
                    raise Exception(f"프레임 {frame_file} 업스케일링 실패: {stderr_text[-200:] if stderr_text else '알 수 없는 오류'}")
                
                # 출력 파일 확인
                if not os.path.exists(output_path):
                    raise Exception(f"업스케일된 파일이 생성되지 않았습니다: {output_path}")
                
                pbar.update(1)
        
        final_count = len([f for f in os.listdir(UPSCALED_DIR) if f.endswith('.png')])
        if final_count < len(frame_files):
            print(f"\n⚠️ 경고: 예상 {len(frame_files)}개 프레임 중 {final_count}개만 생성되었습니다.")

        # 6. 최종 합성 (GPU 가속 사용)
        print(f"\n[3/3] 🎬 영상 합성 및 인코딩 중 (Encoder: {VIDEO_ENCODER})...")
        output_name = f"output_{res_name}_{selected_video}"
        
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
            f'-vf "scale={target_w}:{target_h}:flags=lanczos" '
            f'-c:v {VIDEO_ENCODER} {encoder_params} -pix_fmt yuv420p -c:a copy -map 0:v:0 -map 1:a:0? "{output_name}"'
        )
        subprocess.run(merge_cmd, shell=True, check=True)

        print(f"\n✅ 성공! 결과물: {output_name}")

    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
    finally:
        # 7. 마무리 정리
        confirm = input("\n임시 파일을 삭제하시겠습니까? (y/n): ")
        if confirm.lower() == 'y': cleanup()

if __name__ == "__main__":
    run_upscale()