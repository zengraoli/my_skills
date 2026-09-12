---
name: suno-music
description: 用 Suno（v6）生成歌曲与纯音乐、锁定喜欢的嗓音反复使用、给用户自己的伴奏配唱、翻唱改编、续写加长、人声伴奏分轨，并能在本地给歌加开场独白或截短前奏。当用户要"写一首歌"、"用上次那个声音再唱一首"、"给这段伴奏配上词"、"把这首歌翻唱成别的风格"、"男女对唱"、"做个现场版"、"给视频配段背景音乐"时使用。
---

# Suno 音乐生成

一首歌 3 quota（约 ¥0.20）出 2 个版本，v6，38～65 秒出片。本 skill 目录：`SKILL.md`（本流程）、`suno.py`（脚本）、`.env`（key 与默认配置）、`README.md`（人看的说明与能力边界）。

## 第 0 步：环境检查

```
python "<本 skill 目录>/suno.py" check
```

缺 key、缺 ffmpeg 会逐项提示。缺 `.env` 时让用户在本目录建一个填 `TTAPI_KEY`，不要在聊天里索要 key。

## 第 1 步：认需求，先说清做不到的

先判断用户要哪一类，再动手。下面几条是平台限制，**做不到就直说，别用提示词硬试**：

- **时长**：`--duration` 能卡秒数，但它是**截断**——唱不完的词直接丢掉。要唱完全部歌词就别设，长度由歌词决定（一段主歌+副歌约 45–60 秒），或出完再 `extend`。
- **复刻真人歌手的嗓音**：`persona` 只能从 Suno 生成的歌里提取音色，不能复刻现实中的歌手。
- **本地音频文件不能直接用**：`sing`/`cover` 的伴奏必须是**公开可访问的 URL**，用户给本地文件时要让他先传到对象存储、静态站、GitHub release 等地方。
- **知名歌手的商业歌曲一律被拒**：上传、翻唱、当风格参考（inspo）都会报 "This audio matches an existing recording in our catalog"（不扣费）。能用的素材是 Suno 生成的歌、用户自己的原创或伴奏。**不要用变调、变速、加噪等手段绕过检测**；用户想"翻唱某首流行歌"时，改为提取歌词（asr-tts 的 `lyrics`）后用 `gen` 按描述的曲风重新演绎，并说明旋律会不同。

## 第 2 步：准备歌词

用户给词就用用户的；没给就由你（agent）写，或用 `--idea "一句话主题"` 让模型自己写。

- 结构标签：`[Verse]` `[Pre-Chorus]` `[Chorus]` `[Bridge]` `[Outro]`。
- 演唱标签（放在段落第一行，不会被唱出来，2026-09-12 实测）：
  - 对唱：`[Male Vocal]` / `[Female Vocal]` / `[Duet]`；`--style` 写 `male and female duet, call and response`，不传 `--gender`。按段分配就标在段首；要一句男一句女，就让**每句单独成一块、块之间空一行**，每块第一行标男/女。
  - 现场接唱：观众那句前标 `[Crowd]`，词用括号包起来如 `(我们一起唱)`；开头 `[Crowd Cheering]`、结尾 `[Applause]`；`--style` 写 `live concert recording, crowd singing along, call and response`。一次两版里常只有一版接唱清楚，让用户挑。
  - 开场独白：第一行 `[Spoken Word]`，下一行写独白，**紧接** `[Verse]`（不要加 `[Intro]`）；`--style` 写 `spoken word intro, no instrumental intro, vocals start right after the spoken words`，念完约 5 秒开唱。
- 每段 4–6 行、每行 6–12 字最稳；副歌重复两遍。
- 45–60 秒的歌约 12–20 行；要三分钟就写满三段主歌 + 两遍副歌 + bridge。
- 中文歌词避免整句英文和命令行文本。

## 第 3 步：按需求执行（每条都花钱，执行前告诉用户预计费用）

