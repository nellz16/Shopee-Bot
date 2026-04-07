import asyncio
from dataclasses import dataclass
from typing import Dict, Optional

from playwright.async_api import async_playwright

from modules.logger import get_logger

log = get_logger(__name__)


@dataclass
class AkamaiTokenResult:
    token: Optional[str]
    cookies: Dict[str, str]


class AkamaiTokenGenerator:
    def __init__(self, timeout_seconds: float = 60):
        self._timeout_seconds = timeout_seconds

    async def generate(self, product_url: str, cookies: Dict[str, str]) -> AkamaiTokenResult:
        token_event = asyncio.Event()
        captured_token: Optional[str] = None

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(locale="id-ID")

            cookie_payload = [
                {
                    "name": name,
                    "value": value,
                    "domain": ".shopee.co.id",
                    "path": "/",
                    "httpOnly": False,
                    "secure": True,
                }
                for name, value in cookies.items()
                if value is not None
            ]
            if cookie_payload:
                await context.add_cookies(cookie_payload)

            page = await context.new_page()

            def on_request(request) -> None:
                nonlocal captured_token
                if captured_token:
                    return
                if "/api/" not in request.url:
                    return
                token = request.headers.get("af-ac-enc-dat")
                if token and token != "null":
                    captured_token = token
                    token_event.set()

            page.on("request", on_request)

            try:
                await page.goto(product_url, wait_until="domcontentloaded", timeout=int(self._timeout_seconds * 1000))
                try:
                    await page.wait_for_load_state("networkidle", timeout=int(self._timeout_seconds * 1000))
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(token_event.wait(), timeout=self._timeout_seconds)
                except asyncio.TimeoutError:
                    log.warning("Timeout while waiting af-ac-enc-dat from Playwright session")

                updated_cookies = {
                    c["name"]: c["value"]
                    for c in await context.cookies("https://shopee.co.id")
                }

                if captured_token:
                    log.info("af-ac-enc-dat captured from browser traffic")
                else:
                    log.warning("af-ac-enc-dat not captured from browser traffic")

                return AkamaiTokenResult(token=captured_token, cookies=updated_cookies)
            finally:
                await context.close()
                await browser.close()
