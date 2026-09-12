#!/usr/bin/env python3
"""suno-music：用 Suno（经 TTAPI）生成歌曲、锁定音色、给自己的伴奏配唱、翻唱与续写。

子命令：
  python suno.py check                                             # 环境 / key / 鉴权
  python suno.py gen --lyrics 词.txt --style "古风,女声" --title 长安月冷
  python suno.py gen --idea "夏夜海边久别重逢" --style "中文流行"     # 灵感模式，模型自己写词
  python suno.py gen --instrumental --style "Lo-fi 钢琴" --title bgm  # 纯音乐（做 BGM）
  python suno.py gen --lyrics 词.txt --persona 温柔女声A              # 用锁定的音色唱
  python suno.py persona add --from 长安月冷 --name 温柔女声A [--start 12 --end 40]
  python suno.py persona list
  python suno.py sing --backing <公开URL> --lyrics 词.txt            # 给自己的伴奏配唱
  python suno.py cover --from 长安月冷 --style "民谣吉他,男声"        # 翻唱/改编
  python suno.py extend --from 长安月冷 [--at 55]                    # 续写加长
  python suno.py stems --from 长安月冷                               # 人声/伴奏分轨
  python suno.py intro --song x.mp3 --text "谢谢每一个……"            # 截前奏 / 加开场独白（本地，免费）
  python suno.py list                                               # 本地歌曲与音色库

配置见同目录 .env。产物与索引默认在 output/suno/。
"""
import argparse
import asyncio
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
DEFAULTS = {
    "SUNO_PROVIDER": "ttapi",
    "TTAPI_BASE": "https://api.ttapi.io",
    "T8STAR_BASE": "https://ai.t8star.org",
    "SUNO_MODEL": "chirp-v6",
    "INTRO_TTS": "edge",
    "EDGE_VOICE": "zh-CN-XiaoxiaoNeural",
    "SENSEAUDIO_VOICE": "female_0038_b",
    "OUT_DIR": "output/suno",
}
# 实测单价（TTAPI，100 quota = $1）
COST = {"gen": 3, "cover": 3, "extend": 3, "add-vocals": 3, "stems": 6, "persona": 0, "upload": 0.25}


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


def provider():
    return (cfg("SUNO_PROVIDER") or "ttapi").lower()


def api():
    """返回 (base_url, headers)。"""
    if provider() == "t8star":
        key = os.environ.get("T8STAR_KEY")
        if not key:
            sys.exit(f"缺少 T8STAR_KEY：请在 {SKILL_DIR / '.env'} 里填写")
        return cfg("T8STAR_BASE").rstrip("/"), {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    key = os.environ.get("TTAPI_KEY")
    if not key:
        sys.exit(f"缺少 TTAPI_KEY：请在 {SKILL_DIR / '.env'} 里填写（https://ttapi.io 注册后获取）")
    return cfg("TTAPI_BASE").rstrip("/"), {"TT-API-KEY": key, "Content-Type": "application/json"}


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
        sys.exit(f"命令失败（{Path(cmd[0]).name}，退出码 {r.returncode}）：\n{(r.stderr or r.stdout).strip()[-1200:]}")
    return r


def out_dir():
    d = Path(cfg("OUT_DIR")).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------- 本地库：歌名 → musicId，音色名 → persona_id ----------
def lib_path():
    return out_dir() / "library.json"


def load_lib():
    p = lib_path()
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"songs": {}, "personas": {}}


def save_lib(lib):
    lib_path().write_text(json.dumps(lib, ensure_ascii=False, indent=1), encoding="utf-8")


def resolve_song(ref):
    """把歌名或 musicId 解析成 musicId。"""
    lib = load_lib()
    if ref in lib["songs"]:
        return lib["songs"][ref]["music_id"]
    for name, v in lib["songs"].items():
        if v.get("music_id") == ref:
            return ref
    if len(ref) > 20 and "-" in ref:      # 看着就是个 id
        return ref
    known = "、".join(lib["songs"]) or "（空）"
    sys.exit(f"找不到歌曲「{ref}」。本地库里有：{known}\n用  suno.py list  查看，或直接传 musicId。")


