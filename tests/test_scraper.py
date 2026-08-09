#!/usr/bin/env python3
"""Unit tests for the WeChat Article Scraper module.

Tests cover:
- Successful article scraping
- Expired/deleted article detection
- Anti-scrape captcha detection
- Truncated/invalid URL handling
- Network error handling (timeout, connection error)
- Batch scraping with mixed results
- Content extraction from different page structures
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests

from scraper import (
    SCRAPE_DIR,
    _get_headers,
    _get_random_ua,
    scrape_article,
    scrape_batch,
)


class FakeResponse:
    """Mock requests.Response for testing."""

    def __init__(self, text="", status_code=200, url=""):
        self.text = text
        self.status_code = status_code
        self.url = url or "https://mp.weixin.qq.com/s?test=1"


# ============================================================
# Sample HTML pages for testing
# ============================================================

SAMPLE_ARTICLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>测试文章标题</title></head>
<body>
<div class="rich_media">
    <h1 class="rich_media_title">AI 时代的教育变革</h1>
    <div id="js_content">
        <section>
            <p>人工智能正在深刻改变教育行业。</p>
            <p>从个性化学习到智能评估，AI 技术的应用前景广阔。</p>
            <h2>一、AI 辅助教学</h2>
            <p>通过自然语言处理技术，AI 可以理解学生的问题并给出精准的回答。</p>
            <h2>二、智能化评估</h2>
            <p>机器学习算法可以分析学生的学习模式，提供个性化的学习建议。</p>
        </section>
    </div>
</div>
</body>
</html>
"""

SAMPLE_ARTICLE_FALLBACK_HTML = """
<!DOCTYPE html>
<html>
<head><title>文章标题</title></head>
<body>
<div class="rich_media">
    <h1 class="rich_media_title">使用 rich_media_content 的文章</h1>
    <div class="rich_media_content" id="js_content_fallback">
        <p>这是用 rich_media_content class 的文章正文。</p>
        <p>第二段落内容。</p>
    </div>
</div>
</body>
</html>
"""

SAMPLE_EXPIRED_HTML = """
<!DOCTYPE html>
<html>
<head><title></title></head>
<body>
<div class="weui-msg">
    <div class="weui-msg__icon-area"><i class="weui-icon-warn weui-icon_msg"></i></div>
    <div class="weui-msg__text-area">
        <h2 class="weui-msg__title">链接已过期</h2>
        <p class="weui-msg__desc">该链接已过期，请联系发布者获取最新链接。</p>
    </div>
</div>
</body>
</html>
"""

SAMPLE_DELETED_HTML = """
<!DOCTYPE html>
<html>
<head><title></title></head>
<body>
<div class="weui-msg">
    <div class="weui-msg__text-area">
        <h2 class="weui-msg__title">该内容已被发布者删除</h2>
    </div>
</div>
</body>
</html>
"""

SAMPLE_CAPTCHA_HTML = """
<!DOCTYPE html>
<html>
<head><title></title></head>
<body>
<div class="weui-msg">
    <div class="top_tips warning">环境异常</div>
    <p>请完成验证后继续访问</p>
</div>
</body>
</html>
"""

SAMPLE_EMPTY_CONTENT_HTML = """
<!DOCTYPE html>
<html>
<head><title>Empty Article</title></head>
<body>
<div class="rich_media">
    <h1 class="rich_media_title">空白文章</h1>
    <div id="js_content" style="visibility:hidden;">
    </div>
</div>
</body>
</html>
"""

SAMPLE_NO_CONTENT_DIV_HTML = """
<!DOCTYPE html>
<html>
<head><title>No Content Div</title></head>
<body>
<div class="other-content">
    <p>这不是一篇正常的文章页面</p>
</div>
</body>
</html>
"""


# ============================================================
# Test Cases
# ============================================================


