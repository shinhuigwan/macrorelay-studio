#!/usr/bin/env python3
"""Encode MacroRelay AI recording frames into a compact MP4."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("frame_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fps", type=float, default=2.0)
    args = parser.parse_args()
    frames = sorted(
        path
        for path in args.frame_dir.iterdir()
        if path.is_file() and path.suffix.casefold() in {".png", ".jpg", ".jpeg"}
    ) if args.frame_dir.is_dir() else []
    if not frames:
        return 2
    first = cv2.imread(str(frames[0]), cv2.IMREAD_COLOR)
    if first is None:
        return 3
    height, width = first.shape[:2]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(args.output),
        cv2.VideoWriter_fourcc(*"mp4v"),
        max(0.2, float(args.fps)),
        (width, height),
    )
    if not writer.isOpened():
        return 4
    try:
        for path in frames:
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                continue
            if image.shape[1] != width or image.shape[0] != height:
                image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
            writer.write(image)
    finally:
        writer.release()
    return 0 if args.output.is_file() and args.output.stat().st_size > 0 else 5


if __name__ == "__main__":
    raise SystemExit(main())