def resolve_persona(ref):
    lib = load_lib()
    if ref in lib["personas"]:
        return lib["personas"][ref]["persona_id"]
    if len(ref) > 20 and "-" in ref:
        return ref
    known = "、".join(lib["personas"]) or "（空）"
    sys.exit(f"找不到音色「{ref}」。本地库里有：{known}\n用  suno.py persona add  先创建。")


# ---------- TTAPI 调用 ----------
def charge_note(op, extra=""):
    q = COST.get(op)
    tip = f"约 {q} quota ≈ ¥{q * 0.067:.2f}" if q else "计费未知"
    print(f">>> [付费调用] {op} {extra}（{tip}）", flush=True)


def post(path, body, timeout=180):
    base, H = api()
    r = requests.post(base + path, headers=H, json=body, timeout=timeout)
    try:
        j = r.json()
    except ValueError:
        j = {"raw": r.text[:400]}
    if r.status_code != 200 or j.get("status") == "FAILED":
        sys.exit(f"{path} 失败（HTTP {r.status_code}）：{json.dumps(j, ensure_ascii=False)[:400]}")
    return j


def wait_job(job, tag, limit=900):
    base, H = api()
    t0 = time.time()
    last = None
    while time.time() - t0 < limit:
        f = requests.get(base + "/suno/v2/fetch", headers=H, params={"jobId": job}, timeout=60).json()
        st = f.get("status")
        if st != last:
            print(f"    {time.time()-t0:5.0f}s {st}", flush=True)
            last = st
        if st in ("SUCCESS", "FAILED", "FAILURE", "ERROR"):
            break
        time.sleep(8)
    else:
        sys.exit(f"超时未完成，可稍后查询 jobId={job}")
    if st != "SUCCESS":
        sys.exit(f"生成失败：{json.dumps(f, ensure_ascii=False)[:400]}")
    (out_dir() / f"{tag}_result.json").write_text(json.dumps(f, ensure_ascii=False, indent=1), encoding="utf-8")
    return f


def save_musics(f, title, tag, note=""):
    """下载歌曲、登记到本地库，返回文件列表。"""
    d = f.get("data") or {}
    musics = d.get("musics") or []
    lib = load_lib()
    files = []
    for i, m in enumerate(musics, 1):
        name = title if len(musics) == 1 else f"{title}_{i}"
        mp3 = out_dir() / f"{name}.mp3"
        if m.get("audioUrl"):
            mp3.write_bytes(requests.get(m["audioUrl"], timeout=300).content)
            files.append(mp3)
        lib["songs"][name] = {"music_id": m.get("musicId"), "file": str(mp3), "duration": m.get("duration"),
                              "title": m.get("title"), "tags": m.get("tags"), "note": note,
                              "audio_url": m.get("audioUrl"),        # 公开链接，inspo 参考曲风时要用
                              "created": time.strftime("%Y-%m-%d %H:%M")}
        print(f"    {name}.mp3  {m.get('duration')}s")
    save_lib(lib)
    print(f"    实扣 {d.get('quota')} quota")
    return files


# ---------- 命令 ----------
def cmd_check(args):
    ok = True
    print(f"环境检查（provider={provider()}）")
    env_ok = load_env(args.env)
    print(f"  [{' OK ' if env_ok else '缺失'}] .env")
    ok &= env_ok
    keyname = "T8STAR_KEY" if provider() == "t8star" else "TTAPI_KEY"
    has = bool(os.environ.get(keyname))
    print(f"  [{' OK ' if has else '缺失'}] {keyname}")
    ok &= has
    for t in ("ffmpeg", "ffprobe"):
        p = find_tool(t)
        print(f"  [{' OK ' if p else '缺失'}] {t:<8}{p or '未找到：加入 PATH 或在 .env 设 FFMPEG_DIR'}")
        ok &= bool(p)
    if cfg("INTRO_TTS") == "edge":
        try:
            import edge_tts  # noqa
            print("  [ OK ] edge-tts    开场独白用（免费，无需 key）")
        except ImportError:
            print("  [缺失] edge-tts    pip install edge-tts（只影响 intro 子命令）")
    if has and provider() != "t8star":
        base, H = api()
        try:   # 用一个必然参数错误的请求探活：能返回 JSON 且不是鉴权错误即通
            r = requests.post(base + "/suno/v1/music", headers=H, json={}, timeout=30)
            j = r.json()
            bad = "key" in json.dumps(j, ensure_ascii=False).lower() and r.status_code in (401, 403)
            print(f"  [{'失败' if bad else ' OK '}] 鉴权        HTTP {r.status_code} {str(j.get('message'))[:60]}")
            ok &= not bad
        except Exception as e:
            print(f"  [失败] 连接 {base}：{e}")
            ok = False
    lib = load_lib()
    print(f"  [ -- ] 模型 {cfg('SUNO_MODEL')}；本地库 {len(lib['songs'])} 首歌 / {len(lib['personas'])} 个音色；产物目录 {out_dir()}")
    print("\n环境正常。" if ok else "\n有缺失项，处理后重试。")
    sys.exit(0 if ok else 1)


