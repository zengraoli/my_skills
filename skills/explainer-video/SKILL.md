---
name: explainer-video
description: 输入一个主题，联网调研后自动产出手绘风讲解视频 mp4（默认横屏 1920×1080，HTML 幻灯片 + SenseAudio TTS 配音 + ffmpeg 合成；支持竖屏）。当用户要"做一期/做一个 xx 的讲解视频"、"按上次那样出个视频"时使用。参数为视频主题。
---

# 讲解视频生产流水线

把一个主题变成 content.mov 风格的讲解视频。风格特征：米色背景（#fdf6ec）、居中大黑标题、手绘弯箭头、emoji 图标、元素随旁白逐个弹出、每场底部一句加粗总结。

## 流程总览

0. **环境检查** → 1. **调研** → 2. **写分场文稿** → 3. **制作幻灯片 HTML** → 4. **截图自检（必做）** → 5. **写 scenes.json** → 6. **跑 build.py** → 7. **验收成品**

本 skill 目录下的文件：`SKILL.md`（本流程）、`template.html`（参考成品 + 播放引擎）、`build.py`（合成脚本）、`.env`（key 和 TTS 配置，直接改）。环境依赖与参数详见同目录 `README.md`。

对 agent 能力的要求：能读写文件、能执行 shell 命令；**能查看图片**（第 4 步自检用，没有视觉能力时见该步的降级方案）；有联网搜索工具更好（第 1 步用，没有也能做，见该步说明）。

## 第 0 步：环境检查（开工前先跑，10 秒）

```
python "<本 skill 目录>/build.py" --check
```

它会逐项检查 Python 版本、requests、`.env`、Chrome、ffmpeg/ffprobe、（Linux 上的）中文和 emoji 字体、TTS key 鉴权，缺什么就打印对应系统的安装方法，全部通过才输出"环境正常"。

- 有任何一项"缺失"：**停下来**，把脚本给出的安装提示原样转告用户，等用户装好后重新 `--check`，再往下走。不要在缺 ffmpeg 或 key 的情况下开始调研和写幻灯片——那样做到第 6 步才发现，前面的工作只能干等。
- 缺 `.env` 或 key 为空：让用户在本 skill 目录下建 `.env` 填入 `SENSEAUDIO_API_KEY`（格式见 README）；不要替用户在聊天里索要 key，也不要把 key 写进命令行。
- 只有截图自检（第 4 步）需要的 Chrome 也在这一步一起验证，所以第 4 步不用再单独确认。

## 第 1 步：调研

用你拥有的联网搜索工具（Claude Code 的 WebSearch/WebFetch、其他 agent 提供的 MCP 搜索/抓取工具等）搜 3-5 组不同角度的查询（概念解释、真实案例/事故、数据、解决方案、行业现状），对关键来源读原文。产出 4-8 个要点，**必须包含至少一个真实案例或数字**（这是视频可信度的来源），每个要点记下来源。

- 主题是近期新事物、你的训练知识可能没覆盖时，只能以搜索结果为准，不要用记忆补数字。
- 没有任何搜索工具时：请用户提供资料；或明确告诉用户内容基于模型已有知识，并在报告里标注知识截止时间。
- 信息不足或主题模糊时先问用户。

## 第 2 步：写分场文稿

- 4-7 个场景，叙事弧线：提出问题 → 为什么会这样 → 怎么解决 → 展望/结论。
- 每场旁白 80-100 个汉字（≈15-20 秒配音）。口语化、短句、可以有设问。视频总长度由旁白总字数决定，想要更长/更短就增减场景数或每场字数。
- 英文缩写（CI、PR、API）TTS 逐字母读没问题；整句英文或命令行（如 rm -rf）会很别扭，改成中文说法。
- 引号会干扰 JSON，文稿里用逗号或"某某说"替代直接引语。

## 第 3 步：制作幻灯片 HTML

以本 skill 目录下的 `template.html` 为起点（完整可用的参考成品，含 6 个示例场景），复制到项目根目录命名为 `<主题>.html`，替换其中的 `<section class="slide">` 场景，引擎代码（style + script）不要动。

**写文件用 agent 的文件写入工具**，不要把整段 HTML 通过 shell heredoc/echo 灌进去——引号、`$`、中文和换行在 shell 里很容易被吃掉。

布局规范（坐标系固定 1180×658）：

- 每个元素 `data-step="N"` 控制出现顺序，N 从 1 开始连续编号；标题无 data-step（随场景出现）。
- **元素顶部至少从 y≈95 开始**——标题占到 y≈85（Microsoft YaHei 偏高），贴上去会撞。
- 底部总结句用 `.cap`，bottom:20-50px，关键词用 `<b>` 标红。
- 箭头写在场景末尾的 `<svg class="arrows" viewBox="0 0 1180 658">` 里，只写主体路径 `<path class="arrow" data-step="N" d="M x1 y1 Q cx cy x2 y2"/>`，箭头头部由 JS 自动生成；弯一点才像手绘。
- 可复用的部件类：`.figure`（emoji+标签）、`.term`（黑底终端）、`.block`（色块）、`.badge`（圆角标签）、`.note`（加粗注记）。配色沿用 template：蓝 #1a73e8、青 #16c9b8、红 #e5322d、黄 #f7ce46、橙框 #f5a623、暗红字 #8b1a12。
- 同一步出现多个同类元素时可加 `transition-delay:.12s/.24s` 做错落感。
- 保守布局（视觉能力弱的模型请严格遵守）：元素之间至少留 20px 间距；`.block`/`.badge` 里文字不超过两行、每行不超过 16 个汉字；一场不超过 8 个元素。

