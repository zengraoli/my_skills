#!/usr/bin/env python3
"""asr-tts：语音转文字（SenseAudio ASR）、歌曲歌词提取（带时间轴 .lrc）、文字转语音（SenseAudio / edge-tts）。

子命令：
  python speech.py check
  python speech.py asr --file 录音.mp3 [--model standard] [--format txt|srt|json] [--speakers 4] [--translate en]
  python speech.py lyrics --file 歌曲.mp3 [--model standard]          # 出 歌名.lrc + 歌名.txt
  python speech.py tts --text "要念的话" --out 输出.mp3 [--provider edge|senseaudio] [--voice ...]
  python speech.py tts --file 稿子.txt --out 输出.mp3
  python speech.py voices [--provider edge|senseaudio]

配置见同目录 .env。音频超过 10 MB 会自动压成单声道 16 kHz 再传；仍然太大就按段切开识别再拼回来。
"""
import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("缺少 Python 包 requests：请执行  pip install requests")

SKILL_DIR = Path(__file__).resolve().parent
BASE = "https://api.senseaudio.cn"
DEFAULTS = {"ASR_MODEL": "standard", "TTS_PROVIDER": "senseaudio", "TTS_VOICE": "female_0038_b",
            "EDGE_VOICE": "zh-CN-XiaoxiaoNeural", "TTS_SPEED": "1.0"}
MODELS = {"lite": "senseaudio-asr-lite-1.5-260319", "standard": "senseaudio-asr-1.5-260319",
          "pro": "senseaudio-asr-pro-1.5-260319", "deepthink": "senseaudio-asr-deepthink-1.5-260319"}
PRICE_PER_HOUR = {"lite": 0.9, "standard": 1.8, "pro": 3.6, "deepthink": 3.6}
TIMESTAMP_MODELS = {"standard", "pro"}          # 只有这两个给逐字/逐句时间戳
MAX_BYTES = 9_800_000                            # 接口上限 10 MB，留一点余量
WINDOW = 170                                     # 服务端按 180 秒窗口处理，超过的窗口有时丢时间戳；客户端先切 ≤170 秒
CUT_SEARCH = 60                                  # 在每段末尾 60 秒内找最长的安静处下刀，避免切断演唱


# ---------- 基础 ----------
def load_env(path=None):
    p = Path(path) if path else SKILL_DIR / ".env"
    if not p.is_file():
        return False
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v:
                os.environ.setdefault(k.strip(), v)
    return True


def cfg(name):
    return os.environ.get(name) or DEFAULTS.get(name)


def key():
    k = os.environ.get("SENSEAUDIO_API_KEY")
    if not k:
        sys.exit(f"缺少 SENSEAUDIO_API_KEY：请在 {SKILL_DIR / '.env'} 里填写")
    return k


def find_tool(name):
    exe = name + (".exe" if os.name == "nt" else "")
    d = os.environ.get("FFMPEG_DIR")
    if d:
        p = Path(d.strip().strip('"'))
        if p.is_file():
            p = p.parent
        if (p / exe).is_file():
            return str(p / exe)
    return shutil.which(name)


def run(cmd):
    r = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"命令失败（{Path(cmd[0]).name}）：\n{(r.stderr or r.stdout).strip()[-1000:]}")
    return r


def duration(path):
    return float(run([find_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", str(path)]).stdout.strip() or 0)


# ---------- ASR ----------
def quiet_cuts(src, total):
    """在 WINDOW 秒附近找最安静的点作为切段位置，返回切点列表（秒）。"""
    import wave
    import numpy as np
    ffmpeg = find_tool("ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "env.wav"
        run([ffmpeg, "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", "8000", str(wav)])
        with wave.open(str(wav)) as w:
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)
    hop = 400                                    # 50 ms
    rms = np.sqrt(np.convolve(data ** 2, np.ones(hop) / hop, mode="valid")[::hop] + 1e-9)
    rms = np.convolve(rms, np.ones(20) / 20, mode="same")   # 再平滑 1 秒，偏向真正的换气/间奏而不是瞬间低谷
    cuts, pos = [], 0.0
    while total - pos > WINDOW:
        lo, hi = int((pos + WINDOW - CUT_SEARCH) * 20), int((pos + WINDOW) * 20)
        seg = rms[lo:hi]
        cut = (lo + int(np.argmin(seg))) / 20 if len(seg) else pos + WINDOW
        cuts.append(cut)
        pos = cut
    return cuts


def prepare_audio(src, tmp, need_timestamps=True):
    """返回 [(文件, 起始偏移秒)]。要时间戳时按 ≤170 秒在安静处切段；超 10 MB 的会压成单声道 16 kHz。"""
    src = Path(src)
    total = duration(src)
    ok_format = src.suffix.lower() in (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".mp4")
    if total <= WINDOW or not need_timestamps:
        if src.stat().st_size <= MAX_BYTES and ok_format:
            return [(src, 0.0)]
        small = Path(tmp) / "asr_16k.mp3"
        run([find_tool("ffmpeg"), "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", "16000", "-b:a", "48k", str(small)])
        return [(small, 0.0)]
    cuts = quiet_cuts(src, total)
    bounds = [0.0] + cuts + [total]
    parts = []
    for i, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])):
        p = Path(tmp) / f"asr_part{i:02d}.mp3"
        run([find_tool("ffmpeg"), "-y", "-v", "error", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", str(src),
             "-af", "apad=pad_dur=1.5", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(p)])   # 尾部补静音，服务端对段尾的字对齐更准
        parts.append((p, a))
    print(f"  {total:.0f} 秒的音频在安静处切成 {len(parts)} 段（{', '.join(f'{c:.1f}s' for c in cuts)}）分别识别")
    return parts