class TestGetHeaders(unittest.TestCase):
    """Test header generation."""

    def test_returns_dict_with_user_agent(self):
        headers = _get_headers()
        self.assertIn("User-Agent", headers)
        self.assertIn("MicroMessenger", headers["User-Agent"])

    def test_has_referer(self):
        headers = _get_headers()
        self.assertEqual(headers["Referer"], "https://mp.weixin.qq.com/")

    def test_random_ua_varies(self):
        """User-Agent should rotate (may take a few tries)."""
        uas = set()
        for _ in range(20):
            uas.add(_get_random_ua())
        # With 5 UAs, 20 calls should hit at least 2 different ones
        self.assertGreater(len(uas), 1)


class TestScrapeArticleSuccess(unittest.TestCase):
    """Test successful article scraping."""

    @patch("scraper.requests.get")
    def test_extracts_content_from_js_content(self, mock_get):
        mock_get.return_value = FakeResponse(text=SAMPLE_ARTICLE_HTML)

        result = scrape_article("http://mp.weixin.qq.com/s?test=1", title="hint")

        self.assertTrue(result["success"])
        self.assertEqual(result["title"], "AI 时代的教育变革")
        self.assertIn("人工智能正在深刻改变教育行业", result["content"])
        self.assertIn("个性化学习", result["content"])
        self.assertGreater(result["word_count"], 0)
        self.assertEqual(result["error"], "")

    @patch("scraper.requests.get")
    def test_extracts_from_rich_media_content_class(self, mock_get):
        mock_get.return_value = FakeResponse(text=SAMPLE_ARTICLE_FALLBACK_HTML)

        result = scrape_article("http://mp.weixin.qq.com/s?test=2")

        self.assertTrue(result["success"])
        self.assertIn("rich_media_content", result["content"] or "")
        # Actually it should find content in the class-based div
        # The id is different, so it falls back to class
        self.assertEqual(result["title"], "使用 rich_media_content 的文章")

    @patch("scraper.requests.get")
    def test_uses_title_hint_when_page_has_no_title(self, mock_get):
        html = '<html><body><div id="js_content"><p>Content</p></div></body></html>'
        mock_get.return_value = FakeResponse(text=html)

        result = scrape_article("http://mp.weixin.qq.com/s?test=3", title="My Title Hint")

        self.assertTrue(result["success"])
        self.assertEqual(result["title"], "My Title Hint")

    @patch("scraper.requests.get")
    def test_empty_js_content_uses_fallback_text(self, mock_get):
        """Even if #js_content exists but has no paragraph tags, use get_text fallback."""
        html = """
        <html><body>
        <h1 class="rich_media_title">标题</h1>
        <div id="js_content">
            直接放在 div 里的文字，没有 p 标签包裹。
        </div>
        </body></html>
        """
        mock_get.return_value = FakeResponse(text=html)

        result = scrape_article("http://mp.weixin.qq.com/s?test=4")

        self.assertTrue(result["success"])
        self.assertIn("直接放在", result["content"])


class TestScrapeArticleExpiredDeleted(unittest.TestCase):
    """Test detection of expired/deleted articles."""

    @patch("scraper.requests.get")
    def test_detects_expired_link(self, mock_get):
        mock_get.return_value = FakeResponse(text=SAMPLE_EXPIRED_HTML)

        result = scrape_article("http://mp.weixin.qq.com/s?old=1")

        self.assertFalse(result["success"])
        self.assertIn("过期", result["error"])

    @patch("scraper.requests.get")
    def test_detects_deleted_content(self, mock_get):
        mock_get.return_value = FakeResponse(text=SAMPLE_DELETED_HTML)

        result = scrape_article("http://mp.weixin.qq.com/s?deleted=1")

        self.assertFalse(result["success"])
        self.assertIn("过期", result["error"])  # Combined message includes both cases


