"""
save_session.py
Run this ONCE to log in manually and save your browser session.
Uses a persistent browser profile so SSO/corporate logins are fully captured.
"""

import asyncio
import os
from playwright.async_api import async_playwright

TRAINING_URL   = os.getenv("TRAINING_URL", "https://your-training-platform.com")
PROFILE_DIR    = "./browser-profile"   # Persistent profile folder


async def save_session():
    async with async_playwright() as pw:
        # Persistent context writes cookies/storage to disk continuously
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            slow_mo=100,
            viewport={"width": 1280, "height": 800},
            args=["--disable-blink-features=AutomationControlled"],  # Helps avoid bot detection
        )
        page = await context.new_page()

        print(f"\nOpening {TRAINING_URL}")
        print("=" * 60)
        print("  Log in manually in the browser window that just opened.")
        print("  Go all the way into a course so the player loads.")
        print("  Wait a few seconds for everything to settle.")
        print("  Then come back here and press ENTER.")
        print("=" * 60)

        await page.goto(TRAINING_URL)

        input("\n[WAITING] Press ENTER once you are fully logged in...")

        # Save a snapshot too as fallback
        await context.storage_state(path="session.json")

        print(f"\n[SAVED] Browser profile saved to '{PROFILE_DIR}/'")
        print("[SAVED] session.json also written as fallback.")
        print("\nYou can now run:  python3 agent.py")

        await context.close()


if __name__ == "__main__":
    asyncio.run(save_session())
