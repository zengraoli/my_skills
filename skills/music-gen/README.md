# music-gen：AI 歌曲生成（SenseAudio）

给 coding agent 用的 skill：一句需求 → 一首带 AI 演唱的完整歌曲（mp3 + 封面 + 歌词），或纯音乐；再用同一平台的 TTS/ASR 配合 ffmpeg 截短前奏、加一段开场致谢独白。所有 AI 环节走 SenseAudio 接口，本地只有 ffmpeg 做剪辑混音，不依赖本地模型。

```
需求 ──► 歌词（agent 写 / 平台写）──► music.py gen ──► mp3 + 封面
                                                 └──► music.py intro ──► 截前奏 / 加致谢独白
```

## 目录

| 文件 | 作用 |
|---|---|
| `SKILL.md` | 给 agent 的流程：先对齐做不到的事，再写词、生成、修前奏 |
| `music.py` | 脚本，四个子命令 `check` / `lyrics` / `gen` / `intro` |
| `.env` | key 与默认配置（模型、独白音色、ffmpeg 路径） |
| `README.md` | 本文件 |

## 依赖

Python ≥ 3.9 + `requests`；ffmpeg + ffprobe（Windows 从 https://www.gyan.dev/ffmpeg/builds/ 下载解压，把 bin 目录填进 `.env` 的 `FFMPEG_DIR`）；SenseAudio API key（https://senseaudio.cn ，填进 `.env`）。跑 `python music.py check` 逐项验证。

## 用法

```bash
python music.py check
python music.py lyrics --prompt "深秋渡口送别故人" --style "古风抒情" --rhyme an --duration 120
python music.py gen --lyrics song.txt --style "古风，伤感，古筝、竹笛、二胡，慢板，女声空灵" --gender female --title 长安月冷
python music.py gen --prompt "让 AI 替你值夜班" --style "轻快中文流行电子" --gender female --duration 90
python music.py gen --instrumental --style "Lo-fi，钢琴+轻鼓点，不抢人声" --bpm 85 --duration 150 --title bgm
python music.py gen --lyrics song.txt --style "..." --ref https://example.com/ref.mp3      # 参考曲风
python music.py intro --song output/music/长安月冷.mp3 --keep 5                            # 前奏截到 5 秒
python music.py intro --song output/music/长安月冷.mp3 --text "谢谢每一个在长安月下等过的人。"  # 加致谢独白
```

`gen` 主要参数：`--style`（曲风，最重要）、`--gender male|female|any|duet`、`--instrumental`、`--duration`、`--bpm/--key/--time-signature/--instruments`、`--ref`（公网 URL）、`--cover-hint/--cover-ratio/--no-cover`、`--format mp3|wav|wav32`、`--title`、`--out-dir`（默认 `output/music`）。
`intro` 主要参数：`--text`（不给则只截前奏）、`--voice`、`--keep`（保留前奏秒数）、`--lead`（念完到开唱的秒数）、`--duck`（独白时伴奏音量）、`--onset`（手动指定人声起始，跳过 ASR）。

价格：歌曲 0.5 元/首（10 秒到 10 分钟同价，含封面）；独白 TTS 和 ASR 测前奏合计不到 0.1 元。

## 目前能做到的

- 自带歌词或让模型写词，指定曲风、声部（男/女/任意/男女对唱）、期望时长、BPM/调式/拍号/乐器，顺带出封面（封面提示词可控）。
- 纯音乐模式，适合做视频 BGM。
- 参考音频定曲风（节奏、配器、氛围会靠近，旋律不会复制）。
- 事后精确截前奏；在开头叠一段指定音色、指定文字的致谢独白，独白期间自动压低伴奏。
- 用平台歌词接口写词并拿到它的编曲建议（BPM、调式、拍号和一段详细的英文编曲描述），可回灌生成。

## 目前做不到的，以及为什么

这些是 SenseAudio 音乐模型（`senseaudio-music-2.0`）本身的限制，2026-09-10 逐项实测过，不是脚本没实现：

| 需求 | 现状 | 原因 |
|---|---|---|
| **锁定歌手音色**，多首歌同一个嗓子 | 做不到 | 接口只有 `gender`（男/女/任意/对唱）四个选项，没有歌手音色 id、没有音色克隆用于唱歌；网页创作台同样只有这三个按钮。唯一的软办法是把上一首成品当参考音频（`--ref`）再生成，相似度不保证 |
| **男女对唱并指定谁唱哪句** | 只能"有男有女" | `gender=duet` 会出现两种声部和副歌合唱，但分配由模型决定；歌词里写 `[male]/[female]` 或"男：/女："会被当歌词**唱出来**，而且不改变声部 |
| **歌手唱一句、观众接一句 / 现场版** | 只能"氛围" | 没有 `[crowd]`、`[audience]` 之类的标签语义，写了会被唱出来；`--style` 写 live/crowd 只能得到现场混响和群唱感，接唱位置不可控 |
| **模型内的开场独白** | 不稳定 | 把话写进 `[intro]` 模型会用说话方式念，但有重音/吞字，念完还会铺十几秒前奏。所以本 skill 改用 TTS + ffmpeg 在歌外面做，可控 |
| **同一首歌翻新编曲**（Suno 的 Cover/Extend） | 没有 | 每次生成都是新歌，旋律会变；没有"保留旋律换编曲"或"续写"接口 |
| **翻唱 / 用指定嗓子唱现有歌** | 没有 | 没有歌声转换能力，人声分离接口只能拆出人声，不能换嗓 |
| **精确时长、前奏长度** | 只能事后截 | `expect_duration` 和附加提示词里的"前奏不超过 5 秒"都不被严格遵守 |

其中锁音色、可控对唱、观众接唱三项在 Suno / MiniMax 等平台上是否有对应方案，见本仓库根 README 的调研记录（或直接问 agent）。

## 写歌词的规矩

只用 `[verse]` `[pre-chorus]` `[chorus]` `[bridge]` `[outro]`（可带序号）。模型只认这些结构标签，其他方括号内容会被唱出来，脚本会在提交前警告。演唱方式的要求（对唱、现场、说唱）一律写进 `--style`。
