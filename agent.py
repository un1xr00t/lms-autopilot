"""
Training Auto-Agent — Coordinate-based clicking
Claude Vision identifies WHAT to click and WHERE (pixel coords).
Bypasses all iframe/DOM issues by clicking at screen coordinates.
"""

import asyncio
import base64
import os
import json
import sys
import time as _time
from playwright.async_api import async_playwright, Page, BrowserContext
import anthropic

# ── Config ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "your-key-here")
TRAINING_URL      = os.getenv("TRAINING_URL", "https://your-training-platform.com")
PROFILE_DIR       = "./browser-profile"

CHECK_INTERVAL_SEC  = 1
VIDEO_SKIP_SPEED    = 16
MAX_VIDEO_WAIT_SEC  = 600
SCREENSHOT_QUALITY  = 90   # Higher quality so Claude can read text clearly
POPUP_WAIT_SEC      = 20
VIEWPORT_W          = 1280
VIEWPORT_H          = 800
# ─────────────────────────────────────────────────────────────────────────────

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Token Tracking ────────────────────────────────────────────────────────────
_api_call_count = 0
_total_input_tokens = 0
_total_output_tokens = 0
_total_cost = 0.0

def _log_api_call(context: str, usage):
    """Track API calls and token usage."""
    global _api_call_count, _total_input_tokens, _total_output_tokens, _total_cost
    _api_call_count += 1
    input_tok = usage.input_tokens
    output_tok = usage.output_tokens
    cost = (input_tok * 3 / 1_000_000) + (output_tok * 15 / 1_000_000)
    _total_input_tokens += input_tok
    _total_output_tokens += output_tok
    _total_cost += cost
    print(f"[API #{_api_call_count}] {context} | in={input_tok} out={output_tok} cost=${cost:.4f} | TOTAL: ${_total_cost:.4f}")


# ── Claude Vision ─────────────────────────────────────────────────────────────

def analyze_screen(screenshot_bytes: bytes, hint: str = "", context: str = "analyze") -> dict:
    """
    Ask Claude to analyze the screen and return coordinates to click.
    Returns pixel (x, y) for every action needed.
    """
    system_prompt = f"""You are an AI agent controlling a browser to complete online training courses.
The browser viewport is {VIEWPORT_W}x{VIEWPORT_H} pixels.

Analyze the screenshot and return ONLY valid JSON with NO markdown fences.

Determine the current state:
- "video"       -> A video or animated slide is playing/showing
- "quiz"        -> A question or knowledge check requires answering AND has NOT been submitted yet
- "next_button" -> Quiz already answered/submitted, OR a Next/Continue/Close/arrow button is visible
- "complete"    -> The CURRENT MODULE just finished (shows completion message for this section only)
- "login"       -> Login form is visible
- "unknown"     -> Loading, blank, or unclear

IMPORTANT RULES:
- If checkboxes/answers are already checked AND no feedback popup is showing, state = "next_button"
- If a "That's Correct" or "Incorrect" popup was just dismissed, look for a Next arrow or Continue button — state = "next_button"
- Only use "quiz" if answers have NOT been selected yet
- Navigation arrows (> or >> or arrow icons at edges) count as next_button clicks

For each clickable item, provide the CENTER pixel coordinates (x, y) within the viewport.

Return this exact JSON structure:
{{
  "state": "<state>",
  "reasoning": "<one sentence explaining what you see>",
  "clicks": [
    {{"label": "<what this click does>", "x": <pixel x>, "y": <pixel y>}}
  ]
}}

The "clicks" array should contain:
- For quiz (unanswered): one entry per answer to select PLUS the Submit button
- For next_button: one entry for the Next/Continue/arrow button
- For video with interactive element: one entry for that element
- For other states: empty array []

Be precise — click the CENTER of buttons, checkboxes, and arrows.
"""
    img_b64 = base64.standard_b64encode(screenshot_bytes).decode()
    user_content = []
    if hint:
        user_content.append({"type": "text", "text": f"Context: {hint}"})
    user_content.append({
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}
    })
    user_content.append({"type": "text", "text": "Analyze this screen and return JSON with pixel coordinates."})

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}]
    )
    
    # Track token usage
    _log_api_call(context, response.usage)

    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except Exception:
                continue
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to salvage partial/truncated JSON by extracting what we can
        salvaged = _salvage_json(raw)
        if salvaged:
            return salvaged
        print(f"[WARN] Could not parse Claude response: {raw[:300]}")
        return {"state": "unknown", "reasoning": raw, "clicks": []}


