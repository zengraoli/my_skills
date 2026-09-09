# explainer-video：一句话主题 → 手绘风讲解视频

给 coding agent 用的 skill。输入一个主题，agent 联网调研、写分场文稿、生成 HTML 幻灯片、自检画面，然后由 `build.py` 配音并合成 mp4。风格：米色背景、大黑标题、手绘弯箭头、emoji 图标、元素随旁白逐个弹出、每场底部一句加粗总结。

```
主题 ──► 调研 ──► 分场文稿 ──► 幻灯片 HTML ──► 截图自检 ──► scenes.json ──► build.py ──► mp4
        (agent)   (agent)      (agent)        (agent 看图)    (agent)      (脚本)
```

## 目录

| 文件 | 作用 |
|---|---|
| `SKILL.md` | 给 agent 看的流程规范（7 步 + 布局规则 + 坑） |
| `template.html` | 参考成品：6 个示例场景 + 播放引擎（data-step 步进、箭头自动画头、URL 定位） |
| `build.py` | 合成脚本：TTS → 截帧 → ffmpeg。不含任何 AI 成分，可单独使用 |
| `.env` | key 与 TTS 配置，直接改这个文件（格式见下面"配置"） |
| `README.md` | 本文件 |

## 依赖

**机器上要有：**

| 依赖 | 用途 | 安装 |
|---|---|---|
| Python ≥ 3.9 | 跑 build.py | python.org / anaconda |
| `requests` | 调 TTS 接口 | `pip install requests` |
| Chrome / Chromium / Edge | headless 截帧 | 任一即可，脚本自动探测 |
| ffmpeg + ffprobe | 合成视频、测配音时长 | Windows 免安装，下载二进制解压即可，见下面"Windows 安装 ffmpeg"；Mac `brew install ffmpeg`；Linux `apt install ffmpeg` |
| 中文字体 + 彩色 emoji 字体 | 截图渲染 | Windows/Mac 自带；Linux 装 `fonts-noto-cjk fonts-noto-color-emoji` |
| SenseAudio API key | 配音 | https://senseaudio.cn 申请，填进 `.env` 的 `SENSEAUDIO_API_KEY`（仓库里自带的 key 可直接试用） |

**agent 要有的能力：** 读写文件、执行 shell、**查看图片**（第 4 步自检；没有的话见 SKILL.md 里的降级方案）、联网搜索（第 1 步；MCP 搜索工具也行，没有则由用户提供资料）。

### Windows 安装 ffmpeg（免安装，下载即用）

ffmpeg 在 Windows 上不需要安装程序，直接下载编译好的二进制：

1. 打开 https://www.gyan.dev/ffmpeg/builds/ ，下载 **`ffmpeg-release-essentials.zip`**（essentials 版够用，约 100 MB）。也可以用命令 `winget install Gyan.FFmpeg`，效果相同。
2. 解压到任意目录，例如 `C:\soft\ffmpeg\`。解压后的 `bin\` 子目录里有 `ffmpeg.exe` 和 `ffprobe.exe`，两个都要用。
3. 告诉脚本它在哪，二选一：
   - **推荐**：在 `.env` 里填 `FFMPEG_DIR=C:\soft\ffmpeg\bin`（填 `bin` 目录；填成 `ffmpeg.exe` 的完整路径也行，脚本会自动取它所在目录）。
   - 或者把 `C:\soft\ffmpeg\bin` 加进系统 PATH（设置 → 系统 → 高级系统设置 → 环境变量），然后重开终端。
4. 跑 `python build.py --check`，看到 ffmpeg 和 ffprobe 两行都是 `[ OK ]` 即可。

Chrome 同理：装在非默认位置时在 `.env` 里填 `CHROME_PATH=...\chrome.exe`。

## 快速开始

```bash
# 0. 环境检查：逐项验证 Python / requests / .env / Chrome / ffmpeg / 字体 / TTS 鉴权，缺什么给出安装方法
python build.py --check

# 1. 配置：直接改 build.py 同目录的 .env（key、音色、语速、工具路径都在里面）
#    优先级：命令行参数 > 系统环境变量 > .env > 内置默认值

# 2. 在 agent 里触发 skill（Claude Code）
/explainer-video 讲一期 /loop 命令

