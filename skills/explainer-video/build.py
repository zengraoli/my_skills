#!/usr/bin/env python3
"""explainer-video 构建脚本：TTS 配音 + 逐步截帧 + ffmpeg 合成 mp4

用法：
  python build.py --check                                  # 只检查环境（Chrome / ffmpeg / .env / TTS 鉴权），不合成
  python build.py --slides xxx.html --scenes xxx.scenes.json      # 输出到 ./output/xxx.mp4，中间产物在 ./output/_build/xxx/
  python build.py ... --out d:/path/xxx.mp4                # 指定输出文件
  python build.py ... --voice male_0029_a                  # 临时换音色
  python build.py ... --aspect portrait --gap 0.6          # 竖屏 1080x1920；场景之间停顿 0.6 秒

配置放在本脚本同目录的 .env：SENSEAUDIO_API_KEY、TTS_BASE_URL、TTS_MODEL、TTS_VOICE、TTS_SPEED、CHROME_PATH、FFMPEG_DIR。
优先级：命令行参数 > 系统环境变量 > .env > 内置默认值。

scenes.json 格式：[{"steps": 6, "narration": "第一场旁白……"}, {"steps": 8, "narration": "……"}]
steps = 该场景 data-step 的最大值（画面会截 k=0..steps 共 steps+1 帧）

外部依赖：Chrome/Chromium/Edge 浏览器、ffmpeg + ffprobe、Python 包 requests。
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("缺少 Python 包 requests：请执行  pip install requests")

SKILL_DIR = Path(__file__).resolve().parent
OS = platform.system()

DEFAULTS = {
    "TTS_BASE_URL": "https://api.senseaudio.cn/v1",
    "TTS_MODEL": "sensenova-tts-2.0",
    "TTS_VOICE": "female_0038_b",
    "TTS_SPEED": "1.05",
}

# 各系统上 Chrome 系浏览器的常见安装位置；没设 CHROME_PATH 时按顺序探测，找到第一个能用的
CHROME_CANDIDATES = {
    "Windows": [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    ],
    "Darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
    "Linux": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"],
}

# 缺依赖时给用户的安装提示（按系统）
HINTS = {
    "chrome": {
        "Windows": "安装 Chrome（https://www.google.com/chrome/）或 Edge；装在非默认位置时在 .env 里设 CHROME_PATH=完整路径\\chrome.exe",
        "Darwin": "brew install --cask google-chrome，或在 .env 里设 CHROME_PATH",
        "Linux": "sudo apt install chromium（或 google-chrome-stable），或在 .env 里设 CHROME_PATH",
    },
    "ffmpeg": {
        "Windows": "winget install Gyan.FFmpeg，或到 https://www.gyan.dev/ffmpeg/builds/ 下载 zip 解压，把 bin 目录加入 PATH 或在 .env 里设 FFMPEG_DIR=解压后的 bin 目录",
        "Darwin": "brew install ffmpeg",
        "Linux": "sudo apt install ffmpeg",
    },
    "fonts": {
        "Linux": "sudo apt install fonts-noto-cjk fonts-noto-color-emoji（否则截图里中文/emoji 显示为方块）",
    },
}

# 输出画幅：幻灯片固定 1180:658，竖屏时上下留米色边
ASPECTS = {
    "landscape": "scale=1920:1070:flags=lanczos,pad=1920:1080:0:5:color=0xfdf6ec,format=yuv420p",
    "portrait": "scale=1080:-2:flags=lanczos,pad=1080:1920:0:(oh-ih)/2:color=0xfdf6ec,format=yuv420p",
}


def hint(item):
    return HINTS.get(item, {}).get(OS, "")


def load_env(path):
    """读取 KEY=VALUE 格式的 .env（支持 # 注释、可选引号）；已存在的系统环境变量优先，不被覆盖。"""
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip().strip('"').strip("'")
        if val:
            os.environ.setdefault(key.strip(), val)
    return True


def cfg(name):
    return os.environ.get(name) or DEFAULTS.get(name)


def find_chrome(explicit):
    """返回 Chrome 可执行文件路径，找不到返回 None。"""
    for cand in [explicit, os.environ.get("CHROME_PATH")]:
        if cand:
            return cand if os.path.isfile(cand) else None
    for cand in CHROME_CANDIDATES.get(OS, []):
        p = os.path.expandvars(cand)
        if os.path.isfile(p):
            return p
        found = shutil.which(p)
        if found:
            return found
    return None