class TestScrapeArticleAntiScrape(unittest.TestCase):
    """Test anti-scrape/captcha detection."""

    @patch("scraper.time.sleep")  # Skip actual delays in tests
    @patch("scraper.requests.get")
    def test_detects_captcha_verification(self, mock_get, mock_sleep):
        mock_get.return_value = FakeResponse(text=SAMPLE_CAPTCHA_HTML)

        result = scrape_article("http://mp.weixin.qq.com/s?blocked=1")

        self.assertFalse(result["success"])
        self.assertIn("反爬验证", result["error"])

    @patch("scraper.time.sleep")
    @patch("scraper.requests.get")
    def test_retries_on_captcha(self, mock_get, mock_sleep):
        """Should retry when hitting captcha."""
        mock_get.return_value = FakeResponse(text=SAMPLE_CAPTCHA_HTML)

        scrape_article("http://mp.weixin.qq.com/s?captcha=1")

        # Should have retried MAX_RETRIES times
        self.assertEqual(mock_get.call_count, 3)  # MAX_RETRIES = 3

    @patch("scraper.time.sleep")
    @patch("scraper.requests.get")
    def test_detects_captcha_redirect(self, mock_get, mock_sleep):
        """Should detect when redirected to captcha page."""
        mock_get.return_value = FakeResponse(
            text="<html><body>验证</body></html>",
            url="https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?poc_token=abc&target_url=xxx"
        )

        result = scrape_article("http://mp.weixin.qq.com/s?redirect=1")

        self.assertFalse(result["success"])
        self.assertIn("验证码", result["error"])


class TestScrapeArticleHTTPErrors(unittest.TestCase):
    """Test HTTP error handling."""

    @patch("scraper.time.sleep")
    @patch("scraper.requests.get")
    def test_handles_403_forbidden(self, mock_get, mock_sleep):
        mock_get.return_value = FakeResponse(text="", status_code=403)

        result = scrape_article("http://mp.weixin.qq.com/s?forbidden=1")

        self.assertFalse(result["success"])
        self.assertIn("403", result["error"])

    @patch("scraper.time.sleep")
    @patch("scraper.requests.get")
    def test_handles_429_rate_limit(self, mock_get, mock_sleep):
        mock_get.return_value = FakeResponse(text="", status_code=429)

        result = scrape_article("http://mp.weixin.qq.com/s?ratelimit=1")

        self.assertFalse(result["success"])
        self.assertIn("429", result["error"])
        # Should have retried
        self.assertEqual(mock_get.call_count, 3)

    @patch("scraper.time.sleep")
    @patch("scraper.requests.get")
    def test_handles_500_server_error(self, mock_get, mock_sleep):
        mock_get.return_value = FakeResponse(text="", status_code=500)

        result = scrape_article("http://mp.weixin.qq.com/s?error=1")

        self.assertFalse(result["success"])
        self.assertIn("500", result["error"])


class TestScrapeArticleNetworkErrors(unittest.TestCase):
    """Test network-level error handling."""

    @patch("scraper.time.sleep")
    @patch("scraper.requests.get")
    def test_handles_timeout(self, mock_get, mock_sleep):
        mock_get.side_effect = requests.exceptions.Timeout("Connection timed out")

        result = scrape_article("http://mp.weixin.qq.com/s?timeout=1")

        self.assertFalse(result["success"])
        self.assertIn("超时", result["error"])

    @patch("scraper.time.sleep")
    @patch("scraper.requests.get")
    def test_handles_connection_error(self, mock_get, mock_sleep):
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        result = scrape_article("http://mp.weixin.qq.com/s?noconn=1")

        self.assertFalse(result["success"])
        self.assertIn("连接失败", result["error"])

    @patch("scraper.requests.get")
    def test_handles_unexpected_exception(self, mock_get):
        mock_get.side_effect = ValueError("Something unexpected")

        result = scrape_article("http://mp.weixin.qq.com/s?crash=1")

        self.assertFalse(result["success"])
        self.assertIn("未知错误", result["error"])


class TestScrapeArticleNoContent(unittest.TestCase):
    """Test handling of pages with no extractable content."""

    @patch("scraper.requests.get")
    def test_no_content_div_returns_error(self, mock_get):
        mock_get.return_value = FakeResponse(text=SAMPLE_NO_CONTENT_DIV_HTML)

        result = scrape_article("http://mp.weixin.qq.com/s?nocontent=1")

        self.assertFalse(result["success"])
        self.assertIn("无法提取", result["error"])

    @patch("scraper.requests.get")
    def test_preserves_url_in_result(self, mock_get):
        mock_get.return_value = FakeResponse(text=SAMPLE_NO_CONTENT_DIV_HTML)

        url = "http://mp.weixin.qq.com/s?myurl=1"
        result = scrape_article(url)

        self.assertEqual(result["url"], url)


