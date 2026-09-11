#!/usr/bin/env python3
"""doc-podcast：把双人对谈脚本渲染成播客 mp3 + srt（SenseAudio A1 音频生成）。

用法：
  python podcast.py check
  python podcast.py render --script 脚本.txt --dry-run            # 只看分段与提示词，不花钱
  python podcast.py render --script 脚本.txt [--title 名字] [--bgm 音乐.mp3] [--bgm-gain -18]

脚本格式（UTF-8 文本，# 开头是元信息，其余每行“角色名：台词”）：
  # 标题：AI 如何替你值夜班
  # 角色：小满=female_0038_b|主持人，亲切、语速略快
  # 角色：阿川=male_0029_a|嘉宾，沉稳、爱举例
  # 场景：安静的播客录音室对谈
  小满：（热情）大家好，欢迎收听今天的节目。
  阿川：（笑）简单说，它能让 AI 自己定闹钟。

台词里的（括号）是情绪/语气提示，不会被念出来。脚本会按约 CHUNK_CHARS 字自动分段调用 A1（单次上限 120 秒），
每段都带同一份角色定义，所以两位主播的音色全程一致。输出 mp3、srt（带说话人）、分段文件与费用统计。
配置放本脚本同目录 .env：SENSEAUDIO_API_KEY（必填）、AUDIO_MODEL、CHUNK_CHARS、FFMPEG_DIR。
"""
import argparse
import json
import os
import re
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
DEFAULTS = {"AUDIO_MODEL": "senseaudio-a1", "CHUNK_CHARS": "450", "CHUNK_GAP": "0.5"}
PRICE_PER_SEC = 0.017          # A1 计费：85 积分/秒 ≈ 0.017 元/秒
CHARS_PER_SEC = 4.7            # 实测：230 字 ≈ 48.7 秒


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


def run(cmd):
    r = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"命令失败（{Path(cmd[0]).name}，退出码 {r.returncode}）：\n  {' '.join(cmd[:6])} …\n{(r.stderr or r.stdout).strip()[-1500:]}")
    return r


def duration_of(ffprobe, path):
    return float(run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).stdout.strip())


# ---------- 脚本解析 ----------
COLON = r"[:：]"


def parse_script(text):
    meta = {"title": None, "roles": [], "scene": None, "music": None, "bgm": None}
    lines = []
    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if not s:
            continue
        if s.startswith("#"):
            body = s.lstrip("#").strip()
            m = re.match(rf"^(标题|角色|场景|音乐|背景音乐)\s*{COLON}\s*(.*)$", body)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip()
            if key == "标题":
                meta["title"] = val
            elif key == "场景":
                meta["scene"] = val
            elif key == "音乐":
                meta["music"] = val
            elif key == "背景音乐":
                meta["bgm"] = val
            else:
                rm = re.match(r"^([^=]+?)\s*=\s*([A-Za-z0-9_\-]+)\s*(?:\|\s*(.*))?$", val)
                if not rm:
                    sys.exit(f"第 {n} 行角色定义格式错误，应为  # 角色：名字=音色id|性格描述")
                meta["roles"].append({"name": rm.group(1).strip(), "voice": rm.group(2), "desc": (rm.group(3) or "").strip()})
            continue
        m = re.match(rf"^([^:：]{{1,12}}){COLON}\s*(.+)$", s)
        if not m:
            sys.exit(f"第 {n} 行不是“角色名：台词”格式：{s[:40]}")
        lines.append({"n": n, "speaker": m.group(1).strip(), "text": m.group(2).strip()})
    if not meta["roles"]:
        sys.exit("脚本缺少角色定义：# 角色：名字=音色id|性格描述")
    names = {r["name"] for r in meta["roles"]}
    bad = sorted({l["speaker"] for l in lines if l["speaker"] not in names})
    if bad:
        sys.exit(f"台词里出现了未定义的角色 {bad}，已定义：{sorted(names)}")
    if not lines:
        sys.exit("脚本没有台词")
    return meta, lines


def chunk_lines(lines, max_chars):
    chunks, cur, size = [], [], 0
    for l in lines:
        n = len(l["text"])
        if cur and size + n > max_chars:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(l)
        size += n
    if cur:
        chunks.append(cur)
    return chunks


