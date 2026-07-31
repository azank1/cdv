#!/usr/bin/env python3
"""Generate .github/assets/dag-board.gif from real cdv_dag_* tool calls.

Same approach as generate_cdv_gif.py: call the actual MCP tool functions (no
network, no LLM) and render the resulting DagRun state as a kanban-style
board — the same Pending/Ready/Running/Verified/Failed columns the VS Code
Loop Monitor renders from src/cdv/dag_scheduler.py's to_dict() output.

Requires Pillow: pip install pillow
Run from repo root: python scripts/generate_dag_board_gif.py
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

os.environ.setdefault("CDV_PROVIDER", "mock")

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".github" / "assets" / "dag-board.gif"

BG = (30, 30, 30)
PANEL = (37, 37, 38)
BORDER = (60, 60, 60)
COL_TITLE = (140, 140, 140)
TEXT = (212, 212, 212)
MUTED = (140, 140, 140)
GOAL = (220, 220, 220)

STATE_COLORS = {
    "pending": (110, 110, 110),
    "ready": (33, 150, 243),
    "running": (255, 152, 0),
    "verified": (76, 175, 80),
    "failed": (244, 67, 54),
}
COLUMNS = ["pending", "ready", "running", "verified", "failed"]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        ("DejaVuSansMono-Bold.ttf", "LiberationMono-Bold.ttf")
        if bold
        else ("DejaVuSansMono.ttf", "LiberationMono-Regular.ttf")
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


async def _collect_frames() -> list[dict]:
    """Drive a real 2-node DAG run through mcp_server's tool functions."""
    db_path = Path(tempfile.mkdtemp()) / "store.db"
    os.environ["CDV_DB"] = str(db_path)

    from cdv import mcp_server as m

    compiled = json.loads(m._tool_dag_compile(
        "refactor download() and pass tests",
        nodes=[
            {
                "id": "n1", "role": "implementer",
                "description": "Add retry logic with exponential backoff",
                "dependencies": [], "quality_criteria": ["retry logic"],
                "required_patterns": ["retry"],
            },
            {
                "id": "n2", "role": "test_runner",
                "description": "Run pytest against the refactor",
                "dependencies": ["n1"], "required_patterns": ["passed"],
            },
        ],
        task_type="bugfix",
    ))
    run_id = compiled["run_id"]
    frames = [json.loads(m._tool_dag_status(run_id))]

    await m._tool_dag_submit(
        run_id, "n1",
        "# retry logic: exponential backoff\ndef download(url, max_retry=3): ...",
        None,
    )
    frames.append(json.loads(m._tool_dag_status(run_id)))

    await m._tool_dag_submit(run_id, "n2", "pytest: 12 passed, 0 failed", None)
    frames.append(json.loads(m._tool_dag_status(run_id)))

    return frames


def _render_frame(state: dict, width: int = 860, height: int = 380) -> Image.Image:
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)
    title_font = _font(15, bold=True)
    col_font = _font(11, bold=True)
    node_font = _font(12)
    small_font = _font(10)

    draw.rectangle((10, 10, width - 10, height - 10), fill=PANEL, outline=BORDER, width=1)
    draw.text((22, 18), "CDV · DAG Runs (agent scrum-master)", fill=MUTED, font=title_font)
    goal = state.get("goal", "")[:80]
    draw.text((22, 42), goal, fill=GOAL, font=node_font)

    nodes = list(state.get("nodes", {}).values())
    col_w = (width - 44) // len(COLUMNS)
    top = 74
    for i, col in enumerate(COLUMNS):
        x = 22 + i * col_w
        col_nodes = [n for n in nodes if n["state"] == col]
        draw.text((x, top), f"{col.upper()} ({len(col_nodes)})", fill=COL_TITLE, font=col_font)
        y = top + 22
        for node in col_nodes:
            card_h = 70 if node["state"] in ("verified", "failed") else 46
            color = STATE_COLORS[col]
            draw.rectangle((x, y, x + col_w - 12, y + card_h), fill=(45, 45, 46), outline=color, width=2)
            draw.text((x + 8, y + 6), node["id"], fill=TEXT, font=node_font)
            desc = node.get("description", "")[:26]
            draw.text((x + 8, y + 24), desc, fill=MUTED, font=small_font)
            if node["state"] in ("verified", "failed"):
                score = node.get("verified_score", 0.0)
                draw.text((x + 8, y + 42), f"score {score:.2f}", fill=color, font=small_font)
                deficiencies = node.get("deficiencies") or []
                if deficiencies:
                    draw.text((x + 8, y + 56), deficiencies[0][:26], fill=(200, 120, 120), font=small_font)
            y += card_h + 8

    draw.text((22, height - 26), "Conservative Dual-Verify per node · cdv", fill=MUTED, font=small_font)
    return img


async def main() -> None:
    frames_data = await _collect_frames()
    images = [_render_frame(state) for state in frames_data]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        OUT,
        save_all=True,
        append_images=images[1:],
        duration=3500,
        loop=0,
        optimize=True,
    )
    size_kb = OUT.stat().st_size // 1024
    print(f"Wrote {OUT} ({size_kb} KB, {len(images)} frames)")


if __name__ == "__main__":
    asyncio.run(main())
