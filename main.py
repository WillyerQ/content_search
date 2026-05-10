"""
内容搜索聚合插件
搜索小红书、抖音，自动去重后返回结果
"""
import asyncio
import json
import os
import re
import time
from datetime import datetime
from typing import Optional
from simhash import Simhash

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api import logger


@register("content_search", "AstrBot", "内容搜索聚合", "1.0.0")
class ContentSearchPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._browser = None
        self._lock = asyncio.Lock()

    async def _get_config(self, key, default=None):
        try:
            return self.context.get_config(key) or default
        except Exception:
            return default

    async def _get_browser(self):
        """获取或创建 Playwright 浏览器实例"""
        if self._browser is None:
            from playwright.async_api import async_playwright
            p = await async_playwright().start()
            headless = await self._get_config("headless", True)
            self._browser = await p.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            logger.info("[ContentSearch] 浏览器已启动")
        return self._browser

    @filter.command("搜索")
    async def search_cmd(self, event: AstrMessageEvent):
        """搜索内容：/搜索 <平台> <关键词>"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=2)
        if len(parts) < 3:
            yield event.plain_result("格式：/搜索 <平台> <关键词>\n平台：小红书、抖音、全部")
            return

        platform = parts[1]
        keyword = parts[2]

        yield event.plain_result(f"🔍 正在搜索「{keyword}」（{platform}）...")

        try:
            results = []
            platforms = []

            if platform in ("小红书", "全部"):
                platforms.append(self._search_xiaohongshu)
            if platform in ("抖音", "全部"):
                platforms.append(self._search_douyin)

            if not platforms:
                yield event.plain_result("平台仅支持：小红书、抖音、全部")
                return

            for search_func in platforms:
                try:
                    items = await search_func(keyword)
                    results.extend(items)
                except Exception as e:
                    logger.error(f"[ContentSearch] 搜索失败: {e}")
                    yield event.plain_result(f"⚠️ 搜索出错: {str(e)[:100]}")

            if not results:
                yield event.plain_result("没有找到结果")
                return

            # 去重
            threshold = int(await self._get_config("similarity_threshold", 85))
            unique = self._deduplicate(results, threshold)

            # 格式化输出
            reply = self._format_results(platform, keyword, unique)
            yield event.plain_result(reply)

        except Exception as e:
            logger.error(f"[ContentSearch] 异常: {e}")
            yield event.plain_result(f"❌ 搜索失败: {str(e)[:200]}")

    async def _search_xiaohongshu(self, keyword: str) -> list:
        """搜索小红书"""
        logger.info(f"[ContentSearch] 搜索小红书: {keyword}")
        browser = await self._get_browser()
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            proxy={"server": os.environ.get("http_proxy", "")} if os.environ.get("http_proxy") else None
        )

        # 设置 Cookie
        cookie_str = await self._get_config("xhs_cookie", "")
        if cookie_str:
            await self._set_cookies(context, ".xiaohongshu.com", cookie_str)

        page = await context.new_page()
        results = []
        try:
            url = f"https://www.xiaohongshu.com/search_result?keyword={_encode(keyword)}&source=web_search_result_notes"
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            # 等待搜索结果加载
            try:
                await page.wait_for_selector(".feeds-page .note-item", timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(2)

            cards = await page.query_selector_all(".note-item")
            max_n = int(await self._get_config("max_results", 10))

            for card in cards[:max_n]:
                try:
                    title_el = await card.query_selector(".title")
                    title = (await title_el.inner_text()).strip() if title_el else "无标题"

                    link_el = await card.query_selector("a")
                    link = ""
                    if link_el:
                        link = await link_el.get_attribute("href") or ""
                        if link and not link.startswith("http"):
                            link = "https://www.xiaohongshu.com" + link

                    desc_el = await card.query_selector(".desc")
                    desc = (await desc_el.inner_text()).strip()[:100] if desc_el else ""

                    like_el = await card.query_selector(".like-wrapper .count")
                    likes = (await like_el.inner_text()).strip() if like_el else ""

                    results.append({
                        "platform": "小红书",
                        "title": title,
                        "desc": desc,
                        "url": link,
                        "likes": likes,
                        "text": title + " " + desc
                    })
                except Exception:
                    continue
        finally:
            await page.close()
            await context.close()

        logger.info(f"[ContentSearch] 小红书获取到 {len(results)} 条")
        return results

    async def _search_douyin(self, keyword: str) -> list:
        """搜索抖音"""
        logger.info(f"[ContentSearch] 搜索抖音: {keyword}")
        browser = await self._get_browser()
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            proxy={"server": os.environ.get("http_proxy", "")} if os.environ.get("http_proxy") else None
        )

        cookie_str = await self._get_config("dy_cookie", "")
        if cookie_str:
            await self._set_cookies(context, ".douyin.com", cookie_str)

        page = await context.new_page()
        results = []
        try:
            url = f"https://www.douyin.com/search/{_encode(keyword)}?type=general"
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            # 等待视频卡片加载
            try:
                await page.wait_for_selector(".search-result-card", timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(2)

            cards = await page.query_selector_all(".search-result-card")
            max_n = int(await self._get_config("max_results", 10))

            for card in cards[:max_n]:
                try:
                    title_el = await card.query_selector(".title")
                    title = (await title_el.inner_text()).strip() if title_el else "无标题"

                    link_el = await card.query_selector("a")
                    link = ""
                    if link_el:
                        link = await link_el.get_attribute("href") or ""
                        if link and not link.startswith("http"):
                            link = "https://www.douyin.com" + link

                    desc_el = await card.query_selector(".search-result-card-desc")
                    desc = (await desc_el.inner_text()).strip()[:100] if desc_el else ""

                    stats_el = await card.query_selector(".search-result-card-stats")
                    stats = (await stats_el.inner_text()).strip() if stats_el else ""

                    results.append({
                        "platform": "抖音",
                        "title": title,
                        "desc": desc,
                        "url": link,
                        "likes": stats,
                        "text": title + " " + desc
                    })
                except Exception:
                    continue
        finally:
            await page.close()
            await context.close()

        logger.info(f"[ContentSearch] 抖音获取到 {len(results)} 条")
        return results

    def _deduplicate(self, items: list, threshold: int = 85) -> list:
        """SimHash 去重"""
        unique = []
        seen = []

        for item in items:
            text = item.get("text", "")
            if not text:
                unique.append(item)
                continue

            # 完全一样直接跳过
            if any(text == s for s in seen):
                continue

            try:
                h1 = Simhash(text)
                is_dup = False
                for s in seen:
                    if s:
                        h2 = Simhash(s)
                        sim = h1.similarity(h2)
                        if sim * 100 >= threshold:
                            is_dup = True
                            break
                if not is_dup:
                    seen.append(text)
                    unique.append(item)
            except Exception:
                seen.append(text)
                unique.append(item)

        logger.info(f"[ContentSearch] 去重: {len(items)} → {len(unique)}")
        return unique

    def _format_results(self, platform: str, keyword: str, items: list) -> str:
        """格式化搜索结果"""
        if not items:
            return "没有找到结果"

        lines = [f"🔍 「{keyword}」搜索结果（{platform}）\n"]

        for i, item in enumerate(items[:20], 1):
            p = item.get("platform", "")
            t = item.get("title", "无标题")[:40]
            d = item.get("desc", "")
            u = item.get("url", "")
            l = item.get("likes", "")

            icon = "📕" if "小红书" in p else "🎵"
            lines.append(f"{i}. {icon} **{t}**")
            if d:
                lines.append(f"   {d[:60]}")
            if l:
                lines.append(f"   👍 {l}")
            lines.append("")

        lines.append(f"共 {len(items)} 条结果（已去重）")
        return "\n".join(lines)

    async def _set_cookies(self, context, domain: str, cookie_str: str):
        """从 Cookie 字符串设置浏览器 Cookie"""
        try:
            cookies = []
            for item in cookie_str.split(";"):
                item = item.strip()
                if "=" in item:
                    name, value = item.split("=", 1)
                    cookies.append({
                        "name": name.strip(),
                        "value": value.strip(),
                        "domain": domain,
                        "path": "/"
                    })
            if cookies:
                await context.add_cookies(cookies)
                logger.info(f"[ContentSearch] 已设置 {len(cookies)} 个 Cookie")
        except Exception as e:
            logger.warning(f"[ContentSearch] 设置 Cookie 失败: {e}")

    async def terminate(self):
        if self._browser:
            await self._browser.close()
            logger.info("[ContentSearch] 浏览器已关闭")


def _encode(text: str) -> str:
    """简单的 URL 编码"""
    from urllib.parse import quote
    return quote(text)