def build_prompt(meta, chunk):
    roles = "、".join(f"{r['name']}（@{r['voice']}{'，' + r['desc'] if r['desc'] else ''}）" for r in meta["roles"])
    head = [f"角色：{roles}"]
    scene = meta["scene"] or "两人在安静的播客录音室对谈"
    if meta["music"]:
        scene += f"。[背景音乐] {meta['music']}"
    head.append(f"场景：{scene}")
    body = [f"{{speaker：{l['speaker']}}}{l['text']}" for l in chunk]
    return "\n".join(head) + "\n\n" + "\n".join(body)


# ---------- 背景音乐 ----------
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"}
INPUT_DIR = Path("input")
DEFAULT_BGM_STYLE = "Lo-fi hip hop，温暖的电钢琴与轻柔鼓点，节奏舒缓，不抢人声，适合播客垫底"


def resolve_bgm(spec, dry_run=False):
    """spec：None/无 → 不加；生成[：风格描述] → 调用同级 music-gen 生成；其他 → 文件名，先当前目录再 input/ 目录找。"""
    if not spec or spec.strip() in ("无", "none", "no", "否"):
        return None
    spec = spec.strip()
    m = re.match(rf"^(生成|generate)(?:\s*{COLON}?\s*(.*))?$", spec, re.I)
    if m:
        style = (m.group(2) or "").strip() or DEFAULT_BGM_STYLE
        target = Path("output/music/podcast_bgm.mp3").resolve()
        music_py = SKILL_DIR.parent / "music-gen" / "music.py"
        if not music_py.is_file():
            sys.exit("背景音乐选了“生成”，但找不到 music-gen skill（应在 " + str(music_py) + "）。\n"
                     "请安装：git clone https://gitee.com/zengraoli/my_skills.git 后把 skills/music-gen 放到本 skill 同级目录；"
                     "或改为自备：把音乐文件放进 input/ 目录并用文件名指定。")
        if target.is_file():
            print(f"背景音乐：复用已生成的 {target}（想换风格请删掉它再跑）")
            return target
        if dry_run:
            print(f"背景音乐：将调用 music-gen 生成（风格：{style}），约 0.5 元 → {target}")
            return target
        print(f"背景音乐：调用 music-gen 生成纯音乐（风格：{style}），约 0.5 元…", flush=True)
        r = subprocess.run([sys.executable, "-X", "utf8", str(music_py), "gen", "--instrumental", "--prompt", style,
                            "--style", style, "--duration", "120", "--title", "podcast_bgm", "--no-cover"],
                           text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0 or not target.is_file():
            sys.exit("music-gen 生成背景音乐失败，见上方输出；可改为自备文件放进 input/。")
        return target
    for cand in (Path(spec), INPUT_DIR / spec):
        if cand.is_file():
            return cand.resolve()
    have = sorted(p.name for p in INPUT_DIR.glob("*") if p.suffix.lower() in AUDIO_EXTS) if INPUT_DIR.is_dir() else []
    sys.exit(f"背景音乐文件不存在：{spec}\n请把音乐文件放到 {INPUT_DIR.resolve()} 目录并用文件名指定"
             + (f"；目前 input/ 里有：{', '.join(have)}" if have else "；目前 input/ 里没有音频文件")
             + "。或改为 --bgm 生成 / 脚本里写 # 背景音乐：生成。")


# ---------- A1 调用 ----------
def a1_generate(prompt, fmt="mp3", retries=3):
    body = {"model": cfg("AUDIO_MODEL"), "prompt": prompt, "audio_config": {"format": fmt, "enable_subtitle": True}}
    for attempt in range(1, retries + 1):
        r = requests.post(BASE + "/v1/audio/generate", headers=headers(), json=body, timeout=900)
        try:
            j = r.json()
        except ValueError:
            j = {"raw": r.text[:300]}
        if r.status_code == 200 and j.get("audio_url"):
            return j
        msg = json.dumps(j, ensure_ascii=False)[:300]
        if r.status_code in (429, 500, 502, 503) and attempt < retries:
            print(f"    HTTP {r.status_code}，{15 * attempt}s 后重试：{msg}", flush=True)
            time.sleep(15 * attempt)
            continue
        sys.exit(f"A1 生成失败（HTTP {r.status_code}）：{msg}")


# ---------- 字幕 ----------
def strip_cues(t):
    return re.sub(r"[（(][^（）()]{0,12}[）)]", "", t)


def norm(t):
    return re.sub(r"[\s，。！？、；：,.!?;:\"'“”‘’…—\-]", "", t)


def attribute_speakers(sentences, chunk):
    """字幕里没有说话人，按脚本行顺序对齐：句子文本（去标点）落在哪一行里就归谁。"""
    texts = [norm(strip_cues(l["text"])) for l in chunk]
    i, out = 0, []
    for s in sentences:
        key = norm(s.get("text", ""))
        j = i
        while j < len(texts) and key and key[: min(8, len(key))] not in texts[j]:
            j += 1
        if j < len(texts):
            i = j
        out.append(chunk[i]["speaker"] if i < len(chunk) else chunk[-1]["speaker"])
    return out


def srt_time(ms):
    ms = int(round(ms))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ---------- 命令 ----------
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
            print(f"  [{' OK ' if good else '失败'}] 鉴权  HTTP {r.status_code}")
            ok &= good
        except Exception as e:
            print(f"  [失败] 连接 {BASE}：{e}")
            ok = False
    print(f"  [ -- ] 配置  model={cfg('AUDIO_MODEL')} chunk_chars={cfg('CHUNK_CHARS')} gap={cfg('CHUNK_GAP')}s")
    print("\n环境正常。" if ok else "\n有缺失项，处理后重试。")
    sys.exit(0 if ok else 1)


def cmd_render(args):
    load_env(SKILL_DIR / ".env")
    script_path = Path(args.script)
    meta, lines = parse_script(script_path.read_text(encoding="utf-8-sig"))
    chunks = chunk_lines(lines, int(args.chunk_chars or cfg("CHUNK_CHARS")))
    total_chars = sum(len(l["text"]) for l in lines)
    est_sec = total_chars / CHARS_PER_SEC
    title = args.title or meta["title"] or script_path.stem
    print(f"脚本《{title}》：{len(lines)} 行台词，{total_chars} 字，分 {len(chunks)} 段，"
          f"预计 {est_sec/60:.1f} 分钟，约 {est_sec*PRICE_PER_SEC:.2f} 元")
    for k, c in enumerate(chunks, 1):
        n = sum(len(l["text"]) for l in c)
        print(f"  段 {k}: 第 {c[0]['n']}–{c[-1]['n']} 行，{n} 字 ≈ {n/CHARS_PER_SEC:.0f} 秒")
    bgm_spec = args.bgm if args.bgm is not None else meta.get("bgm")
    bgm_path = resolve_bgm(bgm_spec, dry_run=args.dry_run)
    if not (bgm_spec and re.match(r"^\s*(生成|generate)", bgm_spec, re.I)):   # “生成”分支已在 resolve_bgm 里打印过
        print(f"背景音乐：{bgm_path if bgm_path else '无'}")
    if args.dry_run:
        print("\n--- 第 1 段提示词 ---\n" + build_prompt(meta, chunks[0]))
        return

    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    if not (ffmpeg and ffprobe):
        sys.exit("找不到 ffmpeg/ffprobe，加入 PATH 或在 .env 设 FFMPEG_DIR")
    out_dir = Path(args.out_dir).resolve()      # 绝对路径：ffmpeg concat 列表里的相对路径是相对列表文件的，必须用绝对
    work = out_dir / f"{title}_chunks"
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy(script_path, out_dir / f"{title}_script.txt")

    gap = float(cfg("CHUNK_GAP"))
    offset_ms, srt_items, cost_sec, parts, reused = 0.0, [], 0.0, [], 0
    for k, c in enumerate(chunks, 1):
        prompt = build_prompt(meta, c)
        prompt_path = work / f"chunk{k:02d}_prompt.txt"
        mp3 = work / f"chunk{k:02d}.mp3"
        resp_path = work / f"chunk{k:02d}_response.json"
        same_prompt = prompt_path.is_file() and prompt_path.read_text(encoding="utf-8") == prompt
        if args.reuse_chunks and same_prompt and mp3.is_file() and resp_path.is_file():
            j = json.loads(resp_path.read_text(encoding="utf-8"))
            reused += 1
            print(f"[{k}/{len(chunks)}] 复用已有分段（提示词未变）", flush=True)
        else:
            prompt_path.write_text(prompt, encoding="utf-8")
            print(f"[{k}/{len(chunks)}] 生成中…", flush=True)
            j = a1_generate(prompt)
            resp_path.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
            mp3.write_bytes(requests.get(j["audio_url"], timeout=300).content)
            cost_sec += float(j.get("model_duration") or 0)
        dur = duration_of(ffprobe, mp3)
        sents = (j.get("subtitle") or {}).get("sentences") or []
        for s, who in zip(sents, attribute_speakers(sents, c)):
            srt_items.append((offset_ms + s["start_time"], offset_ms + s["end_time"], who, s.get("text", "")))
        print(f"    {dur:.1f} 秒，{len(sents)} 句字幕", flush=True)
        parts.append(mp3)
        offset_ms += dur * 1000 + gap * 1000

    # 拼接（段间留 gap 秒静音）
    sr = run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate", "-of", "csv=p=0", str(parts[0])]).stdout.strip() or "32000"
    silence = work / "gap.mp3"
    run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", f"anullsrc=r={sr}:cl=mono", "-t", f"{gap}", "-c:a", "libmp3lame", "-q:a", "9", str(silence)])
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" + (f"file '{silence.as_posix()}'\n" if i < len(parts) - 1 else "")
                           for i, p in enumerate(parts)), encoding="utf-8")
    voice = work / "voice.mp3"
    run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "libmp3lame", "-q:a", "2", str(voice)])

    final = out_dir / f"{title}.mp3"
    intro_ms = 0
    if bgm_path:
        # 背景乐：先 intro 秒纯音乐，人声进入后压到 bgm_gain 并随人声闭音；结尾淡出
        intro, outro = float(args.bgm_intro), 3.0
        vlen = duration_of(ffprobe, voice)
        total = vlen + intro + outro
        # apad：人声末尾补上 outro 秒静音，否则 sidechaincompress 会在人声结束时截断整条输出，淡出做不完
        flt = (f"[1:a]adelay={int(intro*1000)}|{int(intro*1000)},apad=pad_dur={outro + 0.5:.1f},asplit=2[v1][v2];"
               f"[0:a]atrim=0:{total:.3f},volume={args.bgm_gain}dB,afade=t=in:st=0:d=1.5,afade=t=out:st={total-outro:.3f}:d={outro}[b];"
               f"[b][v2]sidechaincompress=threshold=0.03:ratio=8:attack=30:release=600[bd];"
               f"[bd][v1]amix=inputs=2:duration=longest:normalize=0[a]")
        run([ffmpeg, "-y", "-v", "error", "-stream_loop", "-1", "-i", str(bgm_path), "-i", str(voice),
             "-filter_complex", flt, "-map", "[a]", "-t", f"{total:.3f}", "-c:a", "libmp3lame", "-q:a", "2", str(final)])
        intro_ms = int(intro * 1000)
    else:
        shutil.copy(voice, final)

    # SRT
    srt = out_dir / f"{title}.srt"
    with open(srt, "w", encoding="utf-8") as f:
        for i, (a, b, who, text) in enumerate(srt_items, 1):
            f.write(f"{i}\n{srt_time(a + intro_ms)} --> {srt_time(b + intro_ms)}\n{who}：{text}\n\n")

    total_len = duration_of(ffprobe, final)
    print(f"\n完成：{final}  （{total_len/60:.1f} 分钟）\n字幕：{srt}  （{len(srt_items)} 句）\n"
          f"费用：本次生成 {cost_sec:.0f} 秒 ≈ {cost_sec*PRICE_PER_SEC:.2f} 元"
          + (f"（复用 {reused} 段未计费）" if reused else "") + f"\n分段文件：{work}")


