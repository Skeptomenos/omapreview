#!/usr/bin/env python3
"""Build the synthetic PDF used by the README screenshots.

The document contains no personal or external source material. It is kept as
a small, reproducible fixture so the screenshots can be regenerated locally.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pymupdf


NAVY = (0.055, 0.12, 0.20)
BLUE = (0.18, 0.42, 0.68)
PALE_BLUE = (0.86, 0.92, 0.97)
ORANGE = (0.91, 0.43, 0.18)
PALE_ORANGE = (0.99, 0.91, 0.82)
INK = (0.16, 0.20, 0.25)
MUTED = (0.36, 0.42, 0.48)
PAPER = (0.985, 0.975, 0.94)


def text(page: pymupdf.Page, value: str, point: tuple[float, float], size: float,
         *, color=INK, font: str = "helv", align: int = 0) -> None:
    if align:
        x, y = point
        rect = pymupdf.Rect(x - 220, y - size, x, y + 5)
        page.insert_textbox(rect, value, fontsize=size, fontname=font, color=color, align=align)
    else:
        page.insert_text(point, value, fontsize=size, fontname=font, color=color)


def box(page: pymupdf.Page, rect: tuple[float, float, float, float], *, fill=None,
        stroke=None, width: float = 0.8, radius: float = 0) -> None:
    shape = page.new_shape()
    r = pymupdf.Rect(rect)
    if radius:
        shape.draw_rounded_rect(r, radius)
    else:
        shape.draw_rect(r)
    shape.finish(color=stroke, fill=fill, width=width)
    shape.commit()


def header(page: pymupdf.Page, section: str, number: str) -> None:
    text(page, "OMAPREVIEW", (48, 42), 9, color=BLUE, font="hebo")
    text(page, section.upper(), (547, 42), 8, color=MUTED, font="hebo", align=2)
    page.draw_line((48, 54), (547, 54), color=(0.72, 0.78, 0.82), width=0.7)
    text(page, number, (48, 804), 9, color=BLUE, font="hebo")
    text(page, "SYNTHETIC DEMO  /  NO PERSONAL DATA", (547, 804), 7, color=MUTED, font="hebo", align=2)


def page_one(doc: pymupdf.Document, illustration: Path) -> None:
    page = doc.new_page(width=595, height=842)
    page.draw_rect(page.rect, color=None, fill=PAPER)
    header(page, "README feature tour", "01")
    text(page, "Human-friendly.", (48, 110), 11, color=ORANGE, font="hebo")
    text(page, "Agent-native.", (164, 110), 11, color=BLUE, font="hebo")
    text(page, "A calmer way", (48, 178), 34, color=NAVY, font="tiro")
    text(page, "to work with PDFs.", (48, 216), 34, color=NAVY, font="tiro")
    text(page, "Review, annotate, sign, redact, and rearrange", (48, 252), 12, color=MUTED)
    text(page, "inside one focused desktop workspace.", (48, 270), 12, color=MUTED)
    box(page, (48, 302, 275, 347), fill=PALE_ORANGE, stroke=None)
    text(page, "The same workbench for people and agents.", (62, 330), 11, color=NAVY, font="hebo")
    if illustration.exists():
        page.insert_image(pymupdf.Rect(318, 105, 547, 286), filename=str(illustration), keep_proportion=True)
    box(page, (318, 302, 547, 347), fill=PALE_BLUE, stroke=None)
    text(page, "One editor. One op engine.", (334, 330), 11, color=NAVY, font="hebo")

    cards = [
        (48, "MARKUP", "Highlight, note, draw", PALE_BLUE, BLUE),
        (220, "SURGERY", "Move, crop, extract", PALE_ORANGE, ORANGE),
        (392, "HANDOFF", "CLI, JSON, MCP", (0.88, 0.94, 0.91), (0.12, 0.42, 0.31)),
    ]
    for x, label, caption, fill, accent in cards:
        box(page, (x, 390, x + 155, 470), fill=fill, stroke=None)
        text(page, label, (x + 14, 417), 8, color=accent, font="hebo")
        text(page, caption, (x + 14, 447), 11, color=NAVY, font="hebo")
    text(page, "Open a document. Make a mark. Keep the last mile human.", (48, 535), 18, color=NAVY, font="tiro")
    text(page, "The editor keeps proposed changes visible as movable ghosts until Save.", (48, 567), 10, color=MUTED)
    text(page, "The CLI and optional MCP server speak the same JSON operation contract.", (48, 584), 10, color=MUTED)
    page.draw_line((48, 650), (547, 650), color=(0.72, 0.78, 0.82), width=0.7)
    text(page, "Preview-style PDF review for Linux", (48, 680), 12, color=NAVY, font="hebo")
    text(page, "Built for a quiet desktop workflow, with a clear review point before consequential edits.", (48, 704), 10, color=MUTED)


def page_two(doc: pymupdf.Document) -> None:
    page = doc.new_page(width=595, height=842)
    page.draw_rect(page.rect, color=None, fill=PAPER)
    header(page, "Review in context", "02")
    text(page, "Review notes,", (48, 120), 31, color=NAVY, font="tiro")
    text(page, "in context.", (48, 156), 31, color=NAVY, font="tiro")
    text(page, "Small marks should stay attached to the page they explain.", (48, 190), 11, color=MUTED)
    box(page, (48, 230, 547, 286), fill=PALE_BLUE, stroke=None)
    text(page, "REVIEW PRINCIPLE", (66, 255), 8, color=BLUE, font="hebo")
    text(page, "A proposed edit is visible before it is written.", (66, 277), 15, color=NAVY, font="tiro")
    text(page, "Keep this sentence visible while testing a highlight ghost.", (48, 345), 13, color=INK)
    text(page, "The review window stays open while operations are proposed.", (48, 380), 13, color=INK)
    text(page, "Notes can carry a short explanation without changing the page text.", (48, 415), 13, color=INK)
    box(page, (48, 468, 547, 470), fill=None, stroke=ORANGE, width=2)
    text(page, "A focused workspace makes the important line easy to find.", (48, 522), 17, color=NAVY, font="tiro")
    rows = [
        ("01", "Read", "Find the target in page context."),
        ("02", "Mark", "Add a highlight, note, or shape."),
        ("03", "Save", "Commit only after the review is clear."),
    ]
    y = 600
    for n, label, caption in rows:
        text(page, n, (48, y), 10, color=ORANGE, font="hebo")
        text(page, label, (92, y), 12, color=NAVY, font="hebo")
        text(page, caption, (180, y), 10, color=MUTED)
        page.draw_line((92, y + 12), (547, y + 12), color=(0.82, 0.84, 0.82), width=0.6)
        y += 48


def page_three(doc: pymupdf.Document) -> None:
    page = doc.new_page(width=595, height=842)
    page.draw_rect(page.rect, color=None, fill=PAPER)
    header(page, "Agent handoff", "03")
    text(page, "From a careful", (48, 126), 31, color=NAVY, font="tiro")
    text(page, "review to a repeatable handoff.", (48, 162), 26, color=NAVY, font="tiro")
    text(page, "The human sees the page. The agent gets a stable operation list.", (48, 198), 11, color=MUTED)
    steps = [
        ("READ", "structured text + bboxes", BLUE, PALE_BLUE),
        ("PROPOSE", "JSON ops as visible ghosts", ORANGE, PALE_ORANGE),
        ("APPLY", "reviewed output PDF", (0.12, 0.42, 0.31), (0.88, 0.94, 0.91)),
    ]
    x = 48
    for i, (label, caption, accent, fill) in enumerate(steps):
        box(page, (x, 280, x + 145, 384), fill=fill, stroke=None)
        text(page, f"0{i + 1}", (x + 16, 313), 10, color=accent, font="hebo")
        text(page, label, (x + 16, 345), 15, color=NAVY, font="hebo")
        text(page, caption, (x + 16, 370), 9, color=MUTED)
        if i < 2:
            page.draw_line((x + 151, 332), (x + 167, 332), color=ORANGE, width=1.4)
            page.draw_line((x + 163, 328), (x + 168, 332), color=ORANGE, width=1.4)
            page.draw_line((x + 163, 336), (x + 168, 332), color=ORANGE, width=1.4)
        x += 172
    box(page, (48, 470, 547, 570), fill=NAVY, stroke=None)
    text(page, "omepreview apply document.pdf --ops edits.json -o out.pdf", (67, 515), 11, color=(0.98, 0.98, 0.96), font="cour")
    text(page, "Same engine. Same coordinates. A human confirmation point.", (48, 634), 17, color=NAVY, font="tiro")
    text(page, "Consequential operations default to dry-run / propose so the final write stays deliberate.", (48, 668), 10, color=MUTED)
    text(page, "That is the handoff: clear enough to automate, calm enough to review.", (48, 706), 10, color=MUTED)


def build(output: Path, illustration: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page_one(doc, illustration)
    page_two(doc)
    page_three(doc)
    doc.set_metadata({
        "title": "omapreview README demo",
        "author": "omapreview",
        "subject": "Synthetic documentation fixture",
        "keywords": "omapreview, PDF, demo, synthetic",
    })
    doc.save(str(output), garbage=4, deflate=True)
    doc.close()
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("omapreview-demo.pdf"))
    parser.add_argument("--illustration", type=Path, default=Path(__file__).parents[1] / "assets" / "readme-hills.jpg")
    args = parser.parse_args()
    print(build(args.output, args.illustration))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
