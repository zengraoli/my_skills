# my_skills

给 coding agent 用的 skill 集合。每个 skill 一个目录，目录里有给 agent 看的 `SKILL.md`（流程规范）和给人看的 `README.md`（依赖、参数、用法），遵循 Agent Skills 约定，Claude Code、Codex、Kimi Code、grok-cli、zcode 等都能加载。

仓库根就是项目里的 `.agents/` 目录：把它 clone 到你的项目下，支持 `.agents/skills/` 的工具会自动发现。

```
.agents/
├── README.md                 # 本文件
└── skills/
    ├── explainer-video/      # 每个 skill 一个目录
    │   ├── SKILL.md          # agent 执行流程
    │   ├── README.md         # 人看的说明：依赖、安装、参数、能力边界
    │   ├── build.py          # 脚本
    │   ├── template.html
    │   └── .env              # 该 skill 的配置与 key
    └── music-gen/
        ├── SKILL.md
        ├── README.md
        ├── music.py
        └── .env
```

## 收录的 skills

| skill | 一句话 | 说明 |
|---|---|---|
| [explainer-video](skills/explainer-video/) | 输入一个主题，联网调研后自动产出手绘风讲解视频 mp4 | HTML 幻灯片 + SenseAudio TTS 配音 + ffmpeg 合成，横/竖屏；依赖 Python、Chrome、ffmpeg，详见其 README |
| [music-gen](skills/music-gen/) | 一句需求生成一首带 AI 演唱的完整歌曲或纯音乐，并能截前奏、加开场致谢独白 | SenseAudio 音乐接口，0.5 元/首含封面；锁音色、可控对唱、观众接唱做不到，原因见其 README |

## 安装到你的项目

```bash
cd <你的项目>
git clone https://gitee.com/zengraoli/my_skills.git .agents
```

然后按 agent 接上：

| agent | 做法 |
|---|---|
| Codex | 自动读取项目里的 `.agents/skills/`，无需配置 |
| Kimi Code CLI | `kimi --skills-dir .agents/skills`，或放进它的默认 skills 目录 |
| Claude Code | 它读 `.claude/skills/`，在项目根目录做一个链接指过去：Windows `mkdir .claude\skills` 后 `mklink /J .claude\skills\explainer-video .agents\skills\explainer-video`；Mac/Linux `mkdir -p .claude/skills && ln -s ../../.agents/skills/explainer-video .claude/skills/` |
| grok-cli / zcode 等 | 支持 skills 目录的直接指向 `.agents/skills`；不支持的把 `SKILL.md` 正文贴进系统提示词或项目说明 |

每个 skill 的环境依赖和自检命令在它自己的 README 里（explainer-video 先跑 `python .agents/skills/explainer-video/build.py --check`，music-gen 先跑 `python .agents/skills/music-gen/music.py check`）。

## 约定

- 每个 skill 自带 `README.md`（依赖、参数、用法）和 `SKILL.md`（给 agent 的流程）；配置和 key 放在该 skill 目录的 `.env`，脚本自己读，不依赖系统环境变量。
- 本仓库里的 `.env` 及 key 是作者自愿公开的，clone 后可直接试用；用量大请换成自己的。
- 版本：提交信息固定为 `vX.Y 简短备注`，并打同名 tag。