class TestScrapeArticleRetryLogic(unittest.TestCase):
    """Test retry behavior on transient errors."""

    @patch("scraper.time.sleep")
    @patch("scraper.requests.get")
    def test_retries_then_succeeds(self, mock_get, mock_sleep):
        """Should succeed if a retry works."""
        # First call: 429, second call: success
        mock_get.side_effect = [
            FakeResponse(text="", status_code=429),
            FakeResponse(text=SAMPLE_ARTICLE_HTML, status_code=200),
        ]

        result = scrape_article("http://mp.weixin.qq.com/s?retry=1")

        self.assertTrue(result["success"])
        self.assertEqual(mock_get.call_count, 2)

    @patch("scraper.time.sleep")
    @patch("scraper.requests.get")
    def test_timeout_then_succeeds(self, mock_get, mock_sleep):
        """Should succeed after a timeout if next attempt works."""
        mock_get.side_effect = [
            requests.exceptions.Timeout("timeout"),
            FakeResponse(text=SAMPLE_ARTICLE_HTML, status_code=200),
        ]

        result = scrape_article("http://mp.weixin.qq.com/s?retry2=1")

        self.assertTrue(result["success"])
        self.assertEqual(mock_get.call_count, 2)


class TestScrapeBatch(unittest.TestCase):
    """Test batch scraping with job file."""

    def setUp(self):
        """Create a temp directory for test job files."""
        self.test_dir = tempfile.mkdtemp()
        self._orig_scrape_dir = os.environ.get("SCRAPE_DIR")
        # Patch SCRAPE_DIR
        import scraper
        self._orig_dir = scraper.SCRAPE_DIR
        scraper.SCRAPE_DIR = self.test_dir

    def tearDown(self):
        """Clean up temp directory."""
        import shutil
        import scraper
        scraper.SCRAPE_DIR = self._orig_dir
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("scraper.time.sleep")
    @patch("scraper.requests.get")
    def test_batch_scrape_mixed_results(self, mock_get, mock_sleep):
        """Batch scrape with both success and failure."""
        # Create job file
        job_id = "test_batch"
        job_data = {
            "articles": [
                {"url": "http://mp.weixin.qq.com/s?a=1", "title": "成功文章", "timestamp": 1000},
                {"url": "http://mp.weixin.qq.com/s?b=2", "title": "失败文章", "timestamp": 2000},
                {"url": "http://mp.weixin.qq.com/s?c=3", "title": "过期文章", "timestamp": 3000},
            ],
            "prompt": "test prompt",
            "status": "pending",
        }
        job_file = os.path.join(self.test_dir, f"{job_id}.json")
        with open(job_file, "w") as f:
            json.dump(job_data, f)

        # Mock responses: success, no-content, expired
        mock_get.side_effect = [
            FakeResponse(text=SAMPLE_ARTICLE_HTML),
            FakeResponse(text=SAMPLE_NO_CONTENT_DIV_HTML),
            FakeResponse(text=SAMPLE_EXPIRED_HTML),
        ]

        # Capture stdout
        import io
        from contextlib import redirect_stdout

        output = io.StringIO()
        with redirect_stdout(output):
            scrape_batch(job_id)

        # Check job file was updated
        with open(job_file) as f:
            result = json.load(f)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["fail_count"], 2)
        self.assertGreater(result["total_words"], 0)
        self.assertEqual(len(result["results"]), 3)

        # Check individual results
        self.assertTrue(result["results"][0]["success"])
        self.assertFalse(result["results"][1]["success"])
        self.assertFalse(result["results"][2]["success"])

    @patch("scraper.time.sleep")
    @patch("scraper.requests.get")
    def test_batch_outputs_json_lines(self, mock_get, mock_sleep):
        """Verify JSON lines output format for subprocess communication."""
        job_id = "test_output"
        job_data = {
            "articles": [
                {"url": "http://mp.weixin.qq.com/s?x=1", "title": "Test", "timestamp": 1000},
            ],
            "prompt": "",
            "status": "pending",
        }
        job_file = os.path.join(self.test_dir, f"{job_id}.json")
        with open(job_file, "w") as f:
            json.dump(job_data, f)

        mock_get.return_value = FakeResponse(text=SAMPLE_ARTICLE_HTML)

        import io
        from contextlib import redirect_stdout

        output = io.StringIO()
        with redirect_stdout(output):
            scrape_batch(job_id)

        lines = [l for l in output.getvalue().strip().split("\n") if l]
        # Should have: info, progress(scraping), progress(success), done
        parsed = [json.loads(l) for l in lines]

        # Check types are present
        types = [p["type"] for p in parsed]
        self.assertIn("info", types)
        self.assertIn("progress", types)
        self.assertIn("done", types)

        # Check done message
        done_msg = [p for p in parsed if p["type"] == "done"][0]
        self.assertEqual(done_msg["success_count"], 1)
        self.assertEqual(done_msg["fail_count"], 0)

    def test_batch_missing_job_file(self):
        """Should handle missing job file gracefully."""
        import io
        from contextlib import redirect_stdout

        output = io.StringIO()
        with redirect_stdout(output):
            scrape_batch("nonexistent_job")

        lines = output.getvalue().strip().split("\n")
        parsed = json.loads(lines[0])
        self.assertEqual(parsed["type"], "error")

    @patch("scraper.time.sleep")
    @patch("scraper.requests.get")
    def test_batch_empty_articles(self, mock_get, mock_sleep):
        """Should handle empty articles list."""
        job_id = "test_empty"
        job_data = {"articles": [], "prompt": "", "status": "pending"}
        job_file = os.path.join(self.test_dir, f"{job_id}.json")
        with open(job_file, "w") as f:
            json.dump(job_data, f)

        import io
        from contextlib import redirect_stdout

        output = io.StringIO()
        with redirect_stdout(output):
            scrape_batch(job_id)

        lines = output.getvalue().strip().split("\n")
        parsed = json.loads(lines[0])
        self.assertEqual(parsed["type"], "error")
        self.assertIn("No articles", parsed["message"])


