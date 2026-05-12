# 内容搜索聚合插件

通过 AstrBot 搜索各平台内容，自动去重聚合返回结果。

> ⚠️ **当前状态**：暂时适配抖音。其他平台开发中。

## 功能

| 功能 | 状态 |
|------|------|
| 🎵 抖音搜索 | ✅ 可用（手机版 Playwright，无需 Cookie） |
| 🅱️ B站搜索 | ✅ 可用（桌面版 Playwright，无需 Cookie） |
| 💬 微博搜索 | 🟡 需要 Cookie 配置 |
| 📕 小红书搜索 | 🚧 开发中（IP 被封） |
| 💬 知乎搜索 | 📅 计划中 |
| 🔄 SimHash 自动去重 | ✅ 已实现 |

## 安装

插件市场搜索 `content_search` 安装，或：

```bash
cd AstrBot/data/plugins
git clone https://github.com/WillyerQ/content_search.git
```

## 配置

在 WebUI → 插件配置中填写：

| 配置项 | 说明 |
|--------|------|
| max_results | 每个平台最大返回条数（默认 10） |
| similarity_threshold | 相似度阈值 0-100，高于此值视为重复（默认 85） |

抖音、B站不需要 Cookie，开箱即用。微博需要配置 Cookie。

## 指令

```
/搜索 抖音 <关键词>
/搜索 B站 <关键词>
/搜索 微博 <关键词>
/搜索 全部 <关键词>
```

### 示例

```
/搜索 抖音 美食
/搜索 全部 AI 教程
```

## 去重机制

使用 SimHash 算法对搜索结果进行文本相似度去重，支持设置相似度阈值。

## 更新日志

### v2.0.0
- 🎯 抖音搜索完全重构，手机版 Playwright 方案
- 🚫 不再需要 Cookie、代理、签名
- 🧹 移除 X-Bogus 依赖

### v1.0.0
- 初始版本，基于 X-Bogus 签名 + requests API
