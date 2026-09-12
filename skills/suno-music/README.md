# suno-music：AI 歌曲生成、锁音色、给自己的伴奏配唱

给 coding agent 用的 skill。用 Suno v6（经第三方 API）生成歌曲与纯音乐，把喜欢的嗓音**锁定下来**反复使用，给**你自己的伴奏**配上词唱出来，还能翻唱改编、续写加长、人声伴奏分轨；后期用本地 ffmpeg 给歌加开场独白或截短前奏。

这是 `music-gen`（SenseAudio）的替代与超集：同样能出歌和纯音乐、同样能加开场独白，另外多出锁音色、伴奏配唱、翻唱、续写、分轨、逐句指定的男女对唱、现场观众接唱、模型内开场白、限定时长，且**每首约 ¥0.10，比 SenseAudio 的 ¥0.50 便宜五倍**，出片也快一倍。

```
需求 ──► 歌词（用户给 / agent 写 / 模型写）──► suno.py gen ──► 2 个版本 mp3
                                              ├── persona add  ──► 锁住这个嗓音，之后 --persona 复用
                                              ├── sing         ──► 你的伴奏 + 你的词 = 唱出来
                                              ├── cover/extend ──► 改编 / 加长
                                              └── intro        ──► 开场独白 / 截前奏（本地，免费）
```

## 怎么用

**在 agent 里说人话即可，不用记参数。** agent 会读 `SKILL.md` 自己走流程。

```
写一首古风伤感的歌，女声
就用刚才那首歌的声音，再唱一首新词
我有段伴奏在这个链接，帮我配上这段词唱出来
把《慢一点》翻唱成民谣吉他版
给这首歌开头加一句"送给所有愿意慢下来的人"
```

下面的命令是给**手动运行或调试**用的。

## 依赖

Python ≥ 3.9 + `requests`；ffmpeg + ffprobe；`edge-tts`（可选，开场独白用，免费无需 key）；TTAPI 的 key（https://ttapi.io 注册，新号送 30 quota，够试 10 次）。跑 `python suno.py check` 逐项验证。

## 命令

```bash
python suno.py check                                  # 环境与鉴权
python suno.py list                                   # 本地歌曲与音色库

# 出歌
python suno.py gen --lyrics 词.txt --style "古风，古筝竹笛，女声空灵" --gender female --title 长安月冷
python suno.py gen --idea "夏夜海边久别重逢" --style "温暖的中文流行"        # 让模型写词
python suno.py gen --instrumental --style "Lo-fi 钢琴，不抢人声" --title bgm  # 纯音乐
python suno.py gen --lyrics 词.txt --style "..." --duration 90                # 限定时长（唱不完的词会被截掉）
python suno.py gen --lyrics 对唱词.txt --style "male and female duet, mandarin pop ballad"   # 对唱，词里按段标 [Male Vocal]/[Female Vocal]

# 锁音色
python suno.py persona add --from 长安月冷_1 --name 温柔女声A --start 12 --end 40 --describe "空灵、气声多"
python suno.py persona list
python suno.py gen --lyrics 新词.txt --persona 温柔女声A --style "民谣"

# 给自己的伴奏配唱（伴奏必须是公开 URL）
python suno.py sing --backing https://example.com/backing.mp3 --lyrics 词.txt --gender female --title 我的歌

# 改编
python suno.py cover --from 长亭_1 --style "Chinese DJ remix, EDM, 128 BPM"        # 同一首歌换成 DJ 版
python suno.py cover --from 长亭_1 --persona 阳光男声B                           # 同词同曲换嗓子
python suno.py cover --from 长亭_1 --lyrics 新词.txt                             # 旋律不变换词
python suno.py inspo --ref 长亭_1 --lyrics 新词.txt --title 青灯                  # 参考曲风写新歌
python suno.py cover --backing https://example.com/song.mp3 --lyrics 词.txt --style "民谣吉他，男声"   # 外部歌曲，必须给词
python suno.py extend --from 长安月冷_1 --at 55 --lyrics 续写的词.txt           # 自动拼回整首
python suno.py stems --from 长安月冷_1                                         # 人声 + 去人声伴奏
python suno.py stems --from 长安月冷_1 --type piano                            # 单独抽某件乐器

# 后期（本地，不花钱）
python suno.py intro --song output/suno/长安月冷_1.mp3 --text "这首歌，送给……"
python suno.py intro --song output/suno/长安月冷_1.mp3 --keep 5
```

