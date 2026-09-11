#!/usr/bin/env python3
"""music-gen：用 SenseAudio 生成 AI 歌曲，并修前奏 / 加开场致谢。

子命令：
  python music.py check                                   # 环境与 key 检查
  python music.py lyrics --prompt "深秋渡口送别故人" [--style ...] [--duration 120]
                                                          # 用平台歌词接口写词，打印歌词 + 编曲建议，保存 json
  python music.py gen --lyrics song.txt --style "古风，古筝竹笛，慢板" --gender female [--title 名字]
  python music.py gen --prompt "夏夜海边久别重逢" --style "..." --gender any --duration 90   # 不给词让模型写
  python music.py gen --prebuilt lyrics.json ...          # 回灌 lyrics 子命令的结果
  python music.py gen --instrumental --style "Lo-fi 钢琴" --bpm 85 --duration 120           # 纯音乐
  python music.py gen ... --ref https://公网/参考.mp3       # 参考曲风（必须公网 URL）
  python music.py intro --song output/music/x.mp3 --text "谢谢每一个……" [--keep 5] [--voice female_0038_b]
                                                          # TTS 念致谢 + 截前奏 + 压混；不给 --text 则只截前奏

配置放本脚本同目录 .env：SENSEAUDIO_API_KEY（必填）、MUSIC_MODEL、TTS_VOICE、FFMPEG_DIR。
歌词里只用 [verse] / [chorus] / [bridge] / [outro] 这类结构标签，其他方括号内容会被当歌词唱出来。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("缺少 Python 包 requests：请执行  pip install requests")

SKILL_DIR = Path(__file__).resolve().parent
BASE = "https://api.senseaudio.cn"
DEFAULTS = {"MUSIC_MODEL": "senseaudio-music-2.0-260626", "TTS_MODEL": "sensenova-tts-2.0",
            "TTS_VOICE": "female_0038_b", "ASR_MODEL": "senseaudio-asr-1.5-260319"}
STRUCT_TAGS = {"intro", "verse", "pre-chorus", "chorus", "bridge", "outro", "hook", "instrumental", "interlude"}


# ---------- 基础 ----------
def load_env(path):
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v:
                os.environ.setdefault(k.strip(), v)
    return True


def cfg(name):
    return os.environ.get(name) or DEFAULTS.get(name)


def headers():
    key = os.environ.get("SENSEAUDIO_API_KEY")
    if not key:
        sys.exit(f"缺少 SENSEAUDIO_API_KEY：请在 {SKILL_DIR / '.env'} 里填写")
    return {"Authorization": f"Bearer {key}"}


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


def api_post(path, body, timeout=120, retries=3):
    for attempt in range(1, retries + 1):
        r = requests.post(BASE + path, headers=headers(), json=body, timeout=timeout)
        try:
            j = r.json()
        except ValueError:
            j = {"raw": r.text[:500]}
        if r.status_code == 200:
            return j
        msg = json.dumps(j, ensure_ascii=False)[:500]
        if r.status_code in (429, 500, 502, 503) and attempt < retries:
            print(f"{path} HTTP {r.status_code}，{20 * attempt} 秒后重试（{attempt}/{retries - 1}）：{msg[:120]}", flush=True)
            time.sleep(20 * attempt)
            continue
        sys.exit(f"{path} 失败（HTTP {r.status_code}）：{msg}")


def download(url, path):
    path.write_bytes(requests.get(url, timeout=300).content)
    return path


def warn_tags(lyrics):
    """歌词里出现非结构标签就提醒：模型会把它们唱出来。"""
    import re
    bad = sorted({t for t in re.findall(r"\[([^\]]+)\]", lyrics)
                  if re.sub(r"\s*\d+$", "", t.strip().lower()) not in STRUCT_TAGS})
    if bad:
        print(f"警告：歌词里的标签 {bad} 不是结构标签，模型会把它们当歌词唱出来。"
              f"只用 [verse]/[chorus]/[bridge]/[outro]，男女、观众、旁白等指令请写进 --style。", file=sys.stderr)
    if re.search(r"^\s*(男|女|合)\s*[:：]", lyrics, re.M):
        print("警告：歌词里的“男：/女：/合：”前缀会被唱出来，且不会改变声部，请去掉。", file=sys.stderr)


# ---------- check ----------
def cmd_check(args):
    ok = True
    print(f"环境检查（{SKILL_DIR}）")
    env_ok = load_env(SKILL_DIR / ".env")
    print(f"  [{' OK ' if env_ok else '缺失'}] .env")
    key = os.environ.get("SENSEAUDIO_API_KEY")
    print(f"  [{' OK ' if key else '缺失'}] SENSEAUDIO_API_KEY")
    ok &= bool(env_ok and key)
    for t in ("ffmpeg", "ffprobe"):
        p = find_tool(t)
        print(f"  [{' OK ' if p else '缺失'}] {t:<8}{p or '未找到：加入 PATH 或在 .env 设 FFMPEG_DIR'}")
        ok &= bool(p)
    if key:
        try:
            r = requests.post(BASE + "/v1/get_voice", headers=headers(), json={"voice_type": "system"}, timeout=20)
            good = r.status_code == 200 and r.json().get("base_resp", {}).get("status_code") == 0
            print(f"  [{' OK ' if good else '失败'}] TTS/音乐鉴权  HTTP {r.status_code}")
            ok &= good
        except Exception as e:
            print(f"  [失败] 连接 {BASE}：{e}")
            ok = False
    print(f"  [ -- ] 配置  music={cfg('MUSIC_MODEL')} tts_voice={cfg('TTS_VOICE')} asr={cfg('ASR_MODEL')}")
    print("\n环境正常。" if ok else "\n有缺失项，处理后重试。")
    sys.exit(0 if ok else 1)


# ---------- lyrics ----------
def cmd_lyrics(args):
    load_env(SKILL_DIR / ".env")
    body = {"model": cfg("MUSIC_MODEL"), "prompt": args.prompt, "language": args.language, "enhance_theme": True}
    if args.style:
        body["style"] = args.style
    if args.rhyme:
        body["rhyme"] = args.rhyme
    if args.duration:
        body["expect_duration"] = args.duration
    if args.extra:
        body["additional_prompt"] = args.extra
    j = api_post("/v1/music/lyrics/create", body)
    d = (j.get("data") or [{}])[0]
    out = Path(args.out or f"output/music/lyrics_{int(time.time())}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"标题：{d.get('title')}   建议：{d.get('bpm')} BPM / {d.get('key_scale')} / {d.get('time_signature')} / {d.get('duration')}s")
    print(f"编曲描述：{d.get('caption')}\n")
    print(d.get("text"))
    print(f"\n已保存 {out}（可用  gen --prebuilt {out}  直接生成）")


# ---------- gen ----------
def cmd_gen(args):
    load_env(SKILL_DIR / ".env")
    body = {"model": cfg("MUSIC_MODEL"), "audio_settings": {"format": args.format}}
    if args.instrumental:
        body["mode"] = "instrumental"
    if args.prebuilt:
        d = json.loads(Path(args.prebuilt).read_text(encoding="utf-8-sig"))
        pb = {"lyrics": d.get("text") or d.get("lyrics")}
        for k in ("caption", "title", "bpm", "key_scale", "time_signature", "duration", "vocal_language"):
            if d.get(k) not in (None, ""):
                pb[k] = d[k]
        body["prebuilt_lyrics"] = pb
        warn_tags(pb["lyrics"])
    elif args.lyrics:
        text = Path(args.lyrics).read_text(encoding="utf-8-sig") if Path(args.lyrics).is_file() else args.lyrics
        body["lyrics"] = text
        warn_tags(text)
    if args.prompt:
        body["prompt"] = args.prompt
    elif args.instrumental and args.style:
        body["prompt"] = args.style          # 纯音乐模式不带 prompt 会稳定 500，用 style 兜底
    if args.style:
        body["style"] = args.style
    if not (args.prompt or args.style or args.lyrics or args.prebuilt):
        sys.exit("至少给 --prompt / --style / --lyrics / --prebuilt 之一")
    if not args.instrumental:
        body["vocal_settings"] = {"gender": args.gender}
    ls = {"language": args.language}
    if args.duration:
        ls["expect_duration"] = args.duration
    body["lyrics_settings"] = ls
    ms = {}
    if args.bpm:
        ms["bpm"] = args.bpm
    if args.key:
        ms["key"] = args.key
    if args.time_signature:
        ms["time_signature"] = args.time_signature
    if args.instruments:
        ms["classes"] = [s.strip() for s in args.instruments.split(",") if s.strip()]
    if ms:
        body["music_settings"] = ms
    if args.extra:
        body["model_settings"] = {"additional_prompt": args.extra}
    if args.ref:
        if not args.ref.startswith(("http://", "https://")):
            sys.exit("--ref 必须是公网可访问的 URL（本地文件请先上传到对象存储 / 静态站，再填 URL）")
        body["attachments"] = [{"type": "audio", "url": args.ref}]
    if not args.no_cover:
        body["cover_settings"] = {"generate": True, "ratio": args.cover_ratio,
                                  **({"prompt_hint": args.cover_hint} if args.cover_hint else {})}

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.title or f"song_{int(time.time())}"
    (out_dir / f"{stem}_request.json").write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")

    tid = api_post("/v2/music/song/create", body)["task_id"]
    print(f"任务已提交 {tid}，等待生成（通常 1～3 分钟）…", flush=True)
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        r = requests.get(f"{BASE}/v1/music/song/pending/{tid}", headers=headers(), timeout=60)
        j = r.json() if r.status_code == 200 else {"status": f"HTTP {r.status_code}"}
        st = j.get("status")
        if st == "SUCCESS":
            break
        if st == "FAILED":
            sys.exit(f"生成失败：{j.get('fail_reason')}")
        time.sleep(8)
    else:
        sys.exit(f"超时仍未完成，稍后可自行查询：GET {BASE}/v1/music/song/pending/{tid}")

    data = (j.get("response") or {}).get("data") or []
    if not data:
        sys.exit(f"返回里没有歌曲：{json.dumps(j, ensure_ascii=False)[:300]}")
    song = data[0]
    (out_dir / f"{stem}_result.json").write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    mp3 = download(song["audio_url"], out_dir / f"{stem}.{args.format if args.format != 'wav32' else 'wav'}")
    cover = download(song["cover_url"], out_dir / f"{stem}_cover.jpg") if song.get("cover_url") and not args.no_cover else None
    if song.get("lyrics"):
        (out_dir / f"{stem}_lyrics.txt").write_text(song["lyrics"], encoding="utf-8")
    print(f"完成：{mp3}  （{song.get('title')}，{song.get('duration')} 秒）")
    if cover:
        print(f"封面：{cover}")
    print("提示：前奏偏长或想加开场致谢，用  intro --song <文件> [--text ...]")


# ---------- intro ----------
def vocal_onset(ffprobe, song, asr_model):
    """用 ASR 字级时间戳找第一个真正唱出的字；首字常被标 0，取前 3 个字里第一个 >1s 的。"""
    with open(song, "rb") as f:
        r = requests.post(BASE + "/v1/audio/transcriptions", headers=headers(),
                          files={"file": (song.name, f, "audio/mpeg")},
                          data={"model": asr_model, "language": "zh", "response_format": "verbose_json",
                                "timestamp_granularities[]": "word"}, timeout=300)
    words = (r.json() if r.status_code == 200 else {}).get("words") or []
    for w in words[:3]:
        if float(w.get("start") or 0) > 1.0:
            return float(w["start"]), words
    return (float(words[0]["start"]) if words else 0.0), words


def cmd_intro(args):
    load_env(SKILL_DIR / ".env")
    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    if not (ffmpeg and ffprobe):
        sys.exit("找不到 ffmpeg/ffprobe，加入 PATH 或在 .env 设 FFMPEG_DIR")
    song = Path(args.song).resolve()
    out = Path(args.out) if args.out else song.with_name(song.stem + ("_with_intro" if args.text else "_short_intro") + ".mp3")

    onset = args.onset
    if onset is None:
        onset, words = vocal_onset(ffprobe, song, cfg("ASR_MODEL"))
        print(f"人声起始 ≈ {onset:.1f} 秒（ASR 测得；不准可用 --onset 手动指定）")

    if not args.text:
        start = max(0.0, onset - args.keep)
        subprocess.run([ffmpeg, "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(song),
                        "-af", "afade=t=in:st=0:d=1.5", "-c:a", "libmp3lame", "-q:a", "2", str(out)], check=True)
        print(f"已截前奏：保留 {min(args.keep, onset):.1f} 秒 → {out}")
        return

    tts = api_post("/v1/t2a_v2", {"model": cfg("TTS_MODEL"), "text": args.text, "stream": False,
                                   "voice_setting": {"voice_id": args.voice or cfg("TTS_VOICE"), "speed": args.speed},
                                   "audio_setting": {"format": "mp3", "sample_rate": 32000, "channel": 1}})
    audio = tts.get("data", {}).get("audio")
    if not audio:
        sys.exit(f"TTS 失败：{json.dumps(tts, ensure_ascii=False)[:300]}")
    spoken = song.with_name(song.stem + "_intro_tts.mp3")
    spoken.write_bytes(bytes.fromhex(audio))
    s_len = float(subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                  str(spoken)], capture_output=True, text=True).stdout.strip())
    keep = s_len + args.lead          # 独白时长 + 念完后留的纯前奏
    start = max(0.0, onset - keep)
    duck_until = s_len + 0.4
    flt = (f"[1:a]afade=t=in:st=0:d=1.5,volume='if(lt(t,{duck_until:.3f}),{args.duck},1)':eval=frame[m];"
           f"[0:a]adelay=400|400[v];[v][m]amix=inputs=2:duration=longest:normalize=0[a]")
    subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(spoken), "-ss", f"{start:.3f}", "-i", str(song),
                    "-filter_complex", flt, "-map", "[a]", "-c:a", "libmp3lame", "-q:a", "2", str(out)], check=True)
    print(f"独白 {s_len:.1f} 秒，压在 {min(keep, onset):.1f} 秒前奏上（伴奏压到 {args.duck}），念完 {args.lead} 秒后进人声 → {out}")


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description="SenseAudio 歌曲生成 / 前奏修整（配置见同目录 .env）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="环境与 key 检查")

    p = sub.add_parser("lyrics", help="用平台歌词接口写词（可选步骤）")
    p.add_argument("--prompt", required=True, help="歌曲主题")
    p.add_argument("--style", help="音乐/写作风格")
    p.add_argument("--rhyme", help="韵脚，如 an")
    p.add_argument("--duration", type=int, help="期望时长秒（10-600）")
    p.add_argument("--language", default="zh")
    p.add_argument("--extra", help="附加要求，如“多用意象，副歌两遍”")
    p.add_argument("--out", help="保存 json 的路径")

    p = sub.add_parser("gen", help="生成歌曲")
    p.add_argument("--lyrics", help="歌词文件路径或歌词文本；只用 [verse]/[chorus]/[bridge] 标签")
    p.add_argument("--prebuilt", help="lyrics 子命令保存的 json，原样回灌")
    p.add_argument("--prompt", help="主题/描述（不给词时模型据此写词）")
    p.add_argument("--style", help="曲风：乐器、情绪、速度、人声特点，中英文皆可")
    p.add_argument("--gender", default="any", choices=["male", "female", "any", "duet"], help="声部；duet=男女对唱（分配不可控）")
    p.add_argument("--instrumental", action="store_true", help="纯音乐")
    p.add_argument("--duration", type=int, help="期望时长秒（10-600，模型只当参考）")
    p.add_argument("--language", default="zh")
    p.add_argument("--bpm", type=int)
    p.add_argument("--key", help="调式，如 D minor")
    p.add_argument("--time-signature", help="拍号，如 4/4")
    p.add_argument("--instruments", help="乐器，逗号分隔，如 古筝,竹笛,二胡")
    p.add_argument("--extra", help="附加提示词（model_settings.additional_prompt）")
    p.add_argument("--ref", help="参考音频的公网 URL（曲风参考，不是翻唱）")
    p.add_argument("--no-cover", action="store_true", help="不生成封面")
    p.add_argument("--cover-hint", help="封面提示词")
    p.add_argument("--cover-ratio", default="1:1", choices=["1:1", "3:4", "2:3", "9:16", "16:9", "4:3"])
    p.add_argument("--format", default="mp3", choices=["mp3", "wav", "wav32"])
    p.add_argument("--title", help="输出文件名（不含扩展名）")
    p.add_argument("--out-dir", default="output/music")
    p.add_argument("--timeout", type=int, default=600)

    p = sub.add_parser("intro", help="截前奏 / 加开场致谢独白")
    p.add_argument("--song", required=True, help="歌曲文件")
    p.add_argument("--text", help="致谢/独白文字；不给则只截前奏")
    p.add_argument("--voice", help="独白音色，默认 .env 的 TTS_VOICE")
    p.add_argument("--speed", type=float, default=0.95)
    p.add_argument("--keep", type=float, default=5.0, help="只截前奏时保留的前奏秒数")
    p.add_argument("--lead", type=float, default=1.5, help="独白念完到人声进入之间的秒数")
    p.add_argument("--duck", type=float, default=0.3, help="独白期间伴奏音量（0-1）")
    p.add_argument("--onset", type=float, help="手动指定人声起始秒，跳过 ASR")
    p.add_argument("--out", help="输出文件")

    args = ap.parse_args()
    {"check": cmd_check, "lyrics": cmd_lyrics, "gen": cmd_gen, "intro": cmd_intro}[args.cmd](args)


if __name__ == "__main__":
    main()