def find_tool(name, ffmpeg_dir):
    """返回 ffmpeg/ffprobe 路径，找不到返回 None。"""
    exe = name + (".exe" if os.name == "nt" else "")
    if ffmpeg_dir:
        d = Path(ffmpeg_dir.strip().strip('"'))
        if d.is_file():  # 用户填的是 ffmpeg.exe 的完整路径，取它所在目录
            d = d.parent
        if (d / exe).is_file():
            return str(d / exe)
    return shutil.which(name)


def run(cmd):
    return subprocess.run(cmd, check=True, text=True, capture_output=True, encoding="utf-8", errors="replace")


def probe(ffprobe, path, entries, stream=False):
    sel = ["-select_streams", "a:0"] if stream else []
    return run([ffprobe, "-v", "error", *sel, "-show_entries", entries, "-of", "csv=p=0", str(path)]).stdout.strip()


def version_line(exe, flag="-version"):
    try:
        out = subprocess.run([exe, flag], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        return (out.stdout or out.stderr).strip().splitlines()[0][:80]
    except Exception:
        return ""


def check_env(args):
    """环境自检：逐项打印 OK/缺失和修复提示，有缺失时退出码 1。"""
    problems = []

    def row(ok, name, detail, fix=""):
        print(f"  [{' OK ' if ok else '缺失'}] {name:<14}{detail}")
        if not ok:
            problems.append(f"{name}：{fix}")

    print(f"环境检查（{OS} / Python {platform.python_version()}）")
    row(sys.version_info >= (3, 9), "Python", platform.python_version(), "需要 Python 3.9 以上")
    row(True, "requests", requests.__version__)

    env_path = Path(args.env)
    env_ok = load_env(env_path)
    row(env_ok, ".env", str(env_path) if env_ok else f"未找到 {env_path}",
        f"在 {SKILL_DIR} 下创建 .env，内容格式见 README，至少填 SENSEAUDIO_API_KEY")

    chrome = find_chrome(args.chrome)
    row(bool(chrome), "Chrome", chrome or "未找到（探测了 CHROME_PATH 和常见安装位置）", hint("chrome"))

    ffmpeg_dir = args.ffmpeg_dir or os.environ.get("FFMPEG_DIR")
    for tool in ("ffmpeg", "ffprobe"):
        p = find_tool(tool, ffmpeg_dir)
        row(bool(p), tool, f"{p}  ({version_line(p)})" if p else "未找到（PATH 和 FFMPEG_DIR 里都没有）", hint("ffmpeg"))

    if OS == "Linux" and shutil.which("fc-list"):
        cjk = subprocess.run(["fc-list", ":lang=zh"], capture_output=True, text=True).stdout.strip()
        emoji = subprocess.run(["fc-list"], capture_output=True, text=True).stdout.lower()
        row(bool(cjk), "中文字体", "已安装" if cjk else "未安装", hint("fonts"))
        row("emoji" in emoji, "emoji 字体", "已安装" if "emoji" in emoji else "未安装", hint("fonts"))

    key = args.api_key or os.environ.get("SENSEAUDIO_API_KEY")
    base_url = (args.base_url or cfg("TTS_BASE_URL")).rstrip("/")
    if not key:
        row(False, "TTS key", "SENSEAUDIO_API_KEY 为空", "在 .env 里填入 SENSEAUDIO_API_KEY（https://senseaudio.cn 申请）")
    else:
        try:
            r = requests.post(f"{base_url}/get_voice", headers={"Authorization": f"Bearer {key}"},
                              json={"voice_type": "system"}, timeout=20)
            body = r.json()
            ok = r.status_code == 200 and body.get("base_resp", {}).get("status_code") == 0
            detail = f"鉴权通过，账号可见系统音色 {len(body.get('system_voice') or [])} 个" if ok \
                else f"HTTP {r.status_code}：{body.get('base_resp', {}).get('status_msg') or r.text[:120]}"
            row(ok, "TTS key", detail, "检查 .env 里的 SENSEAUDIO_API_KEY 是否正确、账号是否有额度")
        except Exception as e:
            row(False, "TTS 接口", f"无法连接 {base_url}：{e}", "检查网络 / 代理，或 .env 里的 TTS_BASE_URL")
    voice = args.voice or cfg("TTS_VOICE")
    print(f"  [--] 配置          voice={voice} model={args.model or cfg('TTS_MODEL')} speed={args.speed or cfg('TTS_SPEED')}")

    if problems:
        print("\n需要处理：")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("\n环境正常，可以开始合成。")


def tts_senseaudio(args, text, out_path):
    # SenseAudio 自有接口 t2a_v2，音频以 hex 字符串返回
    resp = requests.post(f"{args.base_url}/t2a_v2", headers={"Authorization": f"Bearer {args.api_key}"},
                         json={"model": args.model, "text": text, "stream": False,
                               "voice_setting": {"voice_id": args.voice, "speed": args.speed},
                               "audio_setting": {"format": "mp3", "sample_rate": 32000, "channel": 1}}, timeout=180)
    try:
        body = resp.json()
    except ValueError:
        sys.exit(f"TTS 失败（HTTP {resp.status_code}），服务器返回：{resp.text[:500]}")
    audio = body.get("data", {}).get("audio") if resp.status_code == 200 else None
    if not audio:
        sys.exit(f"TTS 失败（HTTP {resp.status_code}），服务器返回：{json.dumps(body, ensure_ascii=False)[:500]}")
    out_path.write_bytes(bytes.fromhex(audio))


def make_silence(ffmpeg, ffprobe, ref_mp3, seconds, out_path):
    """按参考配音的采样率/声道生成一段静音 mp3，供场景间停顿用（concat 要求编码参数一致）。"""
    sr, ch = (probe(ffprobe, ref_mp3, "stream=sample_rate,channels", stream=True).split(",") + ["1"])[:2]
    layout = "stereo" if ch.strip() == "2" else "mono"
    run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", f"anullsrc=r={sr}:cl={layout}",
         "-t", f"{seconds}", "-c:a", "libmp3lame", "-q:a", "9", str(out_path)])


