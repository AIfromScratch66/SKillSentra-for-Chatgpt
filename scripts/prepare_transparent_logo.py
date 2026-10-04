"""Remove the flat matte from the supplied SkillSentra logo without redrawing it."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image


def remove_matte(source: Path, destination: Path, fringe: int = 4) -> None:
    image = Image.open(source).convert("RGB")
    rgb = np.asarray(image, dtype=np.float32)

    border = np.concatenate(
        (rgb[:8].reshape(-1, 3), rgb[-8:].reshape(-1, 3), rgb[:, :8].reshape(-1, 3), rgb[:, -8:].reshape(-1, 3))
    )
    matte = np.median(border, axis=0)

    dark_limit = np.maximum(matte - fringe, 1)
    light_start = np.minimum(matte + fringe, 254)
    dark_alpha = np.maximum((dark_limit - rgb) / dark_limit, 0)
    light_alpha = np.maximum((rgb - light_start) / np.maximum(255 - light_start, 1), 0)
    alpha = np.maximum(dark_alpha.max(axis=2), light_alpha.max(axis=2))
    alpha[alpha < 0.018] = 0
    alpha = np.clip(alpha, 0, 1)

    safe_alpha = np.where(alpha > 0, alpha, 1)[..., None]
    foreground = (rgb - (1 - safe_alpha) * matte) / safe_alpha
    foreground = np.clip(foreground, 0, 255).astype(np.uint8)
    rgba = np.dstack((foreground, np.rint(alpha * 255).astype(np.uint8)))

    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(destination, optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    remove_matte(args.source, args.destination)


if __name__ == "__main__":
    main()