def _salvage_json(raw: str) -> dict:
    """Extract state and valid clicks from malformed/truncated JSON."""
    import re
    result = {"state": "unknown", "reasoning": "", "clicks": []}
    # Extract state
    m = re.search(r'"state"\s*:\s*"(\w+)"', raw)
    if m:
        result["state"] = m.group(1)
    # Extract reasoning
    m2 = re.search(r'"reasoning"\s*:\s*"([^"]*)"', raw)
    if m2:
        result["reasoning"] = m2.group(1)
    # Extract all well-formed click objects: {"label": "...", "x": N, "y": N}
    clicks = re.findall(r'\{[^}]*"label"\s*:\s*"([^"]+)"[^}]*"x"\s*:\s*(\d+)[^}]*"y"\s*:\s*(\d+)[^}]*\}', raw)
    for label, x, y in clicks:
        result["clicks"].append({"label": label, "x": int(x), "y": int(y)})
    if result["state"] != "unknown" or result["clicks"]:
        print(f"[SALVAGE] Recovered state={result['state']} with {len(result['clicks'])} clicks")
        return result
    return None


# ── Page Actions ───────────────────────────────────────────────────────────────

async def take_screenshot(page: Page) -> bytes:
    return await page.screenshot(type="jpeg", quality=SCREENSHOT_QUALITY)


async def get_active_page(context: BrowserContext, current: Page) -> Page:
    for p in reversed(context.pages):
        if any(k in p.url for k in ["player", "ContentEngine", "course", "module"]):
            if p != current:
                print(f"[AGENT] Switched to: {p.url}")
            return p
    return current


async def perform_clicks(page: Page, clicks: list):
    """Click each coordinate in sequence."""
    for item in clicks:
        x = item.get("x")
        y = item.get("y")
        label = item.get("label", "?")
        if x is None or y is None:
            print(f"[CLICK] Skipping '{label}' — no coordinates")
            continue
        print(f"[CLICK] {label} at ({x}, {y})")
        await page.mouse.click(x, y)
        await asyncio.sleep(0.4)


async def speed_up_video(page: Page):
    try:
        await page.evaluate(f"""
            document.querySelectorAll('video').forEach(v => {{
                v.playbackRate = {VIDEO_SKIP_SPEED};
                v.muted = true;
                if (v.duration > 0) v.currentTime = v.duration - 2;
            }});
            document.querySelectorAll('iframe').forEach(f => {{
                try {{
                    f.contentDocument.querySelectorAll('video').forEach(v => {{
                        v.playbackRate = {VIDEO_SKIP_SPEED};
                        v.muted = true;
                        if (v.duration > 0) v.currentTime = v.duration - 2;
                    }});
                }} catch(e) {{}}
            }});
        """)
    except Exception as e:
        print(f"[VIDEO] Speed up error: {e}")


