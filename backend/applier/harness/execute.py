"""Execute only observed DOM targets through async Playwright.

Adapted from browser-use/jev-ultrafast, jev_ultrafast/browser.py (MIT License).
https://github.com/browser-use/jev-ultrafast
"""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import Error as PlaywrightError

from .snapshot import StalePage, fresh

_FOCUSED = """e => e.isConnected && !['password','hidden','file'].includes(e.type) &&
    !e.readOnly && !e.disabled && e.getRootNode().activeElement===e"""


async def _element(snap, target):
    frame = snap["_frame_refs"][target["frame"]]
    handle = await frame.evaluate_handle(
        "node => window.__jobpilotHarness?.nodes.get(node) || null", target["node"]
    )
    element = handle.as_element()
    if element is None:
        await handle.dispose()
        raise StalePage("Target no longer exists. Observe again.")
    return element


async def _point(element, action):
    valid = await element.evaluate(
        """(e, action) => {
          const c=window.__jobpilotHarness;
          if (!e.isConnected || ['password','hidden','file'].includes(e.type) ||
              e.matches(':disabled') || !c?.visible(e) ||
              c.closest(e,'[aria-disabled="true"],[inert]')) return false;
          if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true'))
            return false;
          if (action.kind==='select' && (e.tagName!=='SELECT' || ![...e.options].some(o=>
              o.value===action.value && !o.disabled && !o.closest('optgroup[disabled]'))))
            return false;
          const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
          return r.width>0 && r.height>0 && x>=0 && y>=0 && x<innerWidth && y<innerHeight &&
            c.hitTest(e,x,y);
        }""",
        action,
    )
    box = await element.bounding_box() if valid else None
    if not box:
        raise StalePage("Target changed or is covered. Observe again.")
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    # bounding_box uses main-frame coordinates. Check every enclosing iframe too.
    frame = await element.owner_frame()
    while frame.parent_frame is not None:
        host = await frame.frame_element()
        try:
            host_box = await host.bounding_box()
            if not host_box or not await host.evaluate(
                """(e, point) => {
                  const r=e.getBoundingClientRect(), c=window.__jobpilotHarness;
                  const x=r.x+(point.x-point.box.x)*r.width/point.box.width;
                  const y=r.y+(point.y-point.box.y)*r.height/point.box.height;
                  return c?.visible(e) && c.hitTest(e,x,y);
                }""",
                {"x": x, "y": y, "box": host_box},
            ):
                raise StalePage("Frame changed or is covered. Observe again.")
        finally:
            await host.dispose()
        frame = frame.parent_frame
    return x, y


async def _settle(page, frame, action, old_url, old_frame_url):
    try:
        await frame.evaluate(
            """action => new Promise(resolve => {
              const field=window.__jobpilotHarness?.nodes.get(action.node);
              const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
              let frames=0, stopped=false;
              const finish=()=>{stopped=true;resolve()};
              setTimeout(finish,autocomplete ? 200 : 50);
              const ready=()=>{
                if (stopped) return;
                const owned=field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns');
                const ids=(owned||'').split(/\\s+/).filter(Boolean);
                const root=field?.getRootNode()||document;
                const roots=ids.length ? ids.map(id=>root.getElementById(id)).filter(Boolean)
                  : [root];
                const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                if (++frames>=2 && (!autocomplete || options.some(e=>{
                  const r=e.getBoundingClientRect();
                  return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                }))) finish();
                else requestAnimationFrame(ready);
              };
              requestAnimationFrame(ready);
            })""",
            action,
        )
    except PlaywrightError:
        # Navigation may destroy the old execution context after a successful input.
        pass
    if page.url != old_url:
        await page.wait_for_load_state("domcontentloaded")
    elif not frame.is_detached() and frame.url != old_frame_url:
        await frame.wait_for_load_state("domcontentloaded")


async def act(page, snap: dict, action: dict, text: str | None = None) -> None:
    if action not in snap["actions"] or not await fresh(page, snap, action):
        raise StalePage("Page changed since this decision. Observe again.")
    kind = action["kind"]
    if kind == "fill" and not isinstance(text, str):
        raise ValueError("Fill requires text")
    frame = snap["_frame_refs"][action["frame"]]
    old_url, old_frame_url = page.url, frame.url
    if kind == "wait":
        await asyncio.sleep(0.1)
    elif kind == "scroll":
        if frame.parent_frame is None:
            viewport = await page.evaluate("({width:innerWidth,height:innerHeight})")
            x, y = viewport["width"] / 2, viewport["height"] / 2
        else:
            host = await frame.frame_element()
            try:
                x, y = await _point(host, action)
            finally:
                await host.dispose()
        await page.mouse.move(x, y)
        await page.mouse.wheel(0, action["delta"])
    else:
        element = await _element(snap, action)
        try:
            x, y = await _point(element, action)
            if kind == "select":
                await element.select_option(value=action["value"], timeout=1000)
            else:
                await page.mouse.click(x, y)
                if kind == "fill":
                    if not await element.evaluate(_FOCUSED):
                        raise StalePage("Field changed during focus. Observe again.")
                    await page.keyboard.press("Meta+A" if sys.platform == "darwin" else "Control+A")
                    if not await element.evaluate(_FOCUSED):
                        raise StalePage("Field changed during selection. Observe again.")
                    await page.keyboard.insert_text(text)
        finally:
            await element.dispose()
    await _settle(page, frame, action, old_url, old_frame_url)


async def attach(page, snap: dict, upload: dict, path: Path) -> None:
    target = {**upload, "kind": "upload"}
    if upload not in snap["uploads"] or not await fresh(page, snap, target):
        raise StalePage("Upload changed since observation. Observe again.")
    element = await _element(snap, upload)
    frame = snap["_frame_refs"][upload["frame"]]
    old_url, old_frame_url = page.url, frame.url
    try:
        valid = await element.evaluate("e => e.isConnected && e.type==='file' && !e.disabled")
        if not valid:
            raise StalePage("Upload is no longer available. Observe again.")
        await element.set_input_files(path, timeout=1000)
    finally:
        await element.dispose()
    await _settle(page, frame, target, old_url, old_frame_url)
