# ==============================================================================
# 🚀 Infinity Swarm Studio: GitHub Actions 无头无弹窗版 worker.py
# 核心: Rclone挂载5TB云盘 + 12线程并发音画素材 + 自动抢单分布式渲染
# ==============================================================================
import os, sys, json, time, random, subprocess, shutil, textwrap, re, warnings, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO

warnings.filterwarnings("ignore")
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

print("⚡ [GitHub Worker Node] 正在初始化无头超音速集群节点...")
subprocess.run("apt-get update -qq && apt-get install -y -qq fonts-noto-cjk ffmpeg", shell=True)
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "diffusers", "transformers", "accelerate", "edge-tts", "pillow", "google-genai", "torch"], stdout=subprocess.DEVNULL)

import torch
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from google import genai
from google.genai import types

WORKER_ID = os.environ.get("WORKER_NODE_ID", f"GH_Node_{random.randint(100,999)}")
print(f"🖥️ [节点就绪] 当前工作节点代号: {WORKER_ID}")

# ==============================================================================
# 1. 路径与云盘挂载
# ==============================================================================
DRIVE_ROOT = "/content/drive/MyDrive/Infinity_Feature_Film_5TB"
LOCAL_RAM_DISK = "/dev/shm/hypersonic_studio" if os.path.exists("/dev/shm") else "./local_fast_studio_tmp"

for d in ["01_stills", "02_audio", "03_shots", "beds", "tmp"]:
    os.makedirs(f"{LOCAL_RAM_DISK}/{d}", exist_ok=True)
for d in ["00_bible_and_ledger", "04_chapter_reels", "05_2Hour_Master_Movies", "locks"]:
    os.makedirs(f"{DRIVE_ROOT}/{d}", exist_ok=True)

BIBLE_FILE = f"{DRIVE_ROOT}/00_bible_and_ledger/active_feature_film_bible.json"
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
if not os.path.exists(FONT_PATH):
    FONT_PATH = "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"

TOTAL_CHAPTERS = 24

# ==============================================================================
# 2. 预烘焙母带音轨与大模型初始化
# ==============================================================================
BED_FILES = {}
for mood_type, filter_str in [
    ("comedy", "anoisesrc=d=25:c=pink:a=0.012,lowpass=f=350[n];sine=f=220:d=25,volume=0.02,tremolo=f=3.8:d=0.5[s];[n][s]amix=inputs=2"),
    ("suspense", "anoisesrc=d=25:c=brown:a=0.02,lowpass=f=200[n];sine=f=55:d=25,volume=0.06,tremolo=f=1.4:d=0.8[s];[n][s]amix=inputs=2"),
    ("emotional", "anoisesrc=d=25:c=brown:a=0.015,lowpass=f=240[n];sine=f=110:d=25,volume=0.04[s1];sine=f=165:d=25,volume=0.03[s2];[n][s1][s2]amix=inputs=3")
]:
    bed_path = f"{LOCAL_RAM_DISK}/beds/bed_{mood_type}.wav"
    if not os.path.exists(bed_path):
        subprocess.run(f'ffmpeg -y -f lavfi -i "{filter_str}" -ar 44100 -ac 1 "{bed_path}"', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    BED_FILES[mood_type] = bed_path

API_KEY = os.environ.get('GEMINI_API_KEY')
client = genai.Client(api_key=API_KEY)
ACTIVE_MODEL = 'gemini-2.5-flash'

def quota_safe_call(prompt, use_search=False):
    for attempt in range(3):
        try:
            cfg = types.GenerateContentConfig(temperature=0.83, http_options={'timeout': 40000})
            if use_search and attempt == 0: cfg.tools = [{"google_search": {}}]
            res = client.models.generate_content(model=ACTIVE_MODEL, contents=prompt, config=cfg)
            if res and res.text: return res.text.strip()
        except Exception:
            time.sleep(5 * (attempt + 1))
    return ""

def is_valid_media(path):
    return os.path.exists(path) and os.path.getsize(path) > 1024

def get_media_duration(path):
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', path]
    try: return float(subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8).stdout.strip())
    except: return 0.0

def save_bible(bible):
    try:
        tmp = f"{LOCAL_RAM_DISK}/tmp/bible.tmp.json"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(bible, f, indent=2, ensure_ascii=False)
        shutil.copy2(tmp, BIBLE_FILE)
    except: pass