def read_lyrics(val):
    if not val:
        return None
    p = Path(val)
    return p.read_text(encoding="utf-8-sig") if p.is_file() else val


def cmd_gen(args):
    load_env(args.env)
    if provider() == "t8star":
        return gen_t8star(args)
    title = args.title or f"song_{int(time.time())}"
    body = {"mv": args.model or cfg("SUNO_MODEL"), "title": title[:80],
            "custom": not bool(args.idea), "instrumental": bool(args.instrumental)}
    lyr = read_lyrics(args.lyrics)
    if args.idea:
        body["gpt_description_prompt"] = args.idea
    elif lyr:
        body["prompt"] = lyr
    elif not args.instrumental:
        sys.exit("要么给 --lyrics（自带歌词），要么给 --idea（让模型写词），纯音乐用 --instrumental")
    else:
        body["prompt"] = ""
    if args.style:
        body["tags"] = args.style[:1000]
    if args.negative:
        body["negative_tags"] = args.negative
    if args.gender:
        body["vocal_gender"] = args.gender.capitalize()
    if args.persona:
        body["persona_id"] = resolve_persona(args.persona)
        body["custom"] = True          # persona 仅自定义模式生效
    if args.variety:
        body["variety"] = args.variety
    if args.duration:                  # 实测按时长截断：唱不完的歌词直接丢掉，不会加快语速
        if args.idea or not 10 <= args.duration <= 360:
            sys.exit("--duration 只在自带歌词/纯音乐时生效，范围 10～360 秒")
        body["duration"] = int(args.duration)
    charge_note("gen", f"{title}" + (f" / 音色 {args.persona}" if args.persona else ""))
    (out_dir() / f"{title}_request.json").write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    j = post("/suno/v1/music", body)
    f = wait_job(j["data"]["jobId"], title)
    save_musics(f, title, title, note="gen" + (f"/persona:{args.persona}" if args.persona else ""))
    print("\n提示：前奏太长或想加开场独白，用  suno.py intro --song <文件> [--text ...]（本地处理，免费）")


def gen_t8star(args):
    """t8star 只实现基础生成（其余端点无公开文档）。"""
    base, H = api()
    title = args.title or f"song_{int(time.time())}"
    body = {"mv": args.model or "chirp-v5", "title": title,
            "prompt": read_lyrics(args.lyrics) or "", "tags": args.style or ""}
    if args.idea:
        body["gpt_description_prompt"] = args.idea
    charge_note("gen", f"{title}（t8star，฿0.5 ≈ ¥0.68）")
    r = requests.post(base + "/suno/submit/music", headers=H, json=body, timeout=60)
    j = r.json()
    tid = j.get("data") if isinstance(j.get("data"), str) else None
    if not tid:
        sys.exit(f"提交失败：{json.dumps(j, ensure_ascii=False)[:300]}")
    t0 = time.time()
    while time.time() - t0 < 900:
        f = requests.get(f"{base}/suno/fetch/{tid}", headers=H, timeout=60).json()
        d = f.get("data") or {}
        if d.get("status") in ("SUCCESS", "FAILURE", "FAILED"):
            break
        time.sleep(10)
    clips = (f.get("data") or {}).get("data") or []
    lib = load_lib()
    for i, c in enumerate(clips, 1):
        name = f"{title}_{i}"
        p = out_dir() / f"{name}.mp3"
        if c.get("audio_url"):
            p.write_bytes(requests.get(c["audio_url"], timeout=300).content)
        lib["songs"][name] = {"music_id": c.get("id"), "file": str(p),
                              "duration": (c.get("metadata") or {}).get("duration"), "note": "t8star",
                              "created": time.strftime("%Y-%m-%d %H:%M")}
        print(f"    {name}.mp3  {(c.get('metadata') or {}).get('duration')}s  mv={c.get('model_name')}")
    save_lib(lib)


