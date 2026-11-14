"""Frame-aware DOM snapshots and semantic freshness checks.

Adapted from browser-use/jev-ultrafast, jev_ultrafast/browser.py (MIT License).
https://github.com/browser-use/jev-ultrafast
"""

import asyncio
import hashlib
import json
import re
from pathlib import Path

from playwright.async_api import Error as PlaywrightError

SUBMIT_PATTERN = re.compile(
    r"submit|send|apply|envoyer|postuler|soumettre|finish|complete|terminer|bewerben|absenden|"
    r"valider ma candidature",
    re.IGNORECASE,
)
ADVANCE_PATTERN = re.compile(
    r"next|continue|suivant|continuer|weiter|review|save and continue|étape suivante",
    re.IGNORECASE,
)
LABEL_CHARS = 100
MAX_SELECT_OPTIONS = 60
MAX_ACTIONS = 250

_READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
_LIMITS = {
    "submit_pattern": SUBMIT_PATTERN.pattern,
    "label_chars": LABEL_CHARS,
    "max_select_options": MAX_SELECT_OPTIONS,
    "max_actions": MAX_ACTIONS,
}
_MARKER = f"limits => {{ const state=({_READ_STATE})(limits); return state?.marker ?? null; }}"


class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


async def _frame_viewport(frame, state, parent):
    """Map frame-local points to the top page and intersect ancestor viewports."""
    if parent is None:
        return None
    host = await frame.frame_element()
    try:
        # Playwright already accumulates offsets through all enclosing frames.
        box = await host.bounding_box()
        metrics = await host.evaluate(
            "e => ({left:e.clientLeft,top:e.clientTop,width:e.offsetWidth,height:e.offsetHeight})"
        )
    finally:
        await host.dispose()
    if not box or not metrics["width"] or not metrics["height"]:
        return None
    sx, sy = box["width"] / metrics["width"], box["height"] / metrics["height"]
    x, y = box["x"] + metrics["left"] * sx, box["y"] + metrics["top"] * sy
    left, top, right, bottom = parent["clip"]
    return {
        "x": x,
        "y": y,
        "sx": sx,
        "sy": sy,
        "clip": (
            max(left, x),
            max(top, y),
            min(right, x + state["w"] * sx),
            min(bottom, y + state["h"] * sy),
        ),
    }


def _in_viewport(viewport, x, y):
    if viewport is None:
        return False
    left, top, right, bottom = viewport["clip"]
    return (
        left <= viewport["x"] + x * viewport["sx"] < right
        and top <= viewport["y"] + y * viewport["sy"] < bottom
    )


async def observe(page) -> dict:
    """Merge frames in page.frames order; None marks an unavailable frame.

    ``_frame_refs`` retains the actual frames so a replacement cannot inherit an
    old index. Each entry in ``frames`` holds that frame's page key and guards.
    """
    for attempt in range(10):
        frame_refs = list(page.frames)
        try:
            main = await frame_refs[0].evaluate(_READ_STATE, _LIMITS) if frame_refs else None
        except PlaywrightError:
            main = None
        if main is not None:
            break
        if attempt == 9:
            raise StalePage("Main frame unavailable during observation. Observe again.")
        await asyncio.sleep(0.02)

    frames, actions, uploads, controls = [], [], [], {}
    viewports = {}
    for index, frame in enumerate(frame_refs):
        try:
            state = main if index == 0 else await frame.evaluate(_READ_STATE, _LIMITS)
        except PlaywrightError:
            state = None
        frames.append(state)
        if state is None:
            continue
        if index == 0:
            viewport = {"x": 0, "y": 0, "sx": 1, "sy": 1, "clip": (0, 0, main["w"], main["h"])}
        else:
            try:
                viewport = await _frame_viewport(frame, state, viewports.get(frame.parent_frame))
            except PlaywrightError:
                viewport = None
        viewports[frame] = viewport
        for item in state["actions"]:
            if item["kind"] in {"scroll", "wait"}:
                if item["kind"] == "wait" or _in_viewport(viewport, state["w"] / 2, state["h"] / 2):
                    controls.setdefault(item["id"], {**item, "frame": index})
                continue
            rect = item["rect"]
            if not _in_viewport(viewport, rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2):
                continue
            if len(actions) < MAX_ACTIONS:
                actions.append({**item, "id": f"e{len(actions) + 1}", "frame": index})
        for item in state["uploads"]:
            uploads.append({**item, "id": f"u{len(uploads) + 1}", "frame": index})
    # Retain operation IDs, choosing the first frame that can scroll each way.
    actions.extend(item for item in controls.values() if item["kind"] == "scroll")
    if "wait" in controls:
        actions.append(controls["wait"])
    content = [
        {key: state[key] for key in ("url", "text", "actions", "uploads", "scroll")}
        if state
        else None
        for state in frames
    ]
    return {
        "url": page.url,
        "title": main["title"],
        "text": "\n".join(state["text"] for state in frames if state),
        "actions": actions,
        "uploads": uploads,
        "fingerprint": hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest(),
        "frames": frames,
        "_frame_refs": frame_refs,
    }


async def fresh(page, snap: dict, action: dict | None = None) -> bool:
    """Use scoped click/select guards; typing and completion compare every frame."""
    frames = list(page.frames)
    if frames != snap["_frame_refs"]:
        return False
    try:
        if action is not None and action["kind"] in {"click", "select", "upload"}:
            node, index = action["node"], action["frame"]
            if type(node) is not int or type(index) is not int or not 0 <= index < len(frames):
                return False
            state = snap["frames"][index]
            if state is None or state["guards"].get(str(node)) is None:
                return False
            current = await frames[index].evaluate(
                """action => {
                  const c=window.__jobpilotHarness;
                  if (!c) return null;
                  return [c.pageKey(),c.guard(c.nodes.get(action.node),action.kind==='upload')];
                }""",
                action,
            )
            return current == [state["page_key"], state["guards"][str(node)]]
        for frame, state in zip(frames, snap["frames"]):
            marker = await frame.evaluate(_MARKER, _LIMITS)
            if marker != (state["marker"] if state else None):
                return False
        return True
    except PlaywrightError:
        return False