def transcribe_one(path, model, language, words, speakers, translate, punct):
    data = {"model": MODELS[model], "response_format": "verbose_json"}
    if language:
        data["language"] = language
    if model in TIMESTAMP_MODELS:
        data["timestamp_granularities[]"] = ["segment", "word"] if words else ["segment"]
        if punct:
            data["enable_punctuation"] = "true"
    if speakers and model in TIMESTAMP_MODELS:
        data["enable_speaker_diarization"] = "true"
        if model == "pro":
            data["max_speakers"] = str(speakers)
    if translate:
        if model not in ("pro", "deepthink"):
            sys.exit("翻译只有 pro / deepthink 模型支持，请加 --model pro")
        data["target_language"] = translate
    for attempt in range(1, 4):
        with open(path, "rb") as f:
            r = requests.post(BASE + "/v1/audio/transcriptions", headers={"Authorization": f"Bearer {key()}"},
                              files={"file": (Path(path).name, f)}, data=data, timeout=900)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503) and attempt < 3:
            print(f"  HTTP {r.status_code}，{15*attempt} 秒后重试", flush=True)
            time.sleep(15 * attempt)
            continue
        sys.exit(f"识别失败（HTTP {r.status_code}）：{r.text[:400]}")


def transcribe(src, model, language=None, words=True, speakers=None, translate=None, punct=True):
    """识别整段音频，合并分段结果，时间戳换算回原音频时间。"""
    if model not in MODELS:
        sys.exit(f"--model 可选 {', '.join(MODELS)}")
    secs = duration(src)
    print(f"识别 {Path(src).name}（{secs/60:.1f} 分钟，模型 {model}，约 ¥{secs/3600*PRICE_PER_HOUR[model]:.2f}）…", flush=True)
    out = {"text": "", "segments": [], "words": [], "chunks": [], "duration": secs}
    with tempfile.TemporaryDirectory() as tmp:
        for path, off in prepare_audio(src, tmp, need_timestamps=model in TIMESTAMP_MODELS):
            j = transcribe_one(path, model, language, words, speakers, translate, punct)
            ch = {"offset": off, "text": j.get("text", ""), "words": []}
            out["text"] += j.get("text", "")
            for s in j.get("segments") or []:
                s = dict(s); s["start"] = float(s.get("start") or 0) + off; s["end"] = float(s.get("end") or 0) + off
                out["segments"].append(s)
            for w in j.get("words") or []:
                w = dict(w); w["start"] = float(w.get("start") or 0) + off; w["end"] = float(w.get("end") or 0) + off
                out["words"].append(w); ch["words"].append(w)
            out["chunks"].append(ch)
    return out