歌和音色都**用名字引用**，脚本在 `output/suno/library.json` 里维护名字与 id 的对应，不用记 UUID。

## 价格（实测，非文档标价）

| 操作 | quota | 约合人民币 | 说明 |
|---|---|---|---|
| 生成 / 翻唱 / 续写 / 配唱 / 参考曲风 | 3 | **¥0.20** | 一次出 **2 个版本**（Suno 本身的机制），即 ¥0.10/首 |
| 人声/伴奏分轨 | 6 | ¥0.40 | 返回人声 + 去人声伴奏，各两份 |
| 上传伴奏 | 0.25 | ¥0.017 | `sing` 内部第一步 |
| 创建 Persona（锁音色） | 0 | 免费 | 实测不扣 |
| 续写后拼回整首（concat） | 0 | 免费 | `extend` 自动做 |
| 开场独白 / 截前奏 | 0 | 免费 | 本地 ffmpeg + edge-tts |

TTAPI 100 quota = $1，新账号送 30 quota。**HTTP 200 就是已扣费，400 参数错误不扣。**

## 能力总览（全部用本 skill 的命令实跑过，2026-09-12～13）

一次付费生成返回 2 个版本，v6 模型，48 kHz 立体声、约 170 kbps，20–80 秒出结果。

| 分类 | 能力 | 怎么用 | 实测结果 |
|---|---|---|---|
| **写歌** | 自带歌词 / 一句话让模型写 / 纯音乐 | `gen --lyrics` / `--idea` / `--instrumental` | ✅ |
| | 限定时长 | `gen --duration 10～360` | ✅ 设 90 秒出 91/95 秒；**是截断**，唱不完的词直接丢掉，不会加快语速 |
| **演唱编排**（写在歌词里） | 男女对唱，按段指定谁唱 | 段落首行标 `[Male Vocal]` / `[Female Vocal]` / `[Duet]` | ✅ 4 个版本全部按标注换人 |
| | 男女对唱，**一句男一句女** | 每句单独成一块、块间空一行，每块首行标男/女 | ✅ 两个版本试听都交替正确 |
| | 现场版、歌手唱一句观众接一句 | 观众那句标 `[Crowd]` 并加括号 `(我们一起唱)`；`--style` 写 `live concert recording, crowd singing along, call and response` | ✅ 需挑版本：一版接唱清楚，另一版被人群声盖满 |
| | 模型自己念开场白 | 歌词开头 `[Spoken Word]` + 独白，紧接 `[Verse]`；`--style` 写 `spoken word intro, no instrumental intro, vocals start right after the spoken words` | ✅ 4 个版本一字不差，念完约 5 秒开唱（不这么写要空 12–26 秒） |
| **嗓音** | 锁定一个嗓音反复用 | `persona add` → 之后 `--persona 音色名` | ✅ 创建免费 |
| | A 的词 + B 的嗓音 | 从 B 建音色 → `gen --lyrics A词 --persona B音色` | ✅ 旋律会重写 |
| **翻唱改编** | 同一首歌换曲风（古风→DJ、流行→感伤） | `cover --from 歌名 --style ...` | ✅ 旋律歌词保留 |
| | 同词同曲换嗓子 | `cover --from 歌名 --persona 音色名` | ✅ 接口文档没写但生效：女声翻唱后基频 225→174 Hz，与目标男声一致 |
| | 旋律不变换一套词 | `cover --from 歌名 --lyrics 新词` | ✅ |
| | 参考某首歌的曲风写新歌 | `inspo --ref 歌名或URL --lyrics 新词` | ✅ 新旋律，1～4 首参考 |
| | 翻唱外部歌曲 | `cover --backing <公开URL> --lyrics 词` | ✅ 女声 Lo-fi 原创歌 → 男声民谣；**必须给词**，商业曲库歌曲被拒（见下表） |
| **伴奏 / 结构** | 给自己的伴奏配唱 | `sing --backing <公开URL> --lyrics 词` | ✅ 120 秒伴奏出 119 秒成品 |
| | 续写加长 | `extend --from 歌名 --at 秒 [--lyrics 词]` | ✅ 125 秒的歌在 80 秒处接上新写的 bridge，自动拼回整首（134 秒） |
| | 人声 / 伴奏分轨，或抽单件乐器 | `stems --from 歌名 [--type piano]` | ✅ 人声 + 去人声伴奏各两份 |
| **后期（本地、免费）** | 开场独白 / 截前奏 | `intro --song 文件 --text ... / --keep 5` | ✅ 有长前奏就压在前奏上（伴奏自动压低），没有就接在歌前面 |