def cmd_sing(args):
    """给自己的伴奏配唱：upload（公开 URL）→ add-vocals。"""
    load_env(args.env)
    url = args.backing
    if not url.startswith(("http://", "https://")):
        sys.exit("--backing 必须是**公开可访问的 URL**。Suno 不接受本地文件，请先把伴奏传到对象存储 /"
                 " 静态站 / GitHub release 等能公开访问的地方，再把链接填进来。")
    title = args.title or f"sing_{int(time.time())}"
    charge_note("upload", "上传伴奏")
    up = post("/suno/v1/upload", {"audio_url": url, "is_async": False})
    mid = (up.get("data") or {}).get("music_id")
    if not mid:
        sys.exit(f"上传未返回 music_id：{json.dumps(up, ensure_ascii=False)[:300]}")
    print(f"    伴奏 music_id = {mid}")
    lyr = read_lyrics(args.lyrics)
    if not lyr:
        sys.exit("--lyrics 必填：要唱的词")
    body = {"music_id": mid, "custom": True, "mv": args.model or cfg("SUNO_MODEL"),
            "prompt": lyr, "title": title[:80]}
    if args.style:
        body["tags"] = args.style
    if args.gender:
        body["vocal_gender"] = args.gender.capitalize()
    charge_note("add-vocals", "在伴奏上配唱")
    (out_dir() / f"{title}_request.json").write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    j = post("/suno/v1/add-vocals", body)
    f = wait_job(j["data"]["jobId"], title)
    save_musics(f, title, title, note=f"sing/backing:{url[:60]}")


def cmd_cover(args):
    load_env(args.env)
    title = args.title or f"cover_{int(time.time())}"
    if args.backing:        # 翻唱外部歌曲：upload-cover 直接收 URL
        body = {"audio_url": args.backing, "mv": args.model or cfg("SUNO_MODEL"), "title": title[:80],
                "custom": True, "instrumental": False}
        if not args.lyrics:
            sys.exit("翻唱外部歌曲必须给 --lyrics（接口要求）；不知道原词可先用 asr-tts 的 lyrics 提取")
        body["prompt"] = read_lyrics(args.lyrics)
        if args.style:
            body["tags"] = args.style
        charge_note("cover", "翻唱上传的音频")
        j = post("/suno/v1/upload-cover", body)
    else:
        body = {"music_id": resolve_song(args.source), "mv": args.model or cfg("SUNO_MODEL"),
                "title": title[:80], "custom": True}
        if args.lyrics:
            body["prompt"] = read_lyrics(args.lyrics)
        if args.style:
            body["tags"] = args.style
        if args.gender:
            body["vocal_gender"] = args.gender.capitalize()
        if args.persona:          # 文档未列出，但实测生效：翻唱时换成指定音色（同词同曲换嗓子）
            body["persona_id"] = resolve_persona(args.persona)
        charge_note("cover", f"翻唱《{args.source}》" + (f" / 换成音色 {args.persona}" if args.persona else ""))
        j = post("/suno/v1/cover", body)
    f = wait_job(j["data"]["jobId"], title)
    save_musics(f, title, title, note="cover")