# ==============================================================================
# 3. 业务逻辑核心
# ==============================================================================
def load_or_create_film_bible():
    if os.path.exists(BIBLE_FILE):
        try:
            with open(BIBLE_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except: pass

    raw = quota_safe_call("策划一部120分钟(24章)电影总纲，输出纯JSON：{\"title\":\"四字片名\",\"character_anchor_M\":\"man\",\"character_anchor_F\":\"woman\",\"chapters\":[{\"chapter_num\":1,\"chapter_title\":\"四字标题\",\"summary\":\"核心剧情\",\"mood\":\"comedy\"}]}", use_search=True)
    try: bible = json.loads(raw[raw.find('{'):raw.rfind('}')+1])
    except: bible = {"title": "草台英雄", "character_anchor_M": "man", "character_anchor_F": "woman", "chapters": []}

    while len(bible.get("chapters", [])) < TOTAL_CHAPTERS:
        idx = len(bible["chapters"]) + 1
        bible["chapters"].append({"chapter_num": idx, "chapter_title": f"命运·第{idx}幕", "summary": "荒诞误会中见真情。", "mood": "comedy" if idx <= 8 else "emotional"})
    bible["chapters"] = bible["chapters"][:TOTAL_CHAPTERS]
    bible["film_id"] = str(int(time.time()))
    bible["master_seed"] = random.randint(100000, 999999)
    for ch in bible["chapters"]:
        ch["status"] = "pending"
        ch["shots"] = []
    save_bible(bible)
    return bible

def batch_script_chapters(bible, start_ch_idx):
    ch = bible["chapters"][start_ch_idx]
    if ch.get("shots") and len(ch["shots"]) >= 10: return ch["shots"]
    
    raw = quota_safe_call(f"电影《{bible['title']}》第 {ch['chapter_num']}/24 章，剧情：{ch['summary']}。输出24个分镜纯JSON：{{\"shots\":[{{\"speaker\":\"M\",\"mood\":\"comedy\",\"camera\":\"zoom_in\",\"dialogue\":\"40-58字台词无引号\",\"prompt\":\"under 20 words\"}}]}}")
    shots_data = []
    try: shots_data = json.loads(raw[raw.find('{'):raw.rfind('}')+1]).get("shots", [])
    except: pass

    formatted = []
    for s_idx in range(24):
        s = shots_data[s_idx] if s_idx < len(shots_data) else {}
        formatted.append({
            "shot_id": f"{bible['film_id']}_ch{ch['chapter_num']:02d}_s{s_idx:02d}",
            "speaker": s.get("speaker", "M"), "mood": s.get("mood", ch.get("mood", "comedy")),
            "camera": s.get("camera", "zoom_in"), "dialogue": str(s.get("dialogue", "生活不易，笑对人生。")).replace('"', ''),
            "prompt": str(s.get("prompt", "cinematic shot")), "duration": 12.5, "status": "pending"
        })
    ch["shots"] = formatted
    ch["status"] = "scripted"
    save_bible(bible)
    return formatted

def bake_subtitle(img_obj, text, title, ch_num, ch_title, mood):
    img = img_obj.convert("RGBA")
    overlay = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for y in range(1220, 1920):
        draw.line([(0, y), (1080, y)], fill=(0, 0, 0, int(235 * ((y - 1220) / 700))))
    try: font_sub = ImageFont.truetype(FONT_PATH, 45); font_badge = ImageFont.truetype(FONT_PATH, 28)
    except: font_sub = font_badge = ImageFont.load_default()

    draw.text((40, 60), f"🎬 《{title}》 | 第 {ch_num:02d}/24 章：{ch_title}", font=font_badge, fill=(255, 240, 180, 200), stroke_width=2, stroke_fill=(0, 0, 0, 220))
    for i, line in enumerate(textwrap.wrap(text, width=15)[:4]):
        draw.text(((1080 - draw.textbbox((0,0), line, font=font_sub)[2])/2, 1380 + i*62), line, font=font_sub, fill=(255, 230, 65, 255) if mood=="comedy" else (255, 245, 225, 255), stroke_width=4, stroke_fill=(0,0,0,255))
    return Image.alpha_composite(img, overlay).convert("RGB")

def prepare_shot(shot, bible, ch_num, ch_title):
    sid = shot["shot_id"]
    text = shot.get("dialogue", "")
    mood = shot.get("mood", "comedy")
    voice = "zh-CN-YunxiNeural" if shot.get("speaker")=="M" else "zh-CN-XiaoxiaoNeural"
    
    aud_path = f"{LOCAL_RAM_DISK}/02_audio/{sid}.wav"
    tmp_tts = f"{LOCAL_RAM_DISK}/tmp/{sid}_tts.mp3"
    subprocess.run(f'edge-tts --text "{text}" --voice "{voice}" --rate="+9%" --write-media "{tmp_tts}"', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tts_dur = get_media_duration(tmp_tts) if os.path.exists(tmp_tts) else 0.0
    dur = max(12.0, round(tts_dur + 1.8, 2)) if tts_dur > 1.0 else 12.5
    shot["duration"] = dur

    bed = BED_FILES.get(mood, BED_FILES["comedy"])
    if tts_dur > 0.5:
        subprocess.run(f'ffmpeg -y -i "{tmp_tts}" -i "{bed}" -filter_complex "[0:a]apad=whole_dur={dur}[v];[v][1:a]amix=inputs=2:duration=first" -ar 44100 -ac 1 -t {dur} "{aud_path}"', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try: os.remove(tmp_tts)
        except: pass
    if not is_valid_media(aud_path): shutil.copy2(bed, aud_path)

    img_path = f"{LOCAL_RAM_DISK}/01_stills/{sid}.jpg"
    if not os.path.exists(img_path):
        prompt = f"vertical 9:16, photorealistic, {shot.get('prompt', '')}"
        try:
            url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}?width=540&height=960&seed={bible.get('master_seed',42)+int(sid.split('_s')[-1])}&nologo=true&model=flux"
            raw = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=12).read()
            img_obj = Image.open(BytesIO(raw)).convert("RGB").resize((1080, 1920), Image.Resampling.BILINEAR)
        except:
            img_obj = Image.new("RGB", (1080, 1920), (18, 22, 32))
        bake_subtitle(img_obj, text, bible["title"], ch_num, ch_title, mood).save(img_path, "JPEG", quality=88)

    return sid, img_path, aud_path, dur

def encode_shot(shot):
    sid = shot["shot_id"]
    target = f"{LOCAL_RAM_DISK}/03_shots/{sid}.mp4"
    if is_valid_media(target): return target
    
    img, aud, dur = f"{LOCAL_RAM_DISK}/01_stills/{sid}.jpg", f"{LOCAL_RAM_DISK}/02_audio/{sid}.wav", shot.get("duration", 12.5)
    zp = f"zoompan=z='min(1.0+on/{int(dur*15)}*0.14,1.14)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(dur*15)}:s=1080x1920:fps=15"
    subprocess.run(f'ffmpeg -y -loop 1 -i "{img}" -i "{aud}" -t {dur} -vf "{zp}" -c:v libx264 -preset ultrafast -crf 24 -threads 2 -c:a aac -b:a 96k -pix_fmt yuv420p -shortest "{target}"', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return target

# ==============================================================================
# 4. 主循环：多节点分布式抢单
# ==============================================================================
def run_worker():
    bible = load_or_create_film_bible()
    film_id, title = bible["film_id"], bible["title"]

    for ch_idx, ch in enumerate(bible["chapters"]):
        ch_num = ch["chapter_num"]
        ch_reel = f"{DRIVE_ROOT}/04_chapter_reels/{film_id}_Chapter_{ch_num:02d}_{ch['chapter_title']}.mp4"
        lock = f"{DRIVE_ROOT}/locks/{film_id}_ch{ch_num:02d}.lock"

        if os.path.exists(ch_reel) and os.path.getsize(ch_reel) > 300000: continue
        if os.path.exists(lock) and (time.time() - os.path.getmtime(lock) < 900): continue

        with open(lock, "w") as f: f.write(WORKER_ID)
        shots = batch_script_chapters(bible, ch_idx)

        with ThreadPoolExecutor(max_workers=12) as p:
            list(p.map(lambda s: prepare_shot(s, bible, ch_num, ch["chapter_title"]), shots))
        with ThreadPoolExecutor(max_workers=2) as p:
            list(p.map(encode_shot, shots))

        clips = [f"{LOCAL_RAM_DISK}/03_shots/{s['shot_id']}.mp4" for s in shots if is_valid_media(f"{LOCAL_RAM_DISK}/03_shots/{s['shot_id']}.mp4")]
        concat = f"{LOCAL_RAM_DISK}/tmp/concat_{ch_num}.txt"
        with open(concat, "w") as f:
            for c in clips: f.write(f"file '{os.path.abspath(c)}'\n")
        
        local_reel = f"{LOCAL_RAM_DISK}/tmp/Ch_{ch_num}.mp4"
        subprocess.run(f'ffmpeg -y -f concat -safe 0 -i "{concat}" -c copy "{local_reel}"', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if is_valid_media(local_reel):
            shutil.copy2(local_reel, ch_reel)
            ch["status"] = "rendered"
            save_bible(bible)
            try: os.remove(lock)
            except: pass
            print(f"✅ [{WORKER_ID}] 成功抢单并拍完第 {ch_num:02d} 章！")

if __name__ == "__main__":
    run_worker()
