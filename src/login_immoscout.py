"""
One-time interactive ImmoScout24 login. Opens a real, visible browser window
on your machine so YOU can log in (Claude never sees or enters your
password). The session is saved to data/browser_profile/ and reused by the
headless scraper afterwards.

Usage: python src/login_immoscout.py
"""

import asyncio
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    user_data_dir = Path(__file__).parent.parent / "data" / "browser_profile"
    user_data_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=False,
            viewport={"width": 1280, "height": 900},
        )
        page = await browser.new_page()
        await page.goto("https://www.immobilienscout24.de/")

        print("\n" + "=" * 70)
        print("A browser window just opened on your screen.")
        print("1. Accept/close any cookie banner.")
        print("2. Click 'Anmelden' (Login) and log in with your own account.")
        print("3. Once you're logged in and see your account/dashboard,")
        print("   come back here and press Enter to save the session.")
        print("=" * 70 + "\n")

        await asyncio.get_event_loop().run_in_executor(None, input, "Press Enter once logged in... ")

        await browser.close()
        print(f"Session saved to {user_data_dir}")
        print("The scraper can now run headlessly using this session.")


if __name__ == "__main__":
    asyncio.run(main())