def main():
    ap = argparse.ArgumentParser(description="讲解视频构建：TTS + 截帧 + ffmpeg 合成（配置见同目录 .env）")
    ap.add_argument("--check", action="store_true", help="只做环境检查，不合成")
    ap.add_argument("--slides", help="幻灯片 HTML 路径")
    ap.add_argument("--scenes", help="scenes.json 路径")
    ap.add_argument("--out", help="输出 mp4 路径（默认 <当前目录>/output/<幻灯片文件名>.mp4）")
    ap.add_argument("--out-dir", default="output", help="不指定 --out 时的输出目录（默认当前目录下的 output）")
    ap.add_argument("--env", default=str(SKILL_DIR / ".env"), help="配置文件路径（默认脚本同目录 .env）")
    ap.add_argument("--voice", default=None, help="音色 id（默认取 .env 的 TTS_VOICE）")
    ap.add_argument("--speed", type=float, default=None, help="语速倍率 0.5~2.0（默认取 .env 的 TTS_SPEED）")
    ap.add_argument("--model", default=None, help="TTS 模型名（默认取 .env 的 TTS_MODEL）")
    ap.add_argument("--base-url", default=None, help="TTS 接口地址（默认取 .env 的 TTS_BASE_URL）")
    ap.add_argument("--api-key", default=None, help="TTS key（默认取 .env 的 SENSEAUDIO_API_KEY）")
    ap.add_argument("--aspect", default="landscape", choices=list(ASPECTS), help="横屏 1920x1080 / 竖屏 1080x1920")
    ap.add_argument("--gap", type=float, default=0.0, help="场景之间的停顿秒数（默认 0）")
    ap.add_argument("--chrome", default=None, help="Chrome 路径（默认自动探测 / .env 的 CHROME_PATH）")
    ap.add_argument("--ffmpeg-dir", default=None, help="ffmpeg 目录（默认走 PATH / .env 的 FFMPEG_DIR）")
    ap.add_argument("--window-size", default="2360,1316", help="截帧窗口（默认 2 倍分辨率）")
    ap.add_argument("--work-dir", default=None, help="中间产物目录（默认 <输出目录>/_build/<幻灯片文件名>）")
    args = ap.parse_args()

    if args.check:
        check_env(args)
        return
    if not (args.slides and args.scenes):
        ap.error("合成需要 --slides 和 --scenes；只检查环境请用 --check")

    load_env(Path(args.env))
    args.voice = args.voice or cfg("TTS_VOICE")
    args.speed = args.speed if args.speed is not None else float(cfg("TTS_SPEED"))
    args.model = args.model or cfg("TTS_MODEL")
    args.base_url = (args.base_url or cfg("TTS_BASE_URL")).rstrip("/")
    args.api_key = args.api_key or os.environ.get("SENSEAUDIO_API_KEY")
    if not args.api_key:
        sys.exit(f"缺少 API key：请在 {args.env} 里设置 SENSEAUDIO_API_KEY，或传 --api-key。"
                 f"\n完整环境检查：python {Path(__file__).name} --check")

    chrome = find_chrome(args.chrome)
    if not chrome:
        sys.exit(f"找不到 Chrome。{hint('chrome')}\n完整环境检查：python {Path(__file__).name} --check")
    ffmpeg_dir = args.ffmpeg_dir or os.environ.get("FFMPEG_DIR")
    ffmpeg, ffprobe = find_tool("ffmpeg", ffmpeg_dir), find_tool("ffprobe", ffmpeg_dir)
    if not (ffmpeg and ffprobe):
        sys.exit(f"找不到 ffmpeg/ffprobe。{hint('ffmpeg')}\n完整环境检查：python {Path(__file__).name} --check")

    slides = Path(args.slides).resolve()
    scenes = json.loads(Path(args.scenes).read_text(encoding="utf-8-sig"))
    out = Path(args.out).resolve() if args.out else (Path(args.out_dir) / f"{slides.stem}.mp4").resolve()
    work = Path(args.work_dir).resolve() if args.work_dir else out.parent / "_build" / slides.stem
    frames = work / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    slide_url = slides.as_uri()
    n = len(scenes)

    # 1) TTS：逐场景生成配音并取时长
    durs = []
    for i, sc in enumerate(scenes):
        mp3 = work / f"narr_{i}.mp3"
        tts_senseaudio(args, sc["narration"], mp3)
        if "mp3" not in probe(ffprobe, mp3, "format=format_name"):
            sys.exit(f"TTS 第 {i + 1} 段返回的不是 mp3：{mp3.read_bytes()[:300]!r}")
        d = float(probe(ffprobe, mp3, "format=duration"))
        durs.append(d)
        print(f"scene {i + 1}: audio {d:.2f}s", flush=True)

    # 2) 截帧：每场景 k=0..steps
    chrome_flags = ["--headless=new", "--disable-gpu", f"--window-size={args.window_size}", "--hide-scrollbars"]
    if OS == "Linux" and hasattr(os, "geteuid") and os.geteuid() == 0:
        chrome_flags.append("--no-sandbox")
    for i, sc in enumerate(scenes):
        step_max = int(sc["steps"])
        for k in range(step_max + 1):
            png = frames / f"s{i}_k{k}.png"
            subprocess.run(
                [chrome, *chrome_flags, f"--screenshot={png}", f"{slide_url}?s={i + 1}&k={k}&noanim=1&nohud=1"],
                check=True, capture_output=True,
            )
            if not png.is_file():
                sys.exit(f"截帧失败：{png}")
        print(f"scene {i + 1}: {step_max + 1} frames", flush=True)

    # 3) 时间轴：每场景配音时长均分给 steps+1 帧；场景之间可插入停顿
    silence = None
    if args.gap > 0 and n > 1:
        silence = work / "silence.mp3"
        make_silence(ffmpeg, ffprobe, work / "narr_0.mp3", args.gap, silence)
    vlines, alines, last = [], [], None
    for i, sc in enumerate(scenes):
        step_max = int(sc["steps"])
        per = round(durs[i] / (step_max + 1), 6)
        for k in range(step_max + 1):
            last = (frames / f"s{i}_k{k}.png").as_posix()
            hold = per + (args.gap if (silence and k == step_max and i < n - 1) else 0)
            vlines += [f"file '{last}'", f"duration {round(hold, 6)}"]
        alines.append(f"file '{(work / f'narr_{i}.mp3').as_posix()}'")
        if silence and i < n - 1:
            alines.append(f"file '{silence.as_posix()}'")
    vlines.append(f"file '{last}'")
    (work / "vlist.txt").write_text("\n".join(vlines) + "\n", encoding="utf-8")
    (work / "alist.txt").write_text("\n".join(alines) + "\n", encoding="utf-8")

    # 4) 合成
    run([
        ffmpeg, "-y", "-v", "error",
        "-f", "concat", "-safe", "0", "-i", str(work / "vlist.txt"),
        "-f", "concat", "-safe", "0", "-i", str(work / "alist.txt"),
        "-vf", ASPECTS[args.aspect],
        "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(out),
    ])

    print(f"--- output: {out} ---")
    print(run([ffprobe, "-v", "error", "-show_entries", "format=duration,size:stream=codec_name,width,height",
               "-of", "default=noprint_wrappers=1", str(out)]).stdout.strip())


if __name__ == "__main__":
    main()