def cmd_inspo(args):
    """参考一首或几首歌的曲风，写一首新歌（不保留原旋律）。"""
    load_env(args.env)
    title = args.title or f"inspo_{int(time.time())}"
    urls = []
    lib = load_lib()
    for ref in args.ref:
        if ref.startswith(("http://", "https://")):
            urls.append(ref)
            continue
        url = (lib["songs"].get(ref) or {}).get("audio_url")
        if not url:
            sys.exit(f"「{ref}」在本地库里没有公开链接（inspo 需要公开 URL）；可直接传 URL，或用 list 查看歌名")
        urls.append(url)
    if not 1 <= len(urls) <= 4:
        sys.exit("--ref 给 1～4 首参考歌（本地库歌名或公开 URL）")
    body = {"audio_urls": urls, "mv": args.model or cfg("SUNO_MODEL"), "title": title[:80]}
    lyr = read_lyrics(args.lyrics)
    if lyr:
        body["prompt"] = lyr
    if args.style:
        body["tags"] = args.style
    if args.gender:
        body["vocal_gender"] = args.gender.capitalize()
    charge_note("gen", f"参考 {len(urls)} 首歌的曲风写《{title}》")
    j = post("/suno/v1/inspo", body)
    f = wait_job(j["data"]["jobId"], title)
    save_musics(f, title, title, note="inspo")


def cmd_extend(args):
    load_env(args.env)
    title = args.title or f"extend_{int(time.time())}"
    mid = resolve_song(args.source)
    at = args.at
    if at is None:          # continue_at 必填；默认从结尾往回 5 秒处接，衔接更自然
        dur = float((load_lib()["songs"].get(args.source) or {}).get("duration") or 0)
        if not dur:
            sys.exit("--at 必填：从第几秒开始续（本地库里没有这首歌的时长）")
        at = max(dur - 5, 1)
    body = {"music_id": mid, "mv": args.model or cfg("SUNO_MODEL"),
            "title": title[:80], "custom": True, "continue_at": at}
    if args.lyrics:
        body["prompt"] = read_lyrics(args.lyrics)
    if args.style:
        body["tags"] = args.style
    charge_note("extend", f"续写《{args.source}》")
    j = post("/suno/v1/extend", body)
    f = wait_job(j["data"]["jobId"], title + "_片段")
    # extend 只返回新接的那一段；concat（实测不扣费）把它和原曲拼成整首
    full = []
    for m in (f.get("data") or {}).get("musics") or []:
        c = post("/suno/v1/concat", {"music_id": m["musicId"]})
        full += (wait_job(c["data"]["jobId"], title + "_concat").get("data") or {}).get("musics") or []
    save_musics({"data": {"musics": full, "quota": (f.get("data") or {}).get("quota")}}, title, title,
                note=f"extend/{args.source}@{at}s")


def cmd_stems(args):
    load_env(args.env)
    title = args.title or f"stems_{int(time.time())}"
    charge_note("stems", f"分轨《{args.source}》" + (f" / 提取 {args.type}" if args.type else ""))
    body = {"music_id": resolve_song(args.source)}
    if args.type:
        body["stem_type"] = args.type
    j = post("/suno/v1/stems", body)
    f = wait_job(j["data"]["jobId"], title)
    d = f.get("data") or {}
    # 返回形如「歌名 (Lead Vocal)」「歌名 (Without Lead Vocal)」，通常两组（两次分离结果）
    seen = {}
    for m in (d.get("musics") or []):
        if not m.get("audioUrl"):
            continue
        part = re.search(r"\(([^)]*)\)\s*$", m.get("title") or "")
        part = (part.group(1) if part else "track").replace(" ", "_")
        seen[part] = seen.get(part, 0) + 1
        p = out_dir() / f"{title}_{part}_{seen[part]}.mp3"
        p.write_bytes(requests.get(m["audioUrl"], timeout=300).content)
        print(f"    {p.name}  {m.get('duration')}s")
    if not seen:
        print(f"    未识别到音轨链接，完整返回见 {title}_result.json")
    print(f"    实扣 {d.get('quota')} quota")


