from __future__ import annotations

import hashlib
import json
import struct
import unittest
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "demo-apple"
PLUGIN = ROOT / "plugins" / "skillsentra"


def png_alpha(path: Path) -> tuple[int, int, bytes]:
    payload = path.read_bytes()
    if payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("brand asset is not a PNG")

    offset = 8
    idat = bytearray()
    width = height = color_type = bit_depth = 0
    while offset < len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        kind = payload[offset + 4 : offset + 8]
        chunk = payload[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif kind == b"IDAT":
            idat.extend(chunk)
        elif kind == b"IEND":
            break

    if bit_depth != 8 or color_type != 6:
        raise AssertionError("brand PNG must use 8-bit RGBA pixels")

    packed = zlib.decompress(bytes(idat))
    stride = width * 4
    previous = bytearray(stride)
    alpha = bytearray()
    cursor = 0
    for _ in range(height):
        filter_kind = packed[cursor]
        cursor += 1
        raw = packed[cursor : cursor + stride]
        cursor += stride
        row = bytearray(stride)
        for index, value in enumerate(raw):
            left = row[index - 4] if index >= 4 else 0
            up = previous[index]
            upper_left = previous[index - 4] if index >= 4 else 0
            if filter_kind == 0:
                decoded = value
            elif filter_kind == 1:
                decoded = value + left
            elif filter_kind == 2:
                decoded = value + up
            elif filter_kind == 3:
                decoded = value + ((left + up) // 2)
            elif filter_kind == 4:
                estimate = left + up - upper_left
                distances = (abs(estimate - left), abs(estimate - up), abs(estimate - upper_left))
                decoded = value + (left if distances[0] <= distances[1] and distances[0] <= distances[2] else up if distances[1] <= distances[2] else upper_left)
            else:
                raise AssertionError(f"unsupported PNG filter {filter_kind}")
            row[index] = decoded & 0xFF
        alpha.extend(row[3::4])
        previous = row
    return width, height, bytes(alpha)


class BrandAssetTests(unittest.TestCase):
    def test_page_logo_is_transparent_without_changing_its_canvas(self) -> None:
        width, height, alpha = png_alpha(WEB / "skillsentra-logo-transparent.png")
        self.assertEqual((width, height), (928, 336))
        self.assertEqual(alpha[0], 0)
        self.assertEqual(min(alpha), 0)
        self.assertEqual(max(alpha), 255)

    def test_all_product_surfaces_use_the_transparent_logo(self) -> None:
        for name in ("index.html", "marketplace.html", "platform.html"):
            html = (WEB / name).read_text(encoding="utf-8")
            self.assertIn("skillsentra-icon.png?v=0.6.4", html)
            self.assertEqual(html.count('src="skillsentra-logo-transparent.png"'), 1)
            self.assertNotIn('src="skillsentra-logo.png"', html)

    def test_studio_keeps_the_full_english_logo_in_the_titlebar(self) -> None:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('src="skillsentra-logo-transparent.png"'), 1)
        self.assertNotIn('src="skillsentra-icon.png"', html)
        self.assertIn('class="titlebar-project"', html)
        self.assertNotIn('class="hero-mark"', html)

    def test_mark_only_png_is_square_and_genuinely_transparent(self) -> None:
        width, height, alpha = png_alpha(WEB / "skillsentra-icon.png")
        self.assertEqual((width, height), (512, 512))
        self.assertEqual(min(alpha), 0)
        self.assertEqual(max(alpha), 255)
        self.assertGreater(len(set(alpha)), 32)

    def test_existing_mark_only_positions_use_the_png_icon(self) -> None:
        expected_counts = {"index.html": 0, "marketplace.html": 2, "platform.html": 1}
        for name, count in expected_counts.items():
            html = (WEB / name).read_text(encoding="utf-8")
            self.assertEqual(html.count('src="skillsentra-icon.png"'), count)
            self.assertNotIn('src="skillsentra-mark.svg"', html)

    def test_plugin_icons_are_self_contained_brand_copies(self) -> None:
        manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        interface = manifest["interface"]
        expected = {
            "composerIcon": ("./assets/skillsentra-icon.svg", WEB / "skillsentra-favicon.svg"),
            "logo": ("./assets/skillsentra-icon.svg", WEB / "skillsentra-favicon.svg"),
        }
        for key, (relative, source) in expected.items():
            self.assertEqual(interface[key], relative)
            packaged = PLUGIN / relative
            self.assertTrue(packaged.is_file())
            self.assertEqual(hashlib.sha256(packaged.read_bytes()).digest(), hashlib.sha256(source.read_bytes()).digest())


if __name__ == "__main__":
    unittest.main()