async def wait_for_video_to_end(page: Page, clicked_items: set = None) -> bool:
    """One API call per 6s cycle. Single prompt handles next detection + interactions."""
    if clicked_items is None:
        clicked_items = set()
    print("[VIDEO] Monitoring video (checking every 6s)...")
    elapsed = 0
    stuck_count = 0

    while elapsed < MAX_VIDEO_WAIT_SEC:
        await speed_up_video(page)
        await asyncio.sleep(6)
        elapsed += 6

        screenshot = await take_screenshot(page)

        # Single call handles everything
        analysis = analyze_screen(screenshot,
            "Analyze this training slide. Return JSON with: "
            "state ('next_button' if 'click the next arrow' text is visible AND no popup is open, else 'video'), "
            "popup_open (bool: is a modal/popup with X button visible?), "
            "popup_x (object with x,y of the X close button, or null), "
            "clicks (array of ALL interactive content items with label/x/y - include ALL blocks/tabs/buttons even if highlighted/visited, "
            "EXCLUDE only: MENU, TRANSCRIPT, play/pause, volume, settings, < > player nav arrows).",
            context="video_loop")

        state      = analysis.get("state", "video")
        popup_open = analysis.get("popup_open", False)
        popup_x    = analysis.get("popup_x") or {}
        clicks     = analysis.get("clicks", [])

        print(f"[VIDEO] {elapsed}s state={state} popup={popup_open} items={len(clicks)}")

        # Exit if next arrow is ready
        if state == "next_button" and not popup_open:
            print("[VIDEO] Next arrow ready — handing off.")
            return True

        # Close popup if open
        if popup_open:
            px, py = popup_x.get("x"), popup_x.get("y")
            if px and py:
                print(f"[VIDEO] Closing popup at ({px},{py})")
                await page.mouse.click(px, py)
                await asyncio.sleep(0.3)
                # Click a few more times with slight offsets in case first click missed
                for dx, dy in [(-5, 0), (5, 0), (0, -5)]:
                    await page.mouse.click(px + dx, py + dy)
                    await asyncio.sleep(0.15)
            else:
                # LMS-specific fallback positions for close button
                for cx, cy in [(608, 191), (609, 195), (600, 190), (610, 185)]:
                    await page.mouse.click(cx, cy)
                    await asyncio.sleep(0.2)
            await asyncio.sleep(0.8)
            continue

        # Filter already-clicked items
        new_clicks = [c for c in clicks if c.get("label","") not in clicked_items]

        if not new_clicks:
            stuck_count += 1
            if stuck_count >= 2:
                print("[VIDEO] All interactions done — trying next arrow before exiting.")
                # Try common next arrow positions
                for ax, ay in [(625, 537), (620, 535), (630, 540), (601, 339)]:
                    await page.mouse.click(ax, ay)
                    await asyncio.sleep(0.3)
                return True
            continue

        stuck_count = 0
        for item in new_clicks:
            x, y = item.get("x"), item.get("y")
            label = item.get("label", "?")
            if not x or not y:
                continue
            if y < 90 or y > 630:
                print(f"[VIDEO] BLOCKED chrome: {label} at ({x},{y})")
                continue
            print(f"[VIDEO] Clicking: {label} at ({x},{y})")
            await page.mouse.click(x, y)
            clicked_items.add(label)
            await asyncio.sleep(1.2)
            # Quick screenshot to close any popup that appeared — reuse next cycle's call
            ss2 = await take_screenshot(page)
            chk = analyze_screen(ss2, 
                "Check if a modal/popup/overlay with an X close button is now visible. "
                "Return JSON: popup_open (true if modal visible), popup_x (object with x,y of X button, or null).",
                context="popup_check")
            if chk.get("popup_open"):
                p2 = chk.get("popup_x") or {}
                px2, py2 = p2.get("x"), p2.get("y")
                if px2 and py2:
                    await page.mouse.click(px2, py2)
                else:
                    # LMS-specific fallback positions
                    for cx, cy in [(608, 191), (609, 195), (600, 190)]:
                        await page.mouse.click(cx, cy)
                        await asyncio.sleep(0.2)
                await asyncio.sleep(0.6)

    print("[VIDEO] Timed out.")
    return False

# ── Main Agent Loop ────────────────────────────────────────────────────────────