# 3. 或者绕过 agent，直接合成已有的幻灯片：成品落在 ./output/demo.mp4
python build.py --slides demo.html --scenes demo.scenes.json
```

### 配置（.env）

```ini
SENSEAUDIO_API_KEY=sk-xxxx                       # 必填
TTS_BASE_URL=https://api.senseaudio.cn/v1
TTS_MODEL=sensenova-tts-2.0
TTS_VOICE=female_0038_b                          # 音色，见下文"TTS"
TTS_SPEED=1.05                                   # 语速 0.5~2.0
FFMPEG_DIR=C:\soft\ffmpeg\bin                    # ffmpeg 不在 PATH 时填 bin 目录；在 PATH 里可留空
CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe   # 默认位置可留空自动探测
```

值为空的行会被忽略（退回自动探测 / PATH / 内置默认值），路径含空格不用加引号。

### 在其他 agent 里使用

- **Kimi Code CLI**：`kimi --skills-dir .claude/skills`，或把本目录放到 kimi 自动发现的 skills 目录（见 https://moonshotai.github.io/kimi-code/）。
- **其他支持 Agent Skills 标准（`SKILL.md` + frontmatter）的工具**：把本目录整个拷到它的 skills 目录。
- **不支持 skill 的工具**：把 `SKILL.md` 正文贴进系统提示词/项目说明，`template.html` 和 `build.py` 放到项目里即可。
- 没有内置联网、只有 MCP 搜索工具的 agent：第 1 步用 MCP 搜索代替 WebSearch，其余步骤完全不依赖联网。合成 mp4 本身不需要网络（TTS 调接口除外）。

## build.py 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--slides` | 必填 | 幻灯片 HTML |
| `--scenes` | 必填 | scenes.json |
| `--out` | `./output/<幻灯片文件名>.mp4` | 输出 mp4 路径 |
| `--out-dir` | `output` | 不指定 `--out` 时的输出目录（相对当前目录） |
| `--check` | — | 只做环境检查不合成；有缺失时退出码 1 并打印安装提示 |
| `--env` | 脚本同目录 `.env` | 配置文件路径 |
| `--voice` | `.env` 的 `TTS_VOICE`（`female_0038_b`） | 临时换音色 |
| `--speed` | `.env` 的 `TTS_SPEED`（`1.05`） | 语速倍率 0.5~2.0 |
| `--model` / `--base-url` / `--api-key` | `.env` 对应项 | 临时覆盖模型、接口地址、key |
| `--aspect` | `landscape` | `landscape` 1920×1080；`portrait` 1080×1920（画面居中、上下留米色边） |
| `--gap` | `0` | 场景之间停顿秒数 |
| `--chrome` / `--ffmpeg-dir` | 自动探测 | 也可用环境变量 `CHROME_PATH` / `FFMPEG_DIR` |
| `--window-size` | `2360,1316` | 截帧窗口（2 倍分辨率） |
| `--work-dir` | `<输出目录>/_build/<幻灯片文件名>/` | 中间产物目录（配音 mp3、每步截图、concat 列表） |

### TTS：SenseAudio

- 接口：`https://api.senseaudio.cn/v1/t2a_v2`，模型 `sensenova-tts-2.0`，输出 32kHz / 128kbps mp3，一段 60 字中文约 2 秒返回。
- 价格：3.5 元/万字符，汉字算 2 字符（一期 700 字的视频约 0.5 元）。
- 音色：官方 70+ 系统音色，id 末位 a–f 是同一音色的不同情绪版本。完整列表和在线试听：https://docs.senseaudio.cn/guides/voice/catalog 。讲解旁白推荐：

| 音色 | 说明 |
|---|---|
| `female_0038_b` | 亲切女孩·温柔讲解版（**默认**） |
| `female_0038_a` | 亲切女孩·平稳通用版 |
| `female_0036_a` | 青春女声·内容剖析版 |
| `male_0029_a` | 利落青年·内容剖析版 |
| `male_0004_a` | 儒雅道长，温润叙事感 |

- 没有风格指令参数，想换语气就换音色的情绪版本（如 `_b` 温柔讲解、`_e` 清晰播报）。
- 音色克隆 / 文生音色只能在 SenseAudio 平台上做，拿到 voice_id 后传 `--voice` 即可。

要接入其他商家：在 `build.py` 里照 `tts_senseaudio(args, text, out_path)` 写一个新函数（十几行，输入文本、输出 mp3），并在 `.env` 里加对应的 key/地址项。

## scenes.json

```json
[
  {"steps": 6, "narration": "第一场旁白……"},
  {"steps": 5, "narration": "第二场旁白……"}
]
```

`steps` 是该场景 HTML 里 `data-step` 的最大值；脚本会截 k=0..steps 共 steps+1 帧，把该场配音时长均分给这些帧。**视频总长度 = 各场配音时长之和 + 停顿**，想控制长度就控制旁白字数（80-100 汉字 ≈ 15-20 秒）。

## 幻灯片 HTML 约定

坐标系固定 1180×658，外层按窗口等比缩放。URL 参数：`?s=场景号&k=步骤号` 定位、`&noanim=1` 禁动画（截图必加）、`&nohud=1` 隐藏页码。按键：→/空格 下一步，← 上一步，A 自动播放，H 隐藏页码。布局规则见 `SKILL.md` 第 3 步。

## 常见问题

- **TTS 报错**：脚本会打印服务器返回的 JSON（`base_resp.status_msg`），通常是 voice_id 不存在、key 无效或文本含未转义引号；SenseAudio 单次上限 10000 字符，正常文稿不会超。
- **画面出现方块/emoji 变黑白**：缺字体，Linux 装 Noto CJK + Noto Color Emoji。
- **找不到 Chrome / ffmpeg**：先跑 `python build.py --check` 看缺哪项和安装方法；装在非默认位置时在 `.env` 里设 `CHROME_PATH` / `FFMPEG_DIR`（或命令行 `--chrome` / `--ffmpeg-dir`）。
- **agent 写 HTML 时 shell 报 heredoc 错误**：不要通过 shell 灌大段文本，用 agent 的文件写入工具。
- **想换机器/换 agent**：整个目录拷走即可，脚本跨 Windows/Mac/Linux；唯一硬要求是 agent 能看图做自检。
