# asr-tts：语音转文字、歌词提取、文字转语音

给 coding agent 用的 skill。基于商汤 SenseAudio 的语音识别，做录音转写、视频字幕、会议说话人区分、边转边译，以及**从歌曲里提取带时间轴的歌词（.lrc）**；文字转语音支持商汤音色和免费的 edge-tts。

## 怎么用

在 agent 里直接说：

```
把这段会议录音转成文字，分清谁说的
给这个视频出中文字幕
提取这首歌的歌词
把这段稿子用男声念出来
```

下面的命令是给手动运行或调试用的。

## 依赖

Python ≥ 3.9 + `requests`、`numpy`；ffmpeg + ffprobe；商汤 API key（填 `.env`）；`edge-tts`（可选，免费 TTS）。`python speech.py check` 逐项检查。

## 命令

```bash
python speech.py check
python speech.py asr --file 会议.mp3                                   # → 会议.txt
python speech.py asr --file 视频.mp4 --format srt                       # → 视频.srt
python speech.py asr --file 会议.mp3 --model pro --speakers 4 --format srt
python speech.py asr --file 英文.mp3 --model pro --translate zh --format srt
python speech.py lyrics --file 歌.mp3 --out cover_lab/lyrics/歌名        # → 歌名.txt / .lrc / .asr.json
python speech.py tts --text "这首歌，送给你" --out 独白.mp3
python speech.py tts --file 稿子.txt --out 配音.mp3 --provider edge --voice zh-CN-YunxiNeural
python speech.py voices --provider edge
```

## 模型与价格

| 模型 | 价格 | 时间戳 | 标点 | 说话人 | 翻译 | 适合 |
|---|---|---|---|---|---|---|
| lite | ¥0.9/小时 | ✗ | ✗ | ✗ | ✗ | 只要文字、最省钱 |
| **standard（默认）** | ¥1.8/小时 | ✓ | ✓ | ✓ | ✗ | 转写、字幕、**歌词** |
| pro | ¥3.6/小时 | ✓ | ✓ | ✓（可设人数） | ✓ | 会议、翻译字幕 |
| deepthink | ¥3.6/小时 | ✗ | — | ✗ | ✓ | 整理讲话稿（会改写原话，**不能提歌词**） |

TTS：商汤 ¥3.5/万字符（汉字算 2 个字符），edge-tts 免费。

## 能做到的（2026-09-12 实测）

- 歌曲歌词提取：《弱水三千》4 分 23 秒、《星语心愿》3 分 22 秒，文字基本正确，自动断成乐句并带时间轴；单首约 ¥0.1。
- 任意长度音频：超 10 MB 自动压缩，超 170 秒自动在安静处切段识别再拼回。
- 字幕、说话人区分、翻译、长文本 TTS 自动分段拼接。

## 做不到或要注意的

| 情况 | 说明 |
|---|---|
| 歌词里的同音错字 | 伴奏干扰识别，第二遍副歌尤其容易错；请对照原曲校对，或先分离人声 |
| 服务端 180 秒窗口丢时间戳 | 商汤服务端的问题：长音频有时整段文字对、时间戳全无。脚本已通过客户端切段规避，并对挤在一起的时间做了兜底重排 |
| 歌唱的说话人区分 | 基本无效，对唱分不出谁唱哪句 |
| 没有 key 时 | ASR 没有免费替代；TTS 可切 `--provider edge` 继续用 |