class TestScrapeArticleContentParsing(unittest.TestCase):
    """Test content extraction quality."""

    @patch("scraper.requests.get")
    def test_strips_script_and_style_tags(self, mock_get):
        html = """
        <html><body>
        <div id="js_content">
            <p>正文内容</p>
            <script>alert('xss')</script>
            <style>.hidden{display:none}</style>
            <p>更多内容</p>
        </div>
        </body></html>
        """
        mock_get.return_value = FakeResponse(text=html)

        result = scrape_article("http://mp.weixin.qq.com/s?clean=1")

        self.assertTrue(result["success"])
        self.assertIn("正文内容", result["content"])
        self.assertIn("更多内容", result["content"])
        self.assertNotIn("alert", result["content"])
        self.assertNotIn("display:none", result["content"])

    @patch("scraper.requests.get")
    def test_preserves_paragraph_structure(self, mock_get):
        html = """
        <html><body>
        <div id="js_content">
            <p>第一段</p>
            <p>第二段</p>
            <h2>小标题</h2>
            <p>第三段</p>
        </div>
        </body></html>
        """
        mock_get.return_value = FakeResponse(text=html)

        result = scrape_article("http://mp.weixin.qq.com/s?paragraphs=1")

        self.assertTrue(result["success"])
        # Paragraphs should be separated by double newlines
        self.assertIn("\n\n", result["content"])
        self.assertIn("第一段", result["content"])
        self.assertIn("小标题", result["content"])

    @patch("scraper.requests.get")
    def test_word_count_reflects_actual_content(self, mock_get):
        mock_get.return_value = FakeResponse(text=SAMPLE_ARTICLE_HTML)

        result = scrape_article("http://mp.weixin.qq.com/s?wc=1")

        self.assertTrue(result["success"])
        self.assertEqual(result["word_count"], len(result["content"]))
        self.assertGreater(result["word_count"], 50)


if __name__ == "__main__":
    unittest.main()
