import asyncio
from dataclasses import dataclass
from typing import Dict, Optional

from playwright.async_api import async_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from config.settings import BASE_URL, SHOPEE_WEB_LOCALE, SHOPEE_COOKIE_DOMAIN
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
        timeout_ms = int(self._timeout_seconds * 1000)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(locale=SHOPEE_WEB_LOCALE)

            cookie_payload = [
                {
                    "name": name,
                    "value": value,
                    "domain": SHOPEE_COOKIE_DOMAIN,
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

            def capture_akamai_token_from_request(request) -> None:
                nonlocal captured_token
                if captured_token:
                    return
                if "/api/" not in request.url:
                    return
                token = request.headers.get("af-ac-enc-dat")
                # Shopee sometimes sends the literal string "null" before challenge is solved.
                if token and token.strip().lower() != "null":
                    captured_token = token
                    token_event.set()

            page.on("request", capture_akamai_token_from_request)

            try:
                try:
                    await page.goto(product_url, wait_until="domcontentloaded", timeout=timeout_ms)
                except (PlaywrightTimeoutError, PlaywrightError) as exc:
                    log.warning("Playwright failed opening product page: %s", exc)
                    return AkamaiTokenResult(token=None, cookies=cookies.copy())
                try:
                    await page.wait_for_load_state("networkidle", timeout=timeout_ms)
                except (PlaywrightTimeoutError, PlaywrightError) as exc:
                    log.debug("networkidle wait skipped: %s", exc)
                try:
                    await asyncio.wait_for(token_event.wait(), timeout=self._timeout_seconds)
                except asyncio.TimeoutError:
                    log.warning("Timeout while waiting af-ac-enc-dat from Playwright session")

                updated_cookies = {
                    c["name"]: c["value"]
                    for c in await context.cookies(BASE_URL)
                }

                if captured_token:
                    log.info("af-ac-enc-dat captured from browser traffic")
                else:
                    log.warning("af-ac-enc-dat not captured from browser traffic")

                return AkamaiTokenResult(token=captured_token, cookies=updated_cookies)
            finally:
                await context.close()
                await browser.close()