def main():
    ap = argparse.ArgumentParser(description="双人播客渲染（SenseAudio A1，配置见同目录 .env）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="环境与 key 检查")
    p = sub.add_parser("render", help="把对谈脚本渲染成 mp3 + srt")
    p.add_argument("--script", required=True, help="脚本文件（格式见文件头注释）")
    p.add_argument("--title", help="输出文件名，默认取脚本里的标题")
    p.add_argument("--out-dir", default="output/podcast")
    p.add_argument("--chunk-chars", type=int, help="每段最多字数（默认 .env 的 CHUNK_CHARS=450，≈95 秒）")
    p.add_argument("--bgm", default=None, help="背景音乐：无 | 生成[：风格描述]（调用同级 music-gen）| 文件名（先当前目录再 input/ 找）；不传则用脚本里的“# 背景音乐：”")
    p.add_argument("--bgm-gain", type=float, default=-18, help="背景音乐音量 dB（默认 -18）")
    p.add_argument("--bgm-intro", type=float, default=2.5, help="人声进入前的纯音乐秒数（默认 2.5）")
    p.add_argument("--reuse-chunks", action="store_true", help="分段目录里已有且提示词未变的段直接复用（换 BGM / 修拼接时不重复扣费）")
    p.add_argument("--dry-run", action="store_true", help="只打印分段和第 1 段提示词，不调用接口")
    args = ap.parse_args()
    {"check": cmd_check, "render": cmd_render}[args.cmd](args)


if __name__ == "__main__":
    main()