async def run_agent():
    start_time = _time.time()
    
    if not os.path.exists(PROFILE_DIR):
        print(f"[ERROR] No browser profile at '{PROFILE_DIR}'.")
        print("  Run  python3 save_session.py  first.")
        sys.exit(1)

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            slow_mo=100,
            viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = await context.new_page()
        print(f"[AGENT] Navigating to {TRAINING_URL}")
        await page.goto(TRAINING_URL, wait_until="networkidle")

        print(f"[AGENT] Waiting up to {POPUP_WAIT_SEC}s for course popup...")
        try:
            popup_future = asyncio.ensure_future(context.wait_for_event("page"))
            done, _ = await asyncio.wait({popup_future}, timeout=POPUP_WAIT_SEC)
            if done:
                popup = popup_future.result()
                await popup.wait_for_load_state("domcontentloaded")
                page = popup
                print(f"[AGENT] Popup detected: {page.url}")
            else:
                popup_future.cancel()
                print("[AGENT] No popup — working on current page.")
        except Exception as e:
            print(f"[AGENT] Popup wait error: {e}")

        consecutive_unknowns = 0
        max_unknowns = 5
        last_click_labels = []
        same_action_count = 0
        max_same_action = 2
        modules_done = 0
        slide_clicked_items = set()  # Persists across video loop re-entries  # If same clicks repeat 2x, force next_button scan

        while True:
            page = await get_active_page(context, page)

            screenshot = await take_screenshot(page)
            analysis   = analyze_screen(screenshot, context="main_loop")
            state      = analysis.get("state", "unknown")
            reasoning  = analysis.get("reasoning", "")
            clicks     = analysis.get("clicks", [])

            print(f"\n[STATE] {state}")
            print(f"[REASON] {reasoning}")
            if clicks:
                print(f"[CLICKS] {[c.get('label') for c in clicks]}")

            # Loop detection — if same clicks repeat, try progressively different arrow positions
            current_labels = [c.get("label","") for c in clicks]
            if current_labels and current_labels == last_click_labels:
                same_action_count += 1
                if same_action_count >= max_same_action:
                    print(f"[AGENT] Stuck on same slide — trying all possible next arrows...")
                    # Try a series of known arrow/next-button hotspots across the player
                    # Note: popup window is ~668x569, so coordinates are relative to that
                    arrow_candidates = [
                        (625, 537),  # bottom-right player nav arrow
                        (620, 535),
                        (630, 540),
                        (640, 537),
                        (601, 400),  # mid-slide area
                        (620, 450),
                        (600, 500),
                        (334, 440),  # center area
                        (589, 447),  # START button area
                    ]
                    for ax, ay in arrow_candidates:
                        print(f"[AGENT] Trying arrow at ({ax}, {ay})")
                        await page.mouse.click(ax, ay)
                        await asyncio.sleep(0.8)
                        # Take screenshot and check if slide changed
                        ss_check = await take_screenshot(page)
                        check = analyze_screen(ss_check, "Did the slide advance? Is this a different slide now?", context="stuck_check")
                        new_labels = [c.get("label","") for c in check.get("clicks", [])]
                        if new_labels != current_labels or check.get("state") != state:
                            print(f"[AGENT] Slide advanced after clicking ({ax}, {ay})!")
                            clicks = check.get("clicks", [])
                            state = check.get("state", state)
                            break
                    same_action_count = 0
                    last_click_labels = []
                    continue
            else:
                same_action_count = 0
            last_click_labels = current_labels

            if state == "complete":
                modules_done += 1
                print(f"[AGENT] Module {modules_done} complete! Scanning course menu for next unchecked module...")
                await asyncio.sleep(2)

                # Take a fresh screenshot and zoom into the left menu area specifically
                ss_next = await take_screenshot(page)

                next_analysis = analyze_screen(ss_next,
                    "A module just completed. The course player is still open. "
                    "Look at the Course Menu on the LEFT SIDE of the screen. "
                    "It shows a list of modules with checkboxes. "
                    "Checked modules have a checkmark (done). Unchecked modules have an empty box (not done). "
                    "Find the FIRST unchecked/incomplete module link in that left menu and return its coordinates. "
                    "The menu items are typically in the left 200px of the screen, spread vertically. "
                    "Return state='next_button' and the EXACT center coordinates of that unchecked module text link. "
                    "Do NOT click anywhere near y=0 to y=50 (that is the top bar, not the menu). "
                    "Menu items are typically between y=100 and y=500.",
                    context="next_module_scan")
                next_clicks = next_analysis.get("clicks", [])
                print(f"[AGENT] Next module scan: {[c.get('label') for c in next_clicks]}")

                if next_clicks:
                    # Validate the click is in a reasonable menu position
                    for click in next_clicks:
                        cx, cy = click.get("x", 0), click.get("y", 0)
                        if cy < 80:
                            print(f"[AGENT] Ignoring bad coordinate ({cx},{cy}) — too close to top bar")
                            continue
                        print(f"[CLICK] {click.get('label')} at ({cx}, {cy})")
                        await page.mouse.click(cx, cy)
                        await asyncio.sleep(3)  # Wait for module to load
                        break
                else:
                    print("[AGENT] No more unchecked modules — entire course complete!")
                    break
                continue

            elif state == "login":
                print("[AGENT] Session expired — re-run save_session.py then agent.py")
                with open("stuck_screenshot.jpg", "wb") as f:
                    f.write(screenshot)
                break

            elif state == "video":
                await wait_for_video_to_end(page, slide_clicked_items)
                # Don't clear clicked_items here - it causes re-clicking on same slide
                # Items will naturally not match on new slides anyway
                await asyncio.sleep(1)

            elif state in ("quiz", "next_button"):
                if clicks:
                    await perform_clicks(page, clicks)
                    await asyncio.sleep(0.6)
                    # After any click, check if we just closed a popup or submitted —
                    # if so, immediately scan for the Next navigation arrow
                    labels_lower = [c.get("label","").lower() for c in clicks]
                    just_closed = any(w in l for l in labels_lower for w in ["close", "correct", "popup", "dismiss", "submit"])
                    if just_closed:
                        await asyncio.sleep(0.6)
                        ss_nav = await take_screenshot(page)
                        nav_analysis = analyze_screen(ss_nav,
                            "A quiz was just answered correctly and the popup was closed. "
                            "The quiz slide is still visible with answers checked. "
                            "Find the NEXT navigation arrow or button to advance to the next slide. "
                            "It is usually a right-facing arrow (>) at the right edge or bottom of the player. "
                            "Return state='next_button' and the coordinates of that arrow.",
                            context="post_quiz_nav")
                        nav_clicks = nav_analysis.get("clicks", [])
                        nav_state  = nav_analysis.get("state", "")
                        print(f"[NAV] Post-action scan: {nav_state} — {[c.get('label') for c in nav_clicks]}")
                        if nav_clicks and nav_state == "next_button":
                            await perform_clicks(page, nav_clicks)
                            await asyncio.sleep(0.6)
                else:
                    print("[AGENT] No clicks identified, waiting...")
                    await asyncio.sleep(CHECK_INTERVAL_SEC)

            elif state == "unknown":
                consecutive_unknowns += 1
                print(f"[AGENT] Unknown ({consecutive_unknowns}/{max_unknowns})...")
                if consecutive_unknowns >= max_unknowns:
                    with open("stuck_screenshot.jpg", "wb") as f:
                        f.write(screenshot)
                    print("[AGENT] Saved stuck_screenshot.jpg — pausing 30s")
                    await asyncio.sleep(30)
                    consecutive_unknowns = 0
                else:
                    await asyncio.sleep(CHECK_INTERVAL_SEC)
                continue

            consecutive_unknowns = 0
            await asyncio.sleep(CHECK_INTERVAL_SEC)

        await context.close()
        
        # Print final stats
        elapsed_min = (_time.time() - start_time) / 60
        print("\n" + "=" * 60)
        print("[FINAL STATS]")
        print(f"  Elapsed time:   {elapsed_min:.1f} minutes")
        print(f"  API calls:      {_api_call_count}")
        print(f"  Input tokens:   {_total_input_tokens:,}")
        print(f"  Output tokens:  {_total_output_tokens:,}")
        print(f"  Total cost:     ${_total_cost:.4f}")
        print(f"  Cost/minute:    ${_total_cost/max(elapsed_min,0.1):.4f}")
        print("=" * 60)
        print("[AGENT] Done.")


if __name__ == "__main__":
    asyncio.run(run_agent())
