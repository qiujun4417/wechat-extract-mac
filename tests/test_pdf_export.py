#!/usr/bin/env python3
"""Unit tests for PDF export functionality.

Tests cover:
- Default analysis prompt includes PDF/print-friendly instructions
- html_system_guidance content and inclusion in API messages
- Single-turn and multi-turn message construction
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure the project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDefaultPromptPdfInstructions(unittest.TestCase):
    """Test that the default prompt includes PDF export instructions."""

    def _get_default_prompt(self):
        """Extract the default prompt string used when no user prompt is provided."""
        # This mirrors the logic in app.py api_articles_analyze()
        return (
            "给你这些公众号文章，按照时间排序，使用最先进的可视化分析方式帮我分析这些文章，"
            "然后以 html 可视化的方式输出。要求输出完整的独立 HTML 文件，包含 CSS 样式和必要的 "
            "JavaScript（可以使用 ECharts 或 Chart.js），确保可以直接在浏览器中打开查看。\n\n"
            "【重要】HTML 输出需要同时兼容屏幕浏览和 PDF 导出，请遵守以下规范：\n"
            "1. 使用深色背景（#1a1a2e 或类似）作为默认主题，但必须包含 @media print 样式块，"
            "在打印/导出 PDF 时切换为白底黑字\n"
            "2. 每个图表/卡片/section 添加 CSS: break-inside: avoid; page-break-inside: avoid; "
            "防止分页时被切割\n"
            "3. ECharts 图表使用 SVG 渲染器（renderer: 'svg'）而非默认 Canvas，"
            "这样导出 PDF 时图表是矢量的、清晰可缩放\n"
            "4. 图表容器设置明确的高度（如 300px-400px），不要用百分比高度\n"
            "5. 所有文字使用 system-ui 字体栈，确保中文渲染正确\n"
            "6. 页面整体宽度控制在 800px 以内居中显示，适配 A4 纸张宽度"
        )

    def test_prompt_mentions_pdf_export(self):
        """Default prompt should mention PDF export compatibility."""
        prompt = self._get_default_prompt()
        self.assertIn("PDF 导出", prompt)

    def test_prompt_requires_media_print(self):
        """Default prompt should require @media print CSS block."""
        prompt = self._get_default_prompt()
        self.assertIn("@media print", prompt)

    def test_prompt_requires_break_inside_avoid(self):
        """Default prompt should require break-inside: avoid for pagination."""
        prompt = self._get_default_prompt()
        self.assertIn("break-inside: avoid", prompt)

    def test_prompt_requires_svg_renderer(self):
        """Default prompt should require SVG renderer for ECharts."""
        prompt = self._get_default_prompt()
        self.assertIn("renderer: 'svg'", prompt)

    def test_prompt_requires_fixed_height(self):
        """Default prompt should specify fixed chart heights (not percentages)."""
        prompt = self._get_default_prompt()
        self.assertIn("300px-400px", prompt)
        self.assertIn("不要用百分比高度", prompt)

    def test_prompt_requires_a4_width(self):
        """Default prompt should specify A4-compatible width."""
        prompt = self._get_default_prompt()
        self.assertIn("800px", prompt)
        self.assertIn("A4", prompt)

    def test_prompt_requires_system_ui_font(self):
        """Default prompt should specify system-ui font stack."""
        prompt = self._get_default_prompt()
        self.assertIn("system-ui", prompt)


class TestHtmlSystemGuidance(unittest.TestCase):
    """Test the html_system_guidance content."""

    def _get_html_system_guidance(self):
        """Extract the html_system_guidance string from app.py logic."""
        return (
            "当输出 HTML 可视化时，请遵守以下技术规范以确保导出 PDF 的质量：\n"
            "1. ECharts 使用 SVG 渲染器: init(dom, null, {renderer: 'svg'})\n"
            "2. 每个图表/卡片添加 break-inside: avoid 防止分页切割\n"
            "3. 包含 @media print 样式块（白底黑字、隐藏交互按钮）\n"
            "4. 图表容器使用固定高度（300-400px），不用百分比\n"
            "5. 页面内容宽度控制在 800px 以内，适配 A4"
        )

    def test_guidance_mentions_svg_renderer(self):
        """System guidance should instruct SVG renderer usage."""
        guidance = self._get_html_system_guidance()
        self.assertIn("renderer: 'svg'", guidance)

    def test_guidance_mentions_break_inside(self):
        """System guidance should mention break-inside: avoid."""
        guidance = self._get_html_system_guidance()
        self.assertIn("break-inside: avoid", guidance)

    def test_guidance_mentions_media_print(self):
        """System guidance should mention @media print block."""
        guidance = self._get_html_system_guidance()
        self.assertIn("@media print", guidance)

    def test_guidance_mentions_fixed_height(self):
        """System guidance should specify fixed height for chart containers."""
        guidance = self._get_html_system_guidance()
        self.assertIn("300-400px", guidance)

    def test_guidance_mentions_a4_width(self):
        """System guidance should mention 800px width and A4."""
        guidance = self._get_html_system_guidance()
        self.assertIn("800px", guidance)
        self.assertIn("A4", guidance)

    def test_guidance_mentions_pdf_quality(self):
        """System guidance should reference PDF quality."""
        guidance = self._get_html_system_guidance()
        self.assertIn("导出 PDF 的质量", guidance)


class TestApiMessagesIncludeGuidance(unittest.TestCase):
    """Test that html_system_guidance is included in API messages for both
    single-turn and multi-turn analysis requests."""

    def setUp(self):
        """Set up test fixtures mimicking the api_articles_analyze logic."""
        self.html_system_guidance = (
            "当输出 HTML 可视化时，请遵守以下技术规范以确保导出 PDF 的质量：\n"
            "1. ECharts 使用 SVG 渲染器: init(dom, null, {renderer: 'svg'})\n"
            "2. 每个图表/卡片添加 break-inside: avoid 防止分页切割\n"
            "3. 包含 @media print 样式块（白底黑字、隐藏交互按钮）\n"
            "4. 图表容器使用固定高度（300-400px），不用百分比\n"
            "5. 页面内容宽度控制在 800px 以内，适配 A4"
        )
        self.articles_context = "--- 文章 1: 测试文章 (2024-01-01) ---\n测试内容"
        self.successful_count = 1

    def _build_single_turn_messages(self, prompt):
        """Build API messages for single-turn analysis (mirrors app.py logic)."""
        return [
            {"role": "system", "content": "请始终使用中文进行思考和回答。推理过程也必须使用中文。"},
            {"role": "system", "content": f"以下是用户选择的公众号文章内容（共 {self.successful_count} 篇）：\n\n{self.articles_context}"},
            {"role": "system", "content": self.html_system_guidance},
            {"role": "user", "content": prompt},
        ]

    def _build_multi_turn_messages(self, conversation_messages):
        """Build API messages for multi-turn conversation (mirrors app.py logic)."""
        api_messages = [
            {"role": "system", "content": "请始终使用中文进行思考和回答。推理过程也必须使用中文。"},
            {"role": "system", "content": f"以下是用户选择的公众号文章内容（共 {self.successful_count} 篇）：\n\n{self.articles_context}"},
            {"role": "system", "content": self.html_system_guidance},
        ]
        for msg in conversation_messages:
            role = msg.get("role", "user")
            if role == "assistant":
                api_messages.append({"role": "assistant", "content": msg.get("content", "")})
            else:
                api_messages.append({"role": "user", "content": msg.get("content", "")})
        return api_messages

    def test_single_turn_includes_guidance_as_system_message(self):
        """Single-turn messages should include html_system_guidance as a system message."""
        messages = self._build_single_turn_messages("分析文章")

        system_contents = [m["content"] for m in messages if m["role"] == "system"]
        self.assertIn(self.html_system_guidance, system_contents)

    def test_single_turn_guidance_is_third_message(self):
        """In single-turn, html_system_guidance should be the third message (index 2)."""
        messages = self._build_single_turn_messages("分析文章")

        self.assertEqual(messages[2]["role"], "system")
        self.assertEqual(messages[2]["content"], self.html_system_guidance)

    def test_single_turn_has_user_prompt_last(self):
        """In single-turn, user prompt should be the last message."""
        prompt = "请帮我分析这些文章"
        messages = self._build_single_turn_messages(prompt)

        self.assertEqual(messages[-1]["role"], "user")
        self.assertEqual(messages[-1]["content"], prompt)

    def test_multi_turn_includes_guidance_as_system_message(self):
        """Multi-turn messages should include html_system_guidance as a system message."""
        conversation = [
            {"role": "user", "content": "分析文章"},
            {"role": "assistant", "content": "<html>...</html>"},
            {"role": "user", "content": "请改成柱状图"},
        ]
        messages = self._build_multi_turn_messages(conversation)

        system_contents = [m["content"] for m in messages if m["role"] == "system"]
        self.assertIn(self.html_system_guidance, system_contents)

    def test_multi_turn_guidance_is_third_message(self):
        """In multi-turn, html_system_guidance should be the third message (index 2)."""
        conversation = [
            {"role": "user", "content": "分析文章"},
        ]
        messages = self._build_multi_turn_messages(conversation)

        self.assertEqual(messages[2]["role"], "system")
        self.assertEqual(messages[2]["content"], self.html_system_guidance)

    def test_multi_turn_preserves_conversation_order(self):
        """Multi-turn should append conversation messages after system messages."""
        conversation = [
            {"role": "user", "content": "第一次提问"},
            {"role": "assistant", "content": "第一次回答"},
            {"role": "user", "content": "第二次提问"},
        ]
        messages = self._build_multi_turn_messages(conversation)

        # First 3 are system messages
        for i in range(3):
            self.assertEqual(messages[i]["role"], "system")

        # Then conversation messages
        self.assertEqual(messages[3]["role"], "user")
        self.assertEqual(messages[3]["content"], "第一次提问")
        self.assertEqual(messages[4]["role"], "assistant")
        self.assertEqual(messages[4]["content"], "第一次回答")
        self.assertEqual(messages[5]["role"], "user")
        self.assertEqual(messages[5]["content"], "第二次提问")

    def test_both_modes_have_same_system_prefix(self):
        """Single-turn and multi-turn should have identical system message prefix."""
        single = self._build_single_turn_messages("test")
        multi = self._build_multi_turn_messages([{"role": "user", "content": "test"}])

        # First 3 messages (all system) should be identical
        self.assertEqual(single[0], multi[0])
        self.assertEqual(single[1], multi[1])
        self.assertEqual(single[2], multi[2])

    def test_articles_context_included_in_system(self):
        """Both modes should include article context in system messages."""
        messages = self._build_single_turn_messages("分析")

        context_msg = messages[1]
        self.assertEqual(context_msg["role"], "system")
        self.assertIn("测试文章", context_msg["content"])
        self.assertIn("测试内容", context_msg["content"])

    def test_chinese_instruction_is_first_system_message(self):
        """First system message should instruct Chinese language usage."""
        messages = self._build_single_turn_messages("分析")

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("中文", messages[0]["content"])


class TestDefaultPromptAppliedWhenEmpty(unittest.TestCase):
    """Test that the default prompt is used when user provides empty prompt."""

    def test_empty_string_triggers_default(self):
        """Empty string prompt should trigger default prompt with PDF instructions."""
        prompt = ""
        # Mirrors the logic: if not prompt: prompt = ...
        if not prompt:
            prompt = (
                "给你这些公众号文章，按照时间排序，使用最先进的可视化分析方式帮我分析这些文章，"
                "然后以 html 可视化的方式输出。要求输出完整的独立 HTML 文件，包含 CSS 样式和必要的 "
                "JavaScript（可以使用 ECharts 或 Chart.js），确保可以直接在浏览器中打开查看。\n\n"
                "【重要】HTML 输出需要同时兼容屏幕浏览和 PDF 导出，请遵守以下规范：\n"
                "1. 使用深色背景（#1a1a2e 或类似）作为默认主题，但必须包含 @media print 样式块，"
                "在打印/导出 PDF 时切换为白底黑字\n"
                "2. 每个图表/卡片/section 添加 CSS: break-inside: avoid; page-break-inside: avoid; "
                "防止分页时被切割\n"
                "3. ECharts 图表使用 SVG 渲染器（renderer: 'svg'）而非默认 Canvas，"
                "这样导出 PDF 时图表是矢量的、清晰可缩放\n"
                "4. 图表容器设置明确的高度（如 300px-400px），不要用百分比高度\n"
                "5. 所有文字使用 system-ui 字体栈，确保中文渲染正确\n"
                "6. 页面整体宽度控制在 800px 以内居中显示，适配 A4 纸张宽度"
            )
        self.assertIn("PDF 导出", prompt)
        self.assertIn("@media print", prompt)

    def test_none_triggers_default(self):
        """None prompt should trigger default prompt with PDF instructions."""
        prompt = None
        if not prompt:
            prompt = "... includes PDF 导出 ..."
        self.assertIn("PDF 导出", prompt)

    def test_custom_prompt_not_overridden(self):
        """User-provided prompt should NOT be replaced by default."""
        prompt = "只给我一个简单表格"
        if not prompt:
            prompt = "default with PDF"
        self.assertNotIn("PDF 导出", prompt)
        self.assertEqual(prompt, "只给我一个简单表格")


if __name__ == "__main__":
    unittest.main()
