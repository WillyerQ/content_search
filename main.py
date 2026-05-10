"""
内容搜索聚合插件 v2
搜索抖音（API + X-Bogus 签名）
"""
import asyncio
import json
import os
import urllib.parse
from typing import Optional
from simhash import Simhash
import execjs
import requests

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

DOUYIN_COMMON_PARAMS = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "cookie_enabled": "true",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Edge",
    "browser_version": "120.0.0.0",
    "browser_online": "true",
    "engine_name": "Blink",
    "os_name": "Windows",
    "os_version": "10",
    "engine_version": "120.0.0.0",
    "platform": "PC",
    "screen_width": "1920",
    "screen_height": "1200",
}


@register("content_search", "AstrBot", "内容搜索聚合", "2.0.0")
class ContentSearchPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._dy_js = None
        self._browser = None

    async def _get_config(self, key, default=None):
        try:
            return self.context.get_config(key) or default
        except:
            return default

    def _parse_cookie(self, raw: str) -> str:
        """解析 Cookie，支持常规字符串和 JSON 数组两种格式"""
        raw = raw.strip()
        
        # 尝试 JSON 数组格式（EditThisCookie 等插件导出）
        if raw.startswith("["):
            try:
                cookies = json.loads(raw)
                pairs = []
                for c in cookies:
                    name = c.get("name", "")
                    value = c.get("value", "")
                    if name and value:
                        clean_v = "".join(ch for ch in value if ord(ch) < 128)
                        if clean_v:
                            pairs.append(f"{name}={clean_v}")
                if pairs:
                    return "; ".join(pairs)
            except:
                pass
        
        # 常规 Cookie 字符串格式
        parts = []
        for item in raw.split(";"):
            item = item.strip()
            if "=" in item:
                n, v = item.split("=", 1)
                if all(ord(c) < 128 for c in n + v):
                    parts.append(f"{n.strip()}={v.strip()}")
        return "; ".join(parts)

    def _get_douyin_js(self):
        if self._dy_js is None:
            js_path = os.path.join(PLUGIN_DIR, "douyin.js")
            with open(js_path, "r", encoding="utf-8") as f:
                self._dy_js = execjs.compile(f.read())
        return self._dy_js

    def _sign_request(self, query: str, ua: str) -> str:
        js = self._get_douyin_js()
        return js.call("sign", query, ua)

    async def _search_douyin(self, keyword: str) -> list:
        logger.info(f"[ContentSearch] 搜索抖音: {keyword}")
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True, args=["--no-sandbox"])
                ctx = b.new_context(
                    user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15",
                    viewport={"width": 390, "height": 844}, locale="zh-CN"
                )
                page = ctx.new_page()
                page.goto(f"https://www.douyin.com/search/{keyword}", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(8000)
                for _ in range(3):
                    page.evaluate("window.scrollBy(0, 600)")
                    page.wait_for_timeout(2000)
                all_data = page.evaluate("""() => {
                    const results = [];
                    const scripts = document.querySelectorAll('script');
                    scripts.forEach(s => {
                        const text = s.textContent || '';
                        if (!text.includes('aweme_info')) return;
                        const parts = text.split('"aweme_info":');
                        for (let i = 1; i < parts.length; i++) {
                            let depth = 0, j = 0;
                            for (; j < parts[i].length; j++) {
                                if (parts[i][j] === '{') depth++;
                                else if (parts[i][j] === '}') { depth--; if (depth === 0) { j++; break; } }
                            }
                            try {
                                const obj = JSON.parse(parts[i].slice(0, j));
                                if (obj.aweme_id) results.push(obj);
                            } catch(e) {}
                        }
                    });
                    return results;
                }""")
                b.close()
            max_n = int(await self._get_config("max_results", 10))
            return self._extract_dy_videos(all_data[:max_n*2])
        except Exception as e:
            logger.error(f"[ContentSearch] 抖音搜索失败: {e}")
            return [{"platform": "抖音", "title": f"❌ 失败: {str(e)[:50]}", "text": ""}]

        max_n = int(await self._get_config("max_results", 10))
        results = []
        for item in items[:max_n]:
            try:
                aweme_id = item.get("aweme_id", "")
                title = (item.get("title", "") or item.get("desc", "") or "无标题").strip()
                author = item.get("author", "")
                if isinstance(author, dict):
                    author = author.get("nickname", "")
                stats = item.get("statistics", {}) or {}
                digg = stats.get("digg_count", "")
                url = f"https://www.douyin.com/video/{aweme_id}" if aweme_id else ""

                results.append({
                    "platform": "抖音",
                    "title": title[:80],
                    "author": str(author)[:20],
                    "likes": str(digg),
                    "url": url,
                    "text": title,
                })
            except:
                continue

        return results

    def _deduplicate(self, items: list, threshold: int = 85) -> list:
        unique = []
        seen = []
        for item in items:
            text = item.get("text", "")
            if not text or text.startswith("❌") or text.startswith("未找到"):
                unique.append(item)
                continue
            try:
                h1 = Simhash(text)
                is_dup = False
                for s in seen:
                    if s:
                        h2 = Simhash(s)
                        if h1.similarity(h2) * 100 >= threshold:
                            is_dup = True
                            break
                if not is_dup:
                    seen.append(text)
                    unique.append(item)
            except:
                seen.append(text)
                unique.append(item)
        return unique

    def _format_results(self, keyword: str, items: list) -> str:
        if not items:
            return "没有找到结果"
        
        # Filter out error messages
        real_items = [i for i in items if not i["title"].startswith("❌") and not i["title"].startswith("未找到")]
        errors = [i for i in items if i["title"].startswith("❌") or i["title"].startswith("未找到")]

        lines = [f"🔍 「{keyword}」搜索结果\n"]
        
        if errors:
            lines.append(f"⚠️ {errors[0]['title']}\n")

        for i, item in enumerate(real_items[:20], 1):
            t = item.get("title", "")[:60]
            a = item.get("author", "")
            l = item.get("likes", "")
            u = item.get("url", "")
            lines.append(f"{i}. 🎵 **{t}**")
            if a:
                lines.append(f"   👤 {a}")
            if l:
                lines.append(f"   👍 {l}")
            if u:
                lines.append(f"   🔗 {u}")
            lines.append("")

        lines.append(f"共 {len(real_items)} 条结果（已去重）")
        return "\n".join(lines)

    @filter.command("搜索")
    async def search_cmd(self, event: AstrMessageEvent):
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=2)
        if len(parts) < 3:
            yield event.plain_result("格式：/搜索 <平台> <关键词>\n平台：抖音、B站")
            return

        platform = parts[1]
        keyword = parts[2]

        yield event.plain_result(f"🔍 正在搜索「{keyword}」...")

        try:
            results = []
            if platform in ("抖音", "全部"):
                items = await self._search_douyin(keyword)
                results.extend(items)

            if not results:
                yield event.plain_result("没有找到结果")
                return

            threshold = int(await self._get_config("similarity_threshold", 85))
            unique = self._deduplicate(results, threshold)
            reply = self._format_results(keyword, unique)
            yield event.plain_result(reply)

        except Exception as e:
            logger.error(f"[ContentSearch] 异常: {e}")
            yield event.plain_result(f"❌ 搜索失败: {str(e)[:200]}")

    async def terminate(self):
        if self._browser:
            await self._browser.close()
