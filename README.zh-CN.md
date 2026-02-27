# CyberSecurityRSS

[![Stars](https://img.shields.io/github/stars/zer0yu/CyberSecurityRSS?style=flat-square)](https://github.com/zer0yu/CyberSecurityRSS/stargazers)
[![Forks](https://img.shields.io/github/forks/zer0yu/CyberSecurityRSS?style=flat-square)](https://github.com/zer0yu/CyberSecurityRSS/network/members)
[![Last Commit](https://img.shields.io/github/last-commit/zer0yu/CyberSecurityRSS?style=flat-square)](https://github.com/zer0yu/CyberSecurityRSS/commits/master)
[![OPML Sync](https://img.shields.io/github/actions/workflow/status/zer0yu/CyberSecurityRSS/opml-sync.yml?branch=master&style=flat-square&label=opml-sync)](https://github.com/zer0yu/CyberSecurityRSS/actions/workflows/opml-sync.yml)

一个面向实战的网络安全 RSS/Atom OPML 订阅集合，帮助你搭建高信噪比的每日情报输入流。

[English](README.md)

## 项目亮点

- 双版本订阅集合，覆盖不同阅读精力：
  - `tiny.opml`：适合日常速览（当前约 422 条订阅 / 9 个分类）
  - `CyberSecurityRSS.opml`：适合全量追踪（当前约 735 条订阅 / 12 个分类）
- 覆盖漏洞研究、攻防对抗、逆向分析、Web 安全、情报资讯等核心方向。
- 自动化维护：失效链接清理、按 `xmlUrl` 去重、`tiny` 自动同步到 `full`。
- 既适合手工阅读，也适合作为 AI 自动化日报输入源。

## 快速开始

1. 选择订阅文件：
   - 低噪音日常阅读：`tiny.opml`
   - 全量覆盖：`CyberSecurityRSS.opml`
2. 导入到你常用的 RSS 阅读器。
3. 开始构建个人安全知识库与情报流。

### 常见阅读器

- Reeder（macOS / iOS）
- Feedly（Web / iOS / Android）
- [yarr](https://github.com/nkanaev/yarr)（自托管，跨平台）
- Feeder / Zr / 蚁阅 / Leaf

## 文件说明

- `tiny.opml`：精选精简版，适合每天快速浏览。
- `CyberSecurityRSS.opml`：丰富完整版，主题覆盖更广。
- `others.md`：手工整理的优质站点（含部分未提供 RSS 的网站）。

## AI 每日精选日报（OpenClaw / Claude Code）

你可以将本仓库与 [zer0yu/sec-daily-digest](https://github.com/zer0yu/sec-daily-digest) 中的 Skills 配合使用，让 OpenClaw 或 Claude Code 自动生成**每日精选安全日报**。

推荐工作流：

1. 按 `sec-daily-digest` 仓库说明安装对应 Skills。
2. 默认使用 `tiny.opml` 作为输入源（需要更广覆盖时切换 `CyberSecurityRSS.opml`）。
3. 设置每天定时执行（例如每天早上一次），抓取最近 24 小时更新。
4. 输出结构化日报，例如：
   - 今日重点
   - 漏洞与利用
   - 威胁情报与事件
   - 研究与工具
   - 可落地行动建议

示例提示词：

```text
使用 CyberSecurityRSS 订阅源生成最近 24 小时的网络安全精选日报。
仅保留高价值内容，按主题分组，并给出标题、价值说明和原文链接。
```

## 仓库自动化工作流

仓库已配置 GitHub Actions 自动维护 OPML：

1. `pull_request -> master`：触发 `check` 模式，只校验不改文件；若发现漂移直接失败。
2. `push -> master`：触发 `apply` 模式并自动执行：
   - 校验 `tiny.opml` 与 `CyberSecurityRSS.opml` 中 RSS/Atom 链接可访问性。
   - 删除失效订阅，并按 `xmlUrl` 自动去重。
   - 将 `tiny.opml` 中有效且大表缺失的订阅同步到 `CyberSecurityRSS.opml`。
   - 缺失分类自动归入 `Misc`。
   - 维护 `.github/opml-health-state.json`，仅在连续硬失败后才删除订阅，降低误删。
   - 变更自动提交（commit 含 `[skip ci]`）。

## 本地校验

```bash
python3 -m unittest discover -s tests -v

python3 scripts/opml_sync.py \
  --mode check \
  --tiny tiny.opml \
  --full CyberSecurityRSS.opml \
  --fallback-category Misc \
  --timeout 10 \
  --retries 3
```

## OPML/XML 兼容说明

部分阅读器仅接受 `.xml` 或 `.opml` 后缀，可直接改名使用：

```bash
cp CyberSecurityRSS.opml CyberSecurityRSS.xml
cp tiny.opml tiny.xml
```

## 贡献

欢迎提交 Issue / PR：

- 补充高质量订阅源
- 修复失效或重复链接
- 说明推荐分类与推荐理由
- 提交前先执行本地校验命令

## Sponsor

[![Powered by DartNode](https://dartnode.com/branding/DN-Open-Source-sm.png)](https://dartnode.com "Powered by DartNode - Free VPS for Open Source")