## 第 4 步：截图自检（必做，不能跳过）

对每个场景用 headless Chrome 截"全部步骤展开"的图并逐张查看（Windows 的 Chrome 一般在 `C:\Program Files\Google\Chrome\Application\chrome.exe`，Mac 在 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`，Linux 直接 `google-chrome`）：

```
<chrome> --headless=new --disable-gpu --window-size=1366,640 --hide-scrollbars --screenshot="<临时目录>/check_N.png" "file:///<项目路径>/<主题>.html?s=N&k=99&noanim=1"
```

用你的看图工具逐张查看，检查：元素重叠、文字溢出色块、箭头指向不准、撞标题。有问题改坐标后重截，直到全部干净。
注意：headless Chrome 有 512px 最小窗口宽度，别用更小的 window-size 测试；截图必须带 `&noanim=1`，否则截到动画半程的虚影。

**没有视觉能力的降级方案**：仍然截图，把图片路径列给用户请其人工过目；同时在第 3 步严格按"保守布局"规则排版，并用脚本检查每场所有元素的 left+width ≤ 1160、top ≥ 95、两两矩形不相交。

## 第 5 步：写 scenes.json

与 HTML 同目录，`<主题>.scenes.json`，UTF-8：

```json
[
  {"steps": 6, "narration": "第一场旁白……"},
  {"steps": 8, "narration": "第二场旁白……"}
]
```

`steps` = 该场景 data-step 的最大值，**必须与 HTML 一致**（数错会导致空白帧或漏帧）。

## 第 6 步：合成

在项目根目录执行：

```
python "<本 skill 目录>/build.py" --slides "<主题>.html" --scenes "<主题>.scenes.json"
```

成品落在 `<项目根目录>/output/<主题>.mp4`，中间产物在 `output/_build/<主题>/`（重跑会覆盖）。要放别处加 `--out <路径>`。脚本自动完成：TTS → 按配音实际时长均分步骤 → 2 倍分辨率截帧 → ffmpeg 合成。常用参数：

- 配置都在本 skill 目录的 `.env` 里（key、TTS 地址/模型/音色/语速，模板见 `.env.example`），脚本自动读取，正常情况下命令行不用带 TTS 参数。`.env` 缺失或没有 key 时脚本会明确报错，此时提示用户复制 `.env.example` 填 key，不要把 key 写进命令行或聊天记录。
- TTS 用 SenseAudio（`sensenova-tts-2.0`）。默认音色 `female_0038_b`（亲切女孩·温柔讲解版，用户选定）、语速 1.05；用户要男声时加 `--voice male_0029_a`（利落青年）或 `male_0004_a`（儒雅道长）。音色 id 末位 a-f 是同一音色的不同情绪版本，完整表和试听在 https://docs.senseaudio.cn/guides/voice/catalog 。SenseAudio 没有风格指令参数，想换语气就换音色版本。
- `--aspect landscape|portrait`：横屏 1920×1080（默认）或竖屏 1080×1920（画面居中、上下留米色边）。
- `--gap 0.6`：场景之间停顿秒数，默认 0。
- Chrome 按操作系统自动探测，找不到时 `--chrome <路径>`（或环境变量 `CHROME_PATH`）；ffmpeg 不在 PATH 时 `--ffmpeg-dir <目录>`（或 `FFMPEG_DIR`）。

中间产物目录可用 `--work-dir` 改。

## 第 7 步：验收

1. ffprobe 确认时长/编码正常（脚本末尾已输出）。
2. 从成品中抽 2-3 帧（开头、中段、结尾）查看，确认场景与时间轴对应。
3. 向用户报告：文件路径、时长、分场结构，以及调研中用到的关键事实来源（附 URL 和日期）。

## 已知坑

- TTS 失败时服务器返回的是 JSON 错误而非 mp3，build.py 会检测并把服务器消息打印出来，据此改文稿（常见原因：文本含未转义引号、超长）。
- `steps` 与 HTML 里 data-step 最大值不一致时不会报错，只会出现空白帧或漏帧，验收抽帧时要留意。
- 通过 shell 写含中文/引号的大段文件容易出错（heredoc 解析失败、编码错乱），一律用文件写入工具。
- Linux 上要装中文字体和彩色 emoji 字体（如 Noto Sans CJK、Noto Color Emoji），否则截图出现方块；字体不同标题高度会变，自检时留意是否撞标题。
- Windows 上若在 PowerShell 5.1 里手动调用 chrome/ffmpeg：`curl` 要写 `curl.exe`，别对原生程序用 `2>$null`。
