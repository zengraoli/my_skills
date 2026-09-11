---
name: music-gen
description: 用 SenseAudio 生成带 AI 演唱的完整歌曲或纯音乐（0.5 元/首，自带封面），并能截短前奏、在开头加一段 TTS 致谢独白。当用户要"写一首/生成一首 xx 风格的歌"、"给视频配一段背景音乐"、"这首歌前奏太长/开头加句话"时使用。参数为歌曲需求描述。
---

# AI 歌曲生成

用 SenseAudio 的音乐接口把"主题 + 歌词 + 曲风"变成一首完整歌曲（mp3 + 封面 + 歌词），并用同一平台的 TTS/ASR + ffmpeg 修整前奏或加开场独白。本 skill 目录：`SKILL.md`（本流程）、`music.py`（脚本）、`.env`（key 与默认配置）、`README.md`（人看的说明与能力边界）。

## 第 0 步：环境检查

```
python "<本 skill 目录>/music.py" check
```

缺 key、缺 ffmpeg 会逐项提示。缺 `.env` 时让用户在本目录建一个填 `SENSEAUDIO_API_KEY`，不要在聊天里索要 key。

## 第 1 步：确认需求，先说清做不到的

和用户对齐这几项后再动手；下面四条是平台限制，**做不到就直说，不要尝试用歌词标记硬做**：

- **锁定歌手音色**：没有歌手音色 id，每首歌的嗓音由模型随机决定；只能用 `--ref` 喂上一首成品当参考，相似度不保证。
- **男女对唱指定谁唱哪句**：`--gender duet` 只保证有男有女、副歌合唱，分配由模型决定；歌词里写 `[male]`、"男："之类会被**唱出来**且不改变声部。
- **观众接唱 / 现场版**：没有观众标签；`--style` 里写 "live concert, crowd singing along" 只能得到氛围，"歌手唱一句观众接一句"做不到。
- **精确时长与前奏长度**：`--duration` 只是期望值；前奏常有 30–50 秒，靠第 4 步事后截。

## 第 2 步：准备歌词

- 用户给词就用用户的；没有就由你（agent）直接写，或用 `music.py lyrics --prompt "主题" --style "..."` 让平台写（它还会返回 BPM/调式/拍号和一段很详细的英文编曲描述 `caption`，可以照抄进 `--style`）。
- 歌词**只用结构标签**：`[verse]` `[pre-chorus]` `[chorus]` `[bridge]` `[outro]`，可带序号如 `[verse 2]`。任何其他方括号（`[spoken]`、`[crowd]`、`[male]`）都会被唱出来，脚本会警告。
- 每段 4–6 行、每行 6–12 字最稳；副歌重复两遍。总长 120 秒的歌大约 16–24 行。
- 引号、英文整句尽量避免。

## 第 3 步：生成

```
python "<本 skill 目录>/music.py" gen --lyrics 歌词.txt --style "古风，伤感，古筝、竹笛、二胡，慢板，女声空灵" --gender female --title 长安月冷 --cover-hint "水墨，长安冷月"
```

- `--style` 是最重要的旋钮：乐器、情绪、速度、人声特点、现场/录音室氛围都写这里，中英文混写都行。
- `--gender male|female|any|duet`；纯音乐加 `--instrumental`（此时不需要歌词，`--bpm --key --time-signature --instruments` 可细调编曲）。
- `--ref <公网 URL>` 参考曲风；用户给的本地 mp3/mp4 要先抽音轨 `ffmpeg -i in.mp4 -vn ref.mp3` 并上传到能公开访问的地方，脚本不接受本地路径。
- `--prebuilt lyrics.json` 回灌第 2 步平台写的词。
- 输出到 `output/music/<title>.mp3`、`<title>_cover.jpg`、`<title>_lyrics.txt`、`<title>_request.json`（可复现）。一次只出一首；想要多个版本就多跑几次改 `--title`。
- 任务一般 1–3 分钟；返回 500"服务繁忙"时等一分钟重试。

## 第 4 步：修前奏 / 加开场独白（按需）

```
python "<本 skill 目录>/music.py" intro --song output/music/长安月冷.mp3 --keep 5                       # 只把前奏截到 5 秒
python "<本 skill 目录>/music.py" intro --song output/music/长安月冷.mp3 --text "谢谢每一个在长安月下等过的人。这首歌，送给你们。"
```

脚本用 ASR 字级时间戳找到第一个唱出的字，TTS 念独白（默认音色 `.env` 的 `TTS_VOICE`），把前奏截到"独白时长 + 1.5 秒"，独白期间伴奏压到 30%，混成新文件 `*_with_intro.mp3`。想让模型自己在歌里念独白也可以把话写进 `[intro]` 段，但吐字和时长不可控，优先用本步。

## 第 5 步：验收与报告

用 ffprobe 确认时长；报告文件路径、标题、时长、用了什么 style；如果用户的需求里有第 1 步列出的做不到的项，再次明确说明哪一项没有实现、为什么。

## 已知坑

- `mode=lyrics_to_instrumental`（按歌词结构编纯音乐）服务端稳定 500，脚本没暴露；纯音乐用 `--instrumental`。
- `--key`、`--instruments` 是自由文本，填错不报错也照样计费。
- 平台返回的 `audio_url` 有效期未知，脚本已下载到本地，不要只存 URL。
- ASR 对歌唱的说话人区分无效，别指望用它分析谁唱了哪句。