def ts_srt(t):
    ms = int(round(t * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ts_lrc(t):
    m, s = divmod(max(t, 0.0), 60)
    return f"[{int(m):02d}:{s:05.2f}]"


def cmd_check(args):
    load_env(args.env)
    ok = True
    k = os.environ.get("SENSEAUDIO_API_KEY")
    print(f"  [{' OK ' if k else '缺失'}] SENSEAUDIO_API_KEY")
    ok &= bool(k)
    for t in ("ffmpeg", "ffprobe"):
        p = find_tool(t)
        print(f"  [{' OK ' if p else '缺失'}] {t:<8}{p or '未找到：加入 PATH 或在 .env 设 FFMPEG_DIR'}")
        ok &= bool(p)
    try:
        import edge_tts  # noqa
        print("  [ OK ] edge-tts    免费 TTS 可用")
    except ImportError:
        print("  [ -- ] edge-tts    未安装（pip install edge-tts），只影响 --provider edge")
    if k:
        r = requests.post(BASE + "/v1/get_voice", headers={"Authorization": f"Bearer {k}"}, json={"voice_type": "system"}, timeout=20)
        good = r.status_code == 200 and r.json().get("base_resp", {}).get("status_code") == 0
        print(f"  [{' OK ' if good else '失败'}] 鉴权        HTTP {r.status_code}")
        ok &= good
    print(f"  [ -- ] ASR 默认模型 {cfg('ASR_MODEL')}；TTS 默认 {cfg('TTS_PROVIDER')}")
    print("\n环境正常。" if ok else "\n有缺失项，处理后重试。")
    sys.exit(0 if ok else 1)


def cmd_asr(args):
    load_env(args.env)
    src = Path(args.file)
    if not src.is_file():
        sys.exit(f"找不到文件：{src}")
    model = args.model or cfg("ASR_MODEL")
    j = transcribe(src, model, args.lang, words=args.format == "json", speakers=args.speakers, translate=args.translate)
    out = Path(args.out) if args.out else src.with_suffix({"txt": ".txt", "srt": ".srt", "json": ".json"}[args.format])
    if args.format == "json":
        out.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    elif args.format == "srt":
        if not j["segments"]:
            sys.exit(f"模型 {model} 不返回时间戳，出 srt 请用 --model standard 或 pro")
        lines = []
        for i, s in enumerate(j["segments"], 1):
            who = f"{s['speaker']}：" if s.get("speaker") else ""
            tr = f"\n{s['translation']}" if s.get("translation") else ""
            lines.append(f"{i}\n{ts_srt(s['start'])} --> {ts_srt(s['end'])}\n{who}{s.get('text','').strip()}{tr}\n")
        out.write_text("\n".join(lines), encoding="utf-8")
    else:
        if j["segments"]:
            text = "\n".join((f"{s['speaker']}：" if s.get("speaker") else "") + s.get("text", "").strip() for s in j["segments"])
        else:
            text = j["text"]
        out.write_text(text, encoding="utf-8")
    print(f"完成 → {out}")


# ---------- 歌词 ----------
PUNCT = "，。！？、；：,.!?;:…—~～ 　\"'“”‘’"
SPLIT_RE = re.compile(r"[，。！？；,.!?;\n]+")


def chunk_lines(chunk, max_chars):
    """一段识别结果 → [(起, 止, 句子)]：按标点断成乐句，再逐字对齐到时间戳。"""
    phrases = [p.strip(PUNCT) for p in SPLIT_RE.split(chunk["text"]) if p.strip(PUNCT)]
    chars = []                                   # [(字, 起, 止)]，把逐字结果拆到单字
    for w in chunk["words"]:
        t = "".join(c for c in str(w.get("word", "")) if c not in PUNCT)
        n = max(len(t), 1)
        a, b = float(w["start"]), float(w["end"])
        for k, c in enumerate(t):
            chars.append((c, a + (b - a) * k / n, a + (b - a) * (k + 1) / n))
    out, i = [], 0
    for ph in phrases:
        clean = "".join(c for c in ph if c not in PUNCT)
        n = len(clean)
        if chars and i < len(chars):
            seg = chars[i:i + n]
            start, end = seg[0][1], seg[-1][2]
            # 过长的句子在最大停顿处拆开
            pieces = [(0, len(seg))]
            while any(b - a > max_chars for a, b in pieces):
                a, b = next(p for p in pieces if p[1] - p[0] > max_chars)
                gaps = [(seg[k][1] - seg[k - 1][2], k) for k in range(a + 3, b - 2)]
                k = max(gaps)[1] if gaps else (a + b) // 2
                idx = pieces.index((a, b)); pieces[idx:idx + 1] = [(a, k), (k, b)]
            if len(pieces) > 1:
                for a, b in pieces:
                    out.append((seg[a][1], seg[b - 1][2], clean[a:b]))
            else:
                out.append((start, end, ph))
            i += n
        else:
            out.append((None, None, ph))          # 这段没有逐字时间戳，后面插值补
    return out


SEC_PER_CHAR = 0.28          # 唱一个字至少要这么久；比这更挤的时间戳视为对齐失败


def fill_times(lines, chunks):
    """① 没有时间戳的句子按字数补上；② 挤成一堆的句子（间隔短于唱完上一句所需时间）在前后可信时间点之间按字数摊开。"""
    for i, (a, b, t) in enumerate(lines):
        if a is None:
            prev = next((lines[k][1] for k in range(i - 1, -1, -1) if lines[k][1] is not None), 0.0)
            nxt = next((lines[k][0] for k in range(i + 1, len(lines)) if lines[k][0] is not None), prev + 3 * len(t) / 4)
            lines[i] = (prev + 0.3, min(nxt, prev + 0.3 + len(t) * 0.35), t)
    n = len(lines)
    bad = [False] * n
    for i in range(1, n):
        if lines[i][0] < lines[i - 1][0] + SEC_PER_CHAR * len(lines[i - 1][2]):
            bad[i] = True
    i = 1
    while i < n:
        if not bad[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and bad[j + 1]:
            j += 1
        a = lines[i - 1][0]                                   # 前一个可信起点
        b = lines[j + 1][0] if j + 1 < n else lines[j][0] + SEC_PER_CHAR * 2 * len(lines[j][2])
        run = lines[i - 1:j + 1]
        weights = [max(len(t), 1) for _, _, t in run]
        total = sum(weights)
        t0 = a
        for k, (_, _, t) in enumerate(run):
            if k > 0:
                lines[i - 1 + k] = (t0, t0 + (b - a) * weights[k] / total, t)
            t0 += (b - a) * weights[k] / total
        i = j + 1
    return lines


def cmd_lyrics(args):
    load_env(args.env)
    src = Path(args.file)
    if not src.is_file():
        sys.exit(f"找不到文件：{src}")
    model = args.model or ("standard" if cfg("ASR_MODEL") not in TIMESTAMP_MODELS else cfg("ASR_MODEL"))
    if model not in TIMESTAMP_MODELS:
        sys.exit("提取歌词需要逐字时间戳，只能用 --model standard 或 pro（deepthink 会改写内容，不适合歌词）")
    j = transcribe(src, model, args.lang or "zh", words=True, punct=True)
    if not j["text"].strip():
        sys.exit("没识别到任何字（可能是纯音乐，或人声被伴奏盖住了；可先用 suno-music 的 stems 分出人声再提取）")
    lines = []
    for ch in j["chunks"]:
        lines += chunk_lines(ch, args.max_chars)
    missing = sum(1 for a, _, _ in lines if a is None)
    lines = fill_times(lines, j["chunks"])
    stem = Path(args.out).with_suffix("") if args.out else src.with_suffix("")
    stem.parent.mkdir(parents=True, exist_ok=True)
    title = src.stem
    lrc = [f"[ti:{title}]", "[by:asr-tts / SenseAudio ASR]"] + [f"{ts_lrc(a)}{t}" for a, _, t in lines]
    Path(f"{stem}.lrc").write_text("\n".join(lrc) + "\n", encoding="utf-8")
    Path(f"{stem}.txt").write_text("\n".join(t for _, _, t in lines) + "\n", encoding="utf-8")
    Path(f"{stem}.asr.json").write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    first = lines[0][0] if lines else 0
    print(f"识别出 {len(lines)} 句，人声从 {first:.1f} 秒开始" + (f"（{missing} 句没拿到逐字时间，已按字数估算）" if missing else ""))
    print(f"  歌词（纯文本）→ {stem}.txt\n  歌词（带时间轴）→ {stem}.lrc\n  原始识别结果 → {stem}.asr.json")
    print("提示：歌曲有伴奏，识别会有同音错字，请对照原曲校对；要更准可先分轨取人声再提取。")


# ---------- TTS ----------
def split_text(text, limit):
    parts, cur = [], ""
    for sent in re.split(r"(?<=[。！？!?；;\n])", text):
        if len(cur) + len(sent) > limit and cur:
            parts.append(cur); cur = ""
        cur += sent
    if cur.strip():
        parts.append(cur)
    return parts


def tts_senseaudio(text, voice, speed, out):
    r = requests.post(BASE + "/v1/t2a_v2", headers={"Authorization": f"Bearer {key()}"},
                      json={"model": "sensenova-tts-2.0", "text": text, "stream": False,
                            "voice_setting": {"voice_id": voice, "speed": speed},
                            "audio_setting": {"format": "mp3", "sample_rate": 32000, "channel": 1}}, timeout=180)
    a = (r.json().get("data") or {}).get("audio") if r.status_code == 200 else None
    if not a:
        sys.exit(f"SenseAudio TTS 失败（HTTP {r.status_code}）：{r.text[:300]}")
    Path(out).write_bytes(bytes.fromhex(a))


def tts_edge(text, voice, speed, out):
    try:
        import edge_tts
    except ImportError:
        sys.exit("edge-tts 未安装：pip install edge-tts")
    rate = f"{int(round((speed - 1) * 100)):+d}%"
    asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(str(out)))


def cmd_tts(args):
    load_env(args.env)
    text = Path(args.file).read_text(encoding="utf-8-sig") if args.file else (args.text or "")
    if not text.strip():
        sys.exit("用 --text 或 --file 给出要念的内容")
    provider = (args.provider or cfg("TTS_PROVIDER")).lower()
    voice = args.voice or (cfg("EDGE_VOICE") if provider == "edge" else cfg("TTS_VOICE"))
    speed = args.speed if args.speed is not None else float(cfg("TTS_SPEED"))
    fn = tts_edge if provider == "edge" else tts_senseaudio
    limit = 3000 if provider == "senseaudio" else 2000
    parts = split_text(text, limit)
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    cost = f"约 ¥{len(text)*2/10000*3.5:.2f}" if provider == "senseaudio" else "免费"
    print(f"TTS：{provider} / {voice} / 语速 {speed}，{len(text)} 字，分 {len(parts)} 段（{cost}）", flush=True)
    if len(parts) == 1:
        fn(parts[0], voice, speed, out)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            files = []
            for i, p in enumerate(parts):
                f = Path(tmp) / f"p{i:03d}.mp3"
                fn(p, voice, speed, f)
                files.append(f)
            lst = Path(tmp) / "list.txt"
            lst.write_text("".join(f"file '{f.as_posix()}'\n" for f in files), encoding="utf-8")
            run([find_tool("ffmpeg"), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                 "-c:a", "libmp3lame", "-q:a", "2", str(out)])
    print(f"完成 → {out}（{duration(out):.1f} 秒）")


def cmd_voices(args):
    load_env(args.env)
    provider = (args.provider or cfg("TTS_PROVIDER")).lower()
    if provider == "edge":
        import edge_tts
        vs = asyncio.run(edge_tts.list_voices())
        for v in vs:
            if v["Locale"].startswith(("zh-", "yue")):
                print(f"  {v['ShortName']:<32} {v['Gender']:<7} {v['Locale']}")
        return
    r = requests.post(BASE + "/v1/get_voice", headers={"Authorization": f"Bearer {key()}"}, json={"voice_type": "all"}, timeout=30)
    j = r.json()
    for group in ("system_voice", "voice_cloning", "voice_generation"):
        for v in j.get(group) or []:
            print(f"  {v.get('voice_id'):<22} {v.get('voice_name') or '':<8} {(v.get('description') or [''])[0][:40]}")
    print("完整音色表与试听：https://docs.senseaudio.cn/guides/voice/catalog")


def main():
    ap = argparse.ArgumentParser(description="语音转文字 / 歌词提取 / 文字转语音（配置见同目录 .env）")
    ap.add_argument("--env", help="配置文件路径")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="环境与鉴权检查")

    p = sub.add_parser("asr", help="语音转文字")
    p.add_argument("--file", required=True)
    p.add_argument("--model", choices=list(MODELS), help="默认 .env 的 ASR_MODEL（standard）")
    p.add_argument("--lang", help="语言代码，如 zh / en；不填自动识别")
    p.add_argument("--format", choices=["txt", "srt", "json"], default="txt")
    p.add_argument("--speakers", type=int, help="开启说话人区分，填最多几个人（pro 生效最佳）")
    p.add_argument("--translate", help="同时翻译成该语言，如 en（需 --model pro）")
    p.add_argument("--out")

    p = sub.add_parser("lyrics", help="从歌曲提取歌词，出 .txt + .lrc")
    p.add_argument("--file", required=True)
    p.add_argument("--model", choices=["standard", "pro"])
    p.add_argument("--lang", help="默认 zh")
    p.add_argument("--max-chars", type=int, default=16, help="一行最多几个字，超过就在最大停顿处拆开（默认 16）")
    p.add_argument("--out", help="输出路径（不含扩展名），默认与歌曲同目录同名")

    p = sub.add_parser("tts", help="文字转语音")
    p.add_argument("--text")
    p.add_argument("--file", help="文本文件")
    p.add_argument("--out", required=True)
    p.add_argument("--provider", choices=["senseaudio", "edge"])
    p.add_argument("--voice")
    p.add_argument("--speed", type=float)

    p = sub.add_parser("voices", help="列出可用音色")
    p.add_argument("--provider", choices=["senseaudio", "edge"])

    args = ap.parse_args()
    {"check": cmd_check, "asr": cmd_asr, "lyrics": cmd_lyrics, "tts": cmd_tts, "voices": cmd_voices}[args.cmd](args)


if __name__ == "__main__":
    main()