def cmd_persona(args):
    load_env(args.env)
    lib = load_lib()
    if args.sub == "list":
        if not lib["personas"]:
            print("还没有音色。用  suno.py persona add --from <歌名> --name <音色名>  创建。")
        for name, v in lib["personas"].items():
            print(f"  {name:<16} 来自《{v.get('from')}》  {v.get('describe') or ''}  id={v['persona_id'][:8]}…")
        return
    body = {"music_id": resolve_song(args.source), "name": args.name}
    if args.describe:
        body["describe"] = args.describe
    if args.start is not None:
        body["vocal_start_s"] = args.start
    if args.end is not None:
        body["vocal_end_s"] = args.end
    if args.styles:
        body["styles"] = args.styles
    charge_note("persona", f"从《{args.source}》提取音色「{args.name}」")
    j = post("/suno/v1/persona", body)
    pid = (j.get("data") or {}).get("persona_id")
    if not pid:
        sys.exit(f"未返回 persona_id：{json.dumps(j, ensure_ascii=False)[:300]}")
    lib["personas"][args.name] = {"persona_id": pid, "from": args.source, "describe": args.describe,
                                  "created": time.strftime("%Y-%m-%d %H:%M")}
    save_lib(lib)
    print(f"    音色「{args.name}」已保存  persona_id={pid}")
    print(f"    以后这样用：suno.py gen --lyrics 新词.txt --persona {args.name}")


def cmd_list(args):
    load_env(args.env)
    lib = load_lib()
    print(f"歌曲（{len(lib['songs'])}）：")
    for name, v in lib["songs"].items():
        print(f"  {name:<24} {str(v.get('duration') or '?'):>7}s  {v.get('note') or ''}  {v.get('created') or ''}")
    print(f"\n音色（{len(lib['personas'])}）：")
    for name, v in lib["personas"].items():
        print(f"  {name:<24} 来自《{v.get('from')}》 {v.get('created') or ''}")


# ---------- intro：本地处理，不花钱 ----------
def tts_edge(text, voice, speed, out_path):
    import edge_tts
    rate = f"{int(round((speed - 1) * 100)):+d}%"
    asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(str(out_path)))


def tts_senseaudio(text, voice, speed, out_path):
    key = os.environ.get("SENSEAUDIO_API_KEY")
    if not key:
        sys.exit("INTRO_TTS=senseaudio 但 .env 里没有 SENSEAUDIO_API_KEY；改用 INTRO_TTS=edge（免费）")
    r = requests.post("https://api.senseaudio.cn/v1/t2a_v2", headers={"Authorization": f"Bearer {key}"},
                      json={"model": "sensenova-tts-2.0", "text": text, "stream": False,
                            "voice_setting": {"voice_id": voice, "speed": speed},
                            "audio_setting": {"format": "mp3", "sample_rate": 32000, "channel": 1}}, timeout=120)
    a = (r.json().get("data") or {}).get("audio")
    if not a:
        sys.exit(f"SenseAudio TTS 失败：{r.text[:300]}")
    out_path.write_bytes(bytes.fromhex(a))


