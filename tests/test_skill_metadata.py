from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.skill_engine import parse_skill_metadata


class SkillMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.skill = Path(self.directory.name) / "SKILL.md"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def parse(self, header: str, *, prefix: str = "", newline: str = "\n") -> dict[str, str]:
        text = prefix + "---\n" + header + "---\n\n# Body\n\nname: body-only\n"
        self.skill.write_bytes(text.replace("\n", newline).encode("utf-8"))
        return parse_skill_metadata(self.skill)

    def test_screenshot_folded_chinese_description_is_complete(self) -> None:
        result = self.parse(
            "name: character-design\n"
            "description: >\n"
            "  创建并保持一致的角色设计。\n"
            "  支持角色参考图、表情图和角色转视频。\n"
        )
        self.assertEqual(result["description"], "创建并保持一致的角色设计。 支持角色参考图、表情图和角色转视频。\n")
        self.assertEqual(result["name"], "character-design")

    def test_block_styles_and_chomping_have_exact_yaml_values(self) -> None:
        expected = {
            ">": "First line Second line\n",
            ">-": "First line Second line",
            ">+": "First line Second line\n\n\n",
            "|": "First line\nSecond line\n",
            "|-": "First line\nSecond line",
            "|+": "First line\nSecond line\n\n\n",
        }
        for indicator, value in expected.items():
            with self.subTest(indicator=indicator):
                result = self.parse(f"name: example\ndescription: {indicator}\n  First line\n  Second line\n\n\nversion: 1.20\n")
                self.assertEqual(result["description"], value)
                self.assertEqual(result["version"], "1.20")

    def test_explicit_indentation_and_header_comment(self) -> None:
        for indicator in (">2-", ">-2"):
            with self.subTest(indicator=indicator):
                result = self.parse(f"name: example\ndescription: {indicator} # folded text\n  First line\n  Second line\n")
                self.assertEqual(result["description"], "First line Second line")

    def test_paragraphs_and_more_indented_lines_are_preserved(self) -> None:
        result = self.parse(
            "name: example\ndescription: >-\n"
            "  First paragraph\n  continues here.\n\n"
            "  Second paragraph:\n    name: inside-description\n    # literal content\n  Last line.\n"
        )
        self.assertEqual(result["description"], "First paragraph continues here.\nSecond paragraph:\n  name: inside-description\n  # literal content\nLast line.")
        self.assertEqual(result["name"], "example")
        self.assertNotIn("Second paragraph", result)

    def test_inline_fence_text_does_not_truncate_metadata(self) -> None:
        result = self.parse('name: example\ndescription: "Keep --- inside text: with # characters."\nversion: 0.8.0\n')
        self.assertEqual(result["description"], "Keep --- inside text: with # characters.")
        self.assertEqual(result["version"], "0.8.0")

    def test_indented_fence_is_literal_description_content(self) -> None:
        result = self.parse("name: example\ndescription: |-\n  Before\n  ---\n  After\n")
        self.assertEqual(result["description"], "Before\n---\nAfter")

    def test_bom_crlf_and_trailing_fence_spaces(self) -> None:
        self.skill.write_bytes("\ufeff---\r\nname: example\r\ndescription: >-\r\n  First\r\n  Second\r\n---  \r\n# Body\r\n".encode("utf-8"))
        result = parse_skill_metadata(self.skill)
        self.assertEqual(result["name"], "example")
        self.assertEqual(result["description"], "First Second")

    def test_single_line_quotes_escapes_and_comments(self) -> None:
        cases = [
            ("plain description # comment", "plain description"),
            ("'It''s a description: with # text'", "It's a description: with # text"),
            (r'"Quoted \"word\" and \nline"', 'Quoted "word" and \nline'),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                result = self.parse(f"name: example\ndescription: {source}\n")
                self.assertEqual(result["description"], expected)

    def test_plain_and_quoted_multiline_scalars(self) -> None:
        for source in ('First line\n  Second line', '"First line\n  Second line"', "'First line\n  Second line'"):
            with self.subTest(source=source):
                self.assertEqual(self.parse(f"name: example\ndescription: {source}\n")["description"], "First line Second line")

    def test_metadata_stays_text_without_implicit_yaml_type_coercion(self) -> None:
        result = self.parse("name: on\ndescription: true\nversion: 001.20\noptional:\n")
        self.assertEqual(result["name"], "on")
        self.assertEqual(result["description"], "true")
        self.assertEqual(result["version"], "001.20")
        self.assertEqual(result["optional"], "")

    def test_nested_metadata_cannot_override_top_level_fields(self) -> None:
        result = self.parse("name: example\ndescription: top-level description\nmetadata:\n  name: nested\n  description: nested description\n")
        self.assertEqual(result["name"], "example")
        self.assertEqual(result["description"], "top-level description")
        self.assertNotIn("metadata", result)

    def test_invalid_yaml_and_non_mapping_roots_return_no_partial_metadata(self) -> None:
        for header in ("name: example\ndescription: [unterminated\n", "- name\n- description\n", "plain scalar\n"):
            with self.subTest(header=header):
                self.assertEqual(set(self.parse(header)), {"body_preview"})

    def test_non_scalar_description_is_not_stringified(self) -> None:
        result = self.parse("name: example\ndescription:\n  - first\n  - second\n")
        self.assertEqual(result["name"], "example")
        self.assertNotIn("description", result)

    def test_unsafe_yaml_tags_never_execute_input(self) -> None:
        with patch("os.system") as system:
            result = self.parse('name: example\ndescription: !!python/object/apply:os.system ["echo forbidden"]\n')
        system.assert_not_called()
        self.assertEqual(set(result), {"body_preview"})

    def test_missing_or_non_standalone_frontmatter_fences_are_ignored(self) -> None:
        for text in ("# Body\nname: body-only\n", "---\nname: no-closing-fence\n", "---invalid\nname: example\n---\n"):
            with self.subTest(text=text):
                self.skill.write_text(text, encoding="utf-8")
                result = parse_skill_metadata(self.skill)
                self.assertEqual(set(result), {"body_preview"})
                self.assertLessEqual(len(result["body_preview"]), 360)


if __name__ == "__main__":
    unittest.main()
