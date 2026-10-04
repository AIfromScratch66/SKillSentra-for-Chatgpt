"""Turn an image-generated mark on a checkerboard preview into a square RGBA icon."""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image


def exterior_checkerboard(rgb: np.ndarray) -> np.ndarray:
    minimum = rgb.min(axis=2)
    maximum = rgb.max(axis=2)
    candidate = (minimum >= 235) & ((maximum - minimum) <= 10)
    height, width = candidate.shape
    exterior = np.zeros((height, width), dtype=bool)
    queue: deque[tuple[int, int]] = deque()

    def add(y: int, x: int) -> None:
        if candidate[y, x] and not exterior[y, x]:
            exterior[y, x] = True
            queue.append((y, x))

    for x in range(width):
        add(0, x)
        add(height - 1, x)
    for y in range(height):
        add(y, 0)
        add(y, width - 1)

    while queue:
        y, x = queue.popleft()
        if y:
            add(y - 1, x)
        if y + 1 < height:
            add(y + 1, x)
        if x:
            add(y, x - 1)
        if x + 1 < width:
            add(y, x + 1)
    return exterior


def prepare_icon(source: Path, destination: Path, size: int = 512) -> None:
    rgb = np.asarray(Image.open(source).convert("RGB"), dtype=np.float32)
    exterior = exterior_checkerboard(rgb.astype(np.uint8))
    minimum = rgb.min(axis=2)
    maximum = rgb.max(axis=2)
    chroma = maximum - minimum

    alpha = np.ones(minimum.shape, dtype=np.float32)
    alpha[exterior] = 0
    soft_neutral = (~exterior) & (chroma <= 10) & (minimum <= 235)
    alpha[soft_neutral] = np.clip((244 - minimum[soft_neutral]) / 80, 0, 1)
    alpha[alpha < 0.02] = 0

    matte = np.full_like(rgb, 251)
    safe_alpha = np.where(alpha > 0, alpha, 1)[..., None]
    foreground = (rgb - (1 - safe_alpha) * matte) / safe_alpha
    foreground = np.clip(foreground, 0, 255).astype(np.uint8)
    rgba = np.dstack((foreground, np.rint(alpha * 255).astype(np.uint8)))

    visible = np.argwhere(rgba[..., 3] > 4)
    if visible.size == 0:
        raise ValueError("No foreground mark detected")
    top, left = visible.min(axis=0)
    bottom, right = visible.max(axis=0) + 1
    mark = Image.fromarray(rgba, "RGBA").crop((left, top, right, bottom))

    edge = max(mark.size)
    padding = max(round(edge * 0.12), 1)
    canvas_edge = edge + padding * 2
    canvas = Image.new("RGBA", (canvas_edge, canvas_edge), (0, 0, 0, 0))
    canvas.alpha_composite(mark, ((canvas_edge - mark.width) // 2, (canvas_edge - mark.height) // 2))
    icon = canvas.resize((size, size), Image.Resampling.LANCZOS)

    destination.parent.mkdir(parents=True, exist_ok=True)
    icon.save(destination, optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--size", type=int, default=512)
    args = parser.parse_args()
    prepare_icon(args.source, args.destination, args.size)


if __name__ == "__main__":
    main()