| 用户想要 | 命令 | 费用 |
|---|---|---|
| 写一首歌 | `gen --lyrics 词.txt --style "曲风" --gender female --title 歌名` | 3 quota ≈ ¥0.20，出 2 版 |
| 一句话出歌 | `gen --idea "夏夜海边久别重逢" --style "中文流行"` | 同上 |
| 纯音乐 / BGM | `gen --instrumental --style "Lo-fi 钢琴" --title bgm` | 同上 |
| 卡时长（BGM、短视频） | 在上面任一条加 `--duration 60` | 同上 |
| **男女对唱** | 歌词按段标 `[Male Vocal]`/`[Female Vocal]`，`gen --lyrics 词.txt --style "male and female duet, ..."` | 3 quota |
| **现场版 / 观众接唱** | 歌词标 `[Crowd]` + 括号，`gen --lyrics 词.txt --style "live concert recording, crowd singing along, call and response, ..."` | 3 quota |
| **模型自己念开场白** | 歌词开头 `[Spoken Word]` + 独白，见第 2 步 | 3 quota |
| **记住这个声音** | `persona add --from 歌名 --name 音色名 --start 12 --end 40` | **0.25 quota，几乎免费** |
| **用那个声音唱新歌** | `gen --lyrics 新词.txt --persona 音色名` | 3 quota |
| **给我的伴奏配唱** | `sing --backing <公开URL> --lyrics 词.txt --gender female` | 上传 0.25 + 配唱 3 |
| 翻唱本地库的歌 | `cover --from 歌名 --style "新曲风"` | 3 quota |
| **同一首歌换曲风**（流行→感伤、古风→DJ） | `cover --from 歌名 --style "Chinese DJ remix, EDM, 128 BPM"` | 3 quota |
| **同词同曲换成某个音色唱** | `cover --from 歌名 --persona 音色名` | 3 quota |
| **旋律不变换一套词** | `cover --from 歌名 --lyrics 新词.txt` | 3 quota |
| **参考某首歌的曲风写新歌** | `inspo --ref 歌名 --lyrics 新词.txt`（1～4 首参考，不保留原旋律） | 3 quota |
| **A 的词 + B 的嗓音** | `persona add --from B歌名 --name 音色名` → `gen --lyrics A词.txt --persona 音色名` | 0.25 + 3 |
| 翻唱外部歌曲 | `cover --backing <公开URL> --style "新曲风" --lyrics 词.txt`（**必须给词**，不知道原词先用 asr-tts 的 `lyrics` 提取） | 3 quota |
| 续写加长 | `extend --from 歌名 --at 55 [--lyrics 续写的词]`（不给 `--at` 就从结尾前 5 秒接；脚本自动拼回整首） | 3 quota |
| 人声/伴奏分轨 | `stems --from 歌名`；单独抽乐器加 `--type piano` 等 | 6 quota ≈ ¥0.40 |
| 看有哪些歌和音色 | `list` | 免费 |

要点：

- **歌和音色都用名字引用**，脚本维护 `output/suno/library.json`（歌名 → musicId、音色名 → persona_id），不用记 id。生成时用 `--title` 起个好记的名字。
- 一次生成返回 **2 个版本**，落盘为 `歌名_1.mp3`、`歌名_2.mp3`，让用户挑。
- **锁音色的正确姿势**：先正常出一首 → 用户说"就这个声音" → `persona add` 从那首歌提取（`--start/--end` 选一段人声干净、没有伴奏盖过的区间，10–30 秒为宜，`--describe` 写具体些）→ 之后都带 `--persona`。
- 曲风、情绪、乐器、人声特点全写进 `--style`，中英文混写都行。

## 第 4 步：后期（本地处理，不花钱）

```
python "<本 skill 目录>/suno.py" intro --song output/suno/歌名_1.mp3 --text "这首歌，送给……"
python "<本 skill 目录>/suno.py" intro --song output/suno/歌名_1.mp3 --keep 5
```

脚本自动判断：歌有长前奏就把独白压在前奏上（伴奏自动压低），没有长前奏就把独白接在歌前面。独白默认用 edge-tts（免费、不用 key），`--voice` 换音色（男声 `zh-CN-YunxiNeural`）。

开场独白两条路怎么选：想要独白和歌是一个嗓子、一气呵成，用第 2 步的 `[Spoken Word]`（模型内，花一次生成钱）；要指定独白音色、精确控制念完到开唱的间隔、或者给已有的歌补一句，用这里的 `intro`（本地、免费、可重复调）。

## 第 5 步：验收与报告

报告文件路径、时长、用的曲风与音色、本次花费；如果用户的需求里有第 1 步列出的做不到的项，再次说明哪项没实现、为什么。

## 已知坑

- **HTTP 200 就是已扣费**，参数错误返回 400 才不扣。探索新参数前先想清楚。
- `add-vocals` 只能用在用户上传的音频上，Suno 自己生成的歌会被拒（报错原文写明）；所以"给伴奏配唱"必须走 `sing`（内部先 upload）。
- `persona_id` 只在自定义模式生效，脚本已自动处理。
- 默认 provider 是 ttapi；`.env` 里切成 `t8star` 只能用 `gen`（最高 v5，其余端点无公开文档）。
- 产物 URL 有有效期，脚本已立即下载到本地，不要只存链接。