开场独白两条路：要独白和歌同一个嗓子、一气呵成，写 `[Spoken Word]`；要指定独白音色、精确控制间隔、或给已有的歌补一句，用 `intro`。
不知道一首歌的歌词时，可用同仓库的 `asr-tts` skill（`speech.py lyrics`）提取带时间轴的歌词。

接口还提供、本 skill 暂未接入的：替换某一段（replace-section）、两首混搭（mashup）、重制音质（remaster）、给人声配伴奏（add-instrumental）、剪切/淡入淡出/变速、生成 MIDI 与 BPM、导出 WAV、生成歌词视频。

## 目前做不到的，以及为什么

| 需求 | 现状 | 原因 |
|---|---|---|
| **按秒卡时长又唱完全部歌词** | 二选一 | `--duration` 是截断，不会压缩；要完整唱完就按歌词长度来，要卡时长就把词写短 |
| **复刻真实歌手的嗓音** | 不行 | Persona 只能从 Suno 生成的歌里提取；复刻真人需本人录制 Voices，且受平台限制 |
| **直接用本地音频文件** | 需公开 URL | 上传接口只收 `audio_url`，本地文件要先传到对象存储 / 静态站 / GitHub release |
| **在 Suno 自己生成的歌上加人声** | 被拒 | 接口限制：add-vocals 只接受用户上传的音频（报错原文如此） |
| **翻唱 / 改编知名歌手的商业歌曲** | 被拒 | Suno 对上传和参考音频做曲库比对，实测《弱水三千》《星语心愿》在上传、翻唱、风格参考三条路上全部报 "matches an existing recording in our catalog"（不扣费）。可行的替代是提取歌词后按描述的曲风重新生成，旋律会不同 |

## 供应商

默认 **TTAPI**（`.env` 的 `SUNO_PROVIDER=ttapi`）：v6 全系列、31 个端点、有 OpenAPI 文档、¥0.10/首。

备选 **t8star**（`SUNO_PROVIDER=t8star`）：只实现了基础 `gen`，最高 chirp-v5（请求 v5 实际返回内部代号 `chirp-hawk`），¥0.68/次，出片 75–85 秒，其余端点无公开文档。

两家都是**非官方中转**（Suno 至今没有公开 API），随时可能变动，别一次充太多；产物脚本会立即下载到本地。

## 常见问题

- **报错 "can only be used with music you uploaded yourself"**：你在 Suno 生成的歌上用了加人声，改用 `sing`（它会先上传）。
- **歌太短**：歌词写长一点，或 `extend --from 歌名 --at <秒>` 接续。
- **音色没锁住**：`persona add` 时的 `--start/--end` 要选人声清楚、伴奏不盖的一段，`--describe` 写具体；Persona 捕捉的是"音色与气质"，同一音色多首歌之间仍会有细微差异。
- **找不到歌名**：`python suno.py list` 看本地库，或直接传 musicId。