def vocal_onset(song):
    """粗测人声起点：找第一个持续超过阈值的响度跳变；失败返回 None。"""
    ffmpeg = find_tool("ffmpeg")
    tmp = song.with_suffix(".onset.wav")
    run([ffmpeg, "-y", "-v", "error", "-i", str(song), "-ac", "1", "-ar", "8000", str(tmp)])
    try:
        import wave
        import numpy as np                      # audioop 在 Python 3.13 已移除，用 numpy 算 RMS
        with wave.open(str(tmp)) as w:
            fr = w.getframerate()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)
        step = fr // 4
        if len(data) < step * 2:
            return None
        levels = [float(np.sqrt(np.mean(data[i:i + step] ** 2))) for i in range(0, len(data) - step, step)]
        if not levels:
            return None
        base = sorted(levels)[len(levels) // 5] or 1
        for i, v in enumerate(levels):
            if v > base * 3.5 and all(x > base * 2 for x in levels[i:i + 4]):
                return i * 0.25
        return 0.0          # 没有明显跃变：说明一上来就唱（Suno 多是这样），前奏视作 0
    except Exception:
        return None
    finally:
        tmp.unlink(missing_ok=True)


def cmd_intro(args):
    load_env(args.env)
    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    if not (ffmpeg and ffprobe):
        sys.exit("找不到 ffmpeg/ffprobe，加入 PATH 或在 .env 设 FFMPEG_DIR")
    song = Path(args.song).resolve()
    if not song.is_file():
        sys.exit(f"找不到文件：{song}")
    out = Path(args.out) if args.out else song.with_name(song.stem + ("_with_intro" if args.text else "_short_intro") + ".mp3")
    onset = args.onset if args.onset is not None else vocal_onset(song)
    if onset is None:
        sys.exit("没测出人声起点，请用 --onset <秒> 手动指定（用播放器看一下第一句在第几秒）")
    print(f"人声起点 ≈ {onset:.1f} 秒" + ("（几乎一开口就唱，没有长前奏）" if onset < 2 else ""))

    if not args.text:
        if onset <= args.keep + 0.5:
            print(f"前奏本来就只有 {onset:.1f} 秒，无需截断。")
            return
        start = onset - args.keep
        run([ffmpeg, "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(song),
             "-af", "afade=t=in:st=0:d=1.5", "-c:a", "libmp3lame", "-q:a", "2", str(out)])
        print(f"已把前奏截到 {args.keep:.1f} 秒 → {out}")
        return

    spoken = song.with_name(song.stem + "_intro_tts.mp3")
    engine = (args.tts or cfg("INTRO_TTS")).lower()
    voice = args.voice or (cfg("EDGE_VOICE") if engine == "edge" else cfg("SENSEAUDIO_VOICE"))
    print(f"独白 TTS：{engine} / {voice}")
    (tts_edge if engine == "edge" else tts_senseaudio)(args.text, voice, args.speed, spoken)
    s_len = float(run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(spoken)]).stdout.strip())
    need = s_len + args.lead
    if onset >= need:
        # 前奏够长：把独白压在前奏上，念完正好进人声（SenseAudio 那种长前奏的歌）
        start = onset - need
        duck_until = s_len + 0.4
        flt = (f"[1:a]afade=t=in:st=0:d=1.5,volume='if(lt(t,{duck_until:.3f}),{args.duck},1)':eval=frame[m];"
               f"[0:a]adelay=400|400[v];[v][m]amix=inputs=2:duration=longest:normalize=0[a]")
        run([ffmpeg, "-y", "-v", "error", "-i", str(spoken), "-ss", f"{start:.3f}", "-i", str(song),
             "-filter_complex", flt, "-map", "[a]", "-c:a", "libmp3lame", "-q:a", "2", str(out)])
        print(f"独白 {s_len:.1f} 秒压在 {need:.1f} 秒前奏上（伴奏压到 {args.duck}），念完 {args.lead} 秒进人声 → {out}")
    else:
        # 前奏不够（Suno 常见）：独白放在歌前面，底下垫一段淡入的歌曲开头，念完再进整首歌
        bed = args.lead
        flt = (f"[1:a]atrim=0:{need:.3f},afade=t=in:st=0:d=1.0,volume={args.duck},adelay=0|0[bed];"
               f"[0:a]adelay=400|400[v];[v][bed]amix=inputs=2:duration=longest:normalize=0[head];"
               f"[2:a]adelay={int(need*1000)}|{int(need*1000)}[body];[head][body]amix=inputs=2:duration=longest:normalize=0[a]")
        run([ffmpeg, "-y", "-v", "error", "-i", str(spoken), "-i", str(song), "-i", str(song),
             "-filter_complex", flt, "-map", "[a]", "-c:a", "libmp3lame", "-q:a", "2", str(out)])
        print(f"这首歌没有长前奏，已把 {s_len:.1f} 秒独白接在歌曲前面（底下垫了 {bed:.1f} 秒淡入的乐句）→ {out}")


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description="Suno 音乐生成与后期（配置见同目录 .env）")
    ap.add_argument("--env", help="配置文件路径，默认脚本同目录 .env")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="环境与鉴权检查")
    sub.add_parser("list", help="列出本地歌曲与音色库")

    p = sub.add_parser("gen", help="生成歌曲 / 纯音乐")
    p.add_argument("--lyrics", help="歌词文件或文本（自定义模式）")
    p.add_argument("--idea", help="一句话主题（灵感模式，模型自己写词）")
    p.add_argument("--style", help="曲风标签：乐器、情绪、速度、人声特点")
    p.add_argument("--negative", help="不想要的风格")
    p.add_argument("--gender", choices=["male", "female"], help="人声性别")
    p.add_argument("--persona", help="用已保存的音色名（锁音色）")
    p.add_argument("--instrumental", action="store_true", help="纯音乐，不唱")
    p.add_argument("--variety", choices=["off", "normal", "high", "extra", "max"])
    p.add_argument("--duration", type=float, help="目标时长秒（10～360）；歌词唱不完的部分会被截掉")
    p.add_argument("--model", help="覆盖 .env 的 SUNO_MODEL")
    p.add_argument("--title")

    p = sub.add_parser("sing", help="给自己的伴奏配唱（上传 + 加人声）")
    p.add_argument("--backing", required=True, help="伴奏的公开 URL（Suno 不收本地文件）")
    p.add_argument("--lyrics", required=True, help="要唱的词")
    p.add_argument("--style")
    p.add_argument("--gender", choices=["male", "female"])
    p.add_argument("--model")
    p.add_argument("--title")

    p = sub.add_parser("cover", help="翻唱/改编：本地库里的歌，或上传一首外部歌曲")
    p.add_argument("--from", dest="source", help="本地库里的歌名或 musicId")
    p.add_argument("--backing", help="外部歌曲的公开 URL（走 upload-cover）")
    p.add_argument("--lyrics", help="换词（不给则沿用原词）")
    p.add_argument("--style", help="新的曲风")
    p.add_argument("--gender", choices=["male", "female"])
    p.add_argument("--persona", help="换成已保存的音色来唱（同词同曲换嗓子）")
    p.add_argument("--model")
    p.add_argument("--title")

    p = sub.add_parser("inspo", help="参考 1～4 首歌的曲风写新歌（不保留原旋律）")
    p.add_argument("--ref", nargs="+", required=True, help="参考歌：本地库歌名或公开 URL")
    p.add_argument("--lyrics", help="新歌词")
    p.add_argument("--style")
    p.add_argument("--gender", choices=["male", "female"])
    p.add_argument("--model")
    p.add_argument("--title")

    p = sub.add_parser("extend", help="续写加长")
    p.add_argument("--from", dest="source", required=True)
    p.add_argument("--at", type=float, help="从第几秒开始续")
    p.add_argument("--lyrics")
    p.add_argument("--style")
    p.add_argument("--model")
    p.add_argument("--title")

    p = sub.add_parser("stems", help="人声/伴奏分轨")
    p.add_argument("--from", dest="source", required=True)
    p.add_argument("--type", help="要单独提取的音轨，默认 lead_vocal（人声 + 去人声伴奏）；也可 piano、drum_kit、bass、guitar 等")
    p.add_argument("--title")

    p = sub.add_parser("persona", help="音色管理：锁定喜欢的嗓音")
    p.add_argument("sub", choices=["add", "list"])
    p.add_argument("--from", dest="source", help="音色来源歌曲（歌名或 musicId）")
    p.add_argument("--name", help="给音色起的名字")
    p.add_argument("--describe", help="音色描述，写具体些抓得更准")
    p.add_argument("--start", type=float, help="取样起点秒（选人声干净的一段，10–30 秒为宜）")
    p.add_argument("--end", type=float, help="取样终点秒")
    p.add_argument("--styles", help="风格标签")

    p = sub.add_parser("intro", help="截前奏 / 加开场独白（本地处理，不花钱）")
    p.add_argument("--song", required=True)
    p.add_argument("--text", help="开场独白文字；不给则只截前奏")
    p.add_argument("--tts", choices=["edge", "senseaudio"], help="独白用哪个 TTS，默认 .env 的 INTRO_TTS")
    p.add_argument("--voice", help="独白音色")
    p.add_argument("--speed", type=float, default=0.95)
    p.add_argument("--keep", type=float, default=5.0, help="只截前奏时保留几秒")
    p.add_argument("--lead", type=float, default=1.5, help="独白念完到人声进入的间隔")
    p.add_argument("--duck", type=float, default=0.3, help="独白期间伴奏音量 0-1")
    p.add_argument("--onset", type=float, help="手动指定人声起点秒")
    p.add_argument("--out")

    args = ap.parse_args()
    {"check": cmd_check, "gen": cmd_gen, "sing": cmd_sing, "cover": cmd_cover, "inspo": cmd_inspo, "extend": cmd_extend,
     "stems": cmd_stems, "persona": cmd_persona, "intro": cmd_intro, "list": cmd_list}[args.cmd](args)


if __name__ == "__main__":
    main()
