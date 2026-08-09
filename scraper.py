#!/usr/bin/env python3
"""WeChat Article Scraper Module

Scrapes WeChat official account (公众号) articles from mp.weixin.qq.com URLs.

Can be used as:
1. Library: import scraper; scraper.scrape_article(url)
2. Subprocess: python scraper.py scrape --job <job_id>
   Outputs JSON lines to stdout for progress reporting.

Requirements:
    - requests
    - beautifulsoup4
    - scrapy (for bulk scraping)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# ============================================================
# Constants
# ============================================================

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRAPE_DIR = os.path.join(PROJECT_DIR, "scraped_articles")

# User-Agent pool (WeChat internal browser variants)
WECHAT_USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.43(0x18002b2d) NetType/4G Language/zh_CN",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.40(0x18002828) NetType/WIFI Language/zh_CN",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7 Pro) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/116.0.0.0 Mobile Safari/537.36 "
    "MicroMessenger/8.0.43.2480(0x28002b35)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36 "
    "MicroMessenger/3.8.4(0x13080411) MacWechat",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36 "
    "MicroMessenger/3.9.7(0x63090715)",
]

# Request defaults
DEFAULT_TIMEOUT = 20
MIN_DELAY = 1.0  # seconds between requests
MAX_DELAY = 3.0
MAX_RETRIES = 3


# ============================================================
# Core Scraping Functions
# ============================================================

def _get_random_ua():
    """Get a random WeChat-like User-Agent."""
    return random.choice(WECHAT_USER_AGENTS)


def _get_headers():
    """Build request headers mimicking WeChat browser."""
    return {
        "User-Agent": _get_random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://mp.weixin.qq.com/",
    }


def scrape_article(url, title="", timeout=DEFAULT_TIMEOUT):
    """Scrape a single WeChat article.

    Args:
        url: The article URL (mp.weixin.qq.com/s/...)
        title: Optional title hint (used if extraction fails)
        timeout: Request timeout in seconds

    Returns:
        dict: {success: bool, title: str, content: str, url: str, word_count: int, error: str}
    """
    result = {
        "success": False,
        "title": title,
        "content": "",
        "url": url,
        "word_count": 0,
        "error": "",
    }

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                url,
                headers=_get_headers(),
                timeout=timeout,
                allow_redirects=True,
            )

            # Check for captcha redirect
            if "wappoc_appmsgcaptcha" in resp.url or "captcha" in resp.url:
                result["error"] = "触发微信验证码，需在微信内打开链接"
                if attempt < MAX_RETRIES - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                return result

            if resp.status_code == 403:
                result["error"] = "403 Forbidden - 文章链接可能已过期"
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
                return result
            elif resp.status_code == 429:
                result["error"] = "429 Too Many Requests - 请求过于频繁"
                if attempt < MAX_RETRIES - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                return result
            elif resp.status_code != 200:
                result["error"] = f"HTTP {resp.status_code}"
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                return result

            # Parse HTML
            soup = BeautifulSoup(resp.text, "html.parser")

            # Extract title
            # WeChat articles use <h1 class="rich_media_title"> or <title>
            title_el = soup.find("h1", class_="rich_media_title")
            if title_el:
                result["title"] = title_el.get_text(strip=True)
            elif soup.title:
                result["title"] = soup.title.get_text(strip=True)
            elif not result["title"]:
                result["title"] = "Untitled"

            # Extract main content
            # WeChat article body is in #js_content
            content_el = soup.find(id="js_content")
            if not content_el:
                # Fallback: try rich_media_content
                content_el = soup.find(class_="rich_media_content")

            if content_el:
                # Remove script/style tags
                for tag in content_el.find_all(["script", "style", "noscript"]):
                    tag.decompose()

                # Extract text preserving paragraph structure
                paragraphs = []
                for el in content_el.find_all(["p", "section", "h1", "h2", "h3", "h4", "li", "blockquote"]):
                    text = el.get_text(strip=True)
                    if text:
                        paragraphs.append(text)

                if paragraphs:
                    result["content"] = "\n\n".join(paragraphs)
                else:
                    # Fallback: get all text
                    result["content"] = content_el.get_text(separator="\n", strip=True)
            else:
                # Check if this is an "expired" page
                page_text = soup.get_text()
                if "链接已过期" in page_text or "该内容已被发布者删除" in page_text:
                    result["error"] = "文章链接已过期或已被删除"
                    return result
                elif "环境异常" in page_text or "请完成验证" in page_text:
                    result["error"] = "触发微信反爬验证，请稍后重试"
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(10)
                        continue
                    return result
                else:
                    result["error"] = "无法提取文章正文内容"
                    return result

            result["word_count"] = len(result["content"])
            result["success"] = True
            return result

        except requests.exceptions.Timeout:
            result["error"] = "请求超时"
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
        except requests.exceptions.ConnectionError:
            result["error"] = "连接失败"
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
        except Exception as e:
            result["error"] = f"未知错误: {str(e)}"
            return result

    return result


def scrape_batch(job_id):
    """Scrape articles from a job file, output progress as JSON lines to stdout.

    Job file format (scraped_articles/<job_id>.json):
    {
        "articles": [{url, title, timestamp}, ...],
        "prompt": "user prompt",
        "status": "pending"
    }

    Progress output (one JSON per line):
    {"type": "progress", "index": N, "total": M, "title": "...", "status": "success|failed", "error": "..."}
    {"type": "done", "success_count": X, "fail_count": Y, "total_words": Z}
    """
    job_file = os.path.join(SCRAPE_DIR, f"{job_id}.json")
    if not os.path.exists(job_file):
        _emit({"type": "error", "message": f"Job file not found: {job_id}"})
        return

    with open(job_file, "r", encoding="utf-8") as f:
        job = json.load(f)

    articles = job.get("articles", [])
    if not articles:
        _emit({"type": "error", "message": "No articles in job"})
        return

    total = len(articles)
    results = []
    success_count = 0
    fail_count = 0
    total_words = 0

    _emit({"type": "info", "message": f"开始爬取 {total} 篇文章..."})

    for i, article in enumerate(articles):
        url = article.get("url", "")
        title = article.get("title", "")

        _emit({
            "type": "progress",
            "index": i,
            "total": total,
            "title": title,
            "status": "scraping",
            "progress": i / total * 100,
        })

        result = scrape_article(url, title=title)
        result["timestamp"] = article.get("timestamp", 0)
        result["time_str"] = article.get("time_str", "")
        results.append(result)

        if result["success"]:
            success_count += 1
            total_words += result["word_count"]
            _emit({
                "type": "progress",
                "index": i,
                "total": total,
                "title": result["title"],
                "status": "success",
                "word_count": result["word_count"],
                "progress": (i + 1) / total * 100,
            })
        else:
            fail_count += 1
            _emit({
                "type": "progress",
                "index": i,
                "total": total,
                "title": title,
                "status": "failed",
                "error": result["error"],
                "progress": (i + 1) / total * 100,
            })

        # Rate limiting between requests
        if i < total - 1:
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            time.sleep(delay)

    # Save results back to job file
    job["results"] = results
    job["status"] = "completed"
    job["completed_at"] = datetime.now().isoformat()
    job["success_count"] = success_count
    job["fail_count"] = fail_count
    job["total_words"] = total_words

    with open(job_file, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)

    _emit({
        "type": "done",
        "success_count": success_count,
        "fail_count": fail_count,
        "total_words": total_words,
        "message": f"爬取完成！成功 {success_count}/{total}，共 {total_words} 字",
    })


def _emit(data):
    """Emit a JSON line to stdout (for subprocess communication)."""
    print(json.dumps(data, ensure_ascii=False), flush=True)


# ============================================================
# CLI Entry Point
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WeChat Article Scraper")
    parser.add_argument("command", choices=["scrape", "test"],
                        help="Command to run")
    parser.add_argument("--job", help="Job ID for batch scraping")
    parser.add_argument("--url", help="Single URL for test scraping")

    args = parser.parse_args()

    os.makedirs(SCRAPE_DIR, exist_ok=True)

    if args.command == "scrape":
        if not args.job:
            print("Error: --job is required for scrape command", file=sys.stderr)
            sys.exit(1)
        scrape_batch(args.job)

    elif args.command == "test":
        if not args.url:
            print("Error: --url is required for test command", file=sys.stderr)
            sys.exit(1)
        result = scrape_article(args.url)
        print(json.dumps(result, ensure_ascii=False, indent=2))
