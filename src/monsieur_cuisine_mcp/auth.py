import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from .config import (
    MC_AUTHENTICATION_SSO_DOMAIN,
    MC_AUTHENTICATION_TIMEOUT_SECONDS,
    MC_BROWSER_LOCALE,
    MC_BROWSER_PROFILE_DIR,
    MC_DOMAIN,
    MC_LOGIN_DIAGNOSTICS_PATH,
    MC_LOGIN_ENTRY_URL,
    MC_SESSION_COOKIE_PREFIXES,
    STATE_DIR,
    STORAGE_STATE_PATH,
)
from .logging_config import setup_logging

logger = logging.getLogger(__name__)

# Chromium reports navigator.webdriver = true by default, which is the single
# most obvious "this is automated" signal to the bot protection the Lidl SSO
# runs. This flag turns it off.
_ANTI_AUTOMATION_ARGS = ["--disable-blink-features=AutomationControlled"]

# Cap what the recorder keeps so a long manual session can't grow unbounded.
_MAX_RECORDED = 400


class NotLoggedInError(RuntimeError):
    """Raised when an authenticated call is made without a saved session."""

    def __init__(self) -> None:
        super().__init__(
            "No saved Monsieur Cuisine session found. Run `uv run "
            "monsieur-cuisine-mcp-login` to log in once, then retry."
        )


class LoginFailedError(RuntimeError):
    """Raised when the interactive login does not produce a session.

    Carries a user-facing explanation; the details live in the diagnostics file
    written alongside it.
    """


class _LoginRecorder:
    """Collects a timestamped trace of a manual login attempt.

    The previous failure was undiagnosable because the only evidence was an
    untimestamped navigation list - there was no way to tell page-load routing
    apart from a rejected submit. This records what happened and when, so a
    failed attempt answers the question instead of raising a new one.

    Deliberately records no request or response bodies: the login POST carries
    the user's password, and page bodies carry session tokens. Statuses, URLs
    and console output are enough to locate a failure.
    """

    def __init__(self) -> None:
        self._start = time.monotonic()
        self.navigations: list[str] = []
        self.console: list[str] = []
        self.errors: list[str] = []
        self.responses: list[str] = []
        # Screens the user actually saw, captured as the attempt runs rather
        # than after it fails: closing the window destroys the page, and the
        # on-screen message (a rejected credential vs. a bot challenge) is the
        # single most diagnostic thing there is.
        self.screens: list[str] = []
        self.last_snapshot = ""

    def _stamp(self) -> str:
        return f"{time.monotonic() - self._start:8.2f}s"

    def _add(self, bucket: list[str], line: str) -> None:
        if len(bucket) < _MAX_RECORDED:
            bucket.append(f"[{self._stamp()}] {line}")

    async def snapshot(self, page) -> None:
        """Record the page's visible text whenever it changes.

        Called while polling, so a navigation in flight simply skips a turn.
        The result is a timeline of the screens the user actually saw - which is
        where an error message ("wrong password", "verify it's you", a blocked
        page) shows up.
        """
        try:
            text = await page.evaluate(
                "() => document.body ? document.body.innerText.trim().slice(0, 800) : ''"
            )
        except PlaywrightError:
            return
        if text and text != self.last_snapshot:
            self.last_snapshot = text
            indented = "\n".join(f"    {line}" for line in text.splitlines() if line.strip())
            self._add(self.screens, f"--- screen ---\n{indented}")

    def attach(self, page) -> None:
        """Start recording a page (also used for popups the SSO may open)."""
        page.on(
            "framenavigated",
            lambda frame: self._add(self.navigations, frame.url)
            if frame is page.main_frame
            else None,
        )
        page.on(
            "console",
            lambda msg: self._add(self.console, f"{msg.type}: {msg.text}")
            if msg.type in ("error", "warning")
            else None,
        )
        page.on("pageerror", lambda exc: self._add(self.errors, str(exc)))
        page.on("response", self._on_response)

    def _on_response(self, response) -> None:
        url = response.url
        if MC_AUTHENTICATION_SSO_DOMAIN not in url and MC_DOMAIN not in url:
            return
        method = response.request.method
        # Successful GETs of assets are noise; keep submits and anything failing.
        if method == "GET" and response.status < 400:
            return
        self._add(self.responses, f"{method} {response.status} {url}")

    def write(self, *, outcome: str, browser: str, final_url: str) -> None:
        """Dump the trace next to the session state and return nothing.

        Written only when a login fails, so a successful run leaves no clutter.
        """
        sections = [
            "Monsieur Cuisine login diagnostics",
            f"when:     {datetime.now().isoformat(timespec='seconds')}",
            f"outcome:  {outcome}",
            f"browser:  {browser}",
            f"last URL: {final_url}",
            "",
            "This file records no passwords and no request/response bodies.",
            "",
            "== main-frame navigations (elapsed since the browser opened) ==",
            *(self.navigations or ["  (none)"]),
            "",
            "== requests to Lidl / Monsieur Cuisine (submits and failures) ==",
            *(self.responses or ["  (none)"]),
            "",
            "== page errors ==",
            *(self.errors or ["  (none)"]),
            "",
            "== console errors and warnings ==",
            *(self.console or ["  (none)"]),
            "",
            "== screens shown during the attempt, in order ==",
            *(self.screens or ["  (none captured)"]),
            "",
        ]
        try:
            MC_LOGIN_DIAGNOSTICS_PATH.write_text("\n".join(sections))
        except OSError as exc:
            logger.debug("Could not write login diagnostics: %s", exc)
            return
        logger.info("Login diagnostics written to %s", MC_LOGIN_DIAGNOSTICS_PATH)


async def _launch_login_browser(playwright, headless: bool):
    """Open the browser used for the manual login.

    Prefers the real Google Chrome over Playwright's bundled Chromium: it has a
    far more ordinary fingerprint, which matters because the Lidl SSO runs bot
    protection. Falls back to bundled Chromium when Chrome is not installed.

    Uses a persistent profile so the Lidl device id and bot-protection cookies
    carry over between attempts, and bypasses page CSP because the SSO page
    creates a Web Worker from a blob: URL that its own Content-Security-Policy
    does not allow (the policy omits `blob:`), so the browser blocks it.
    """
    MC_BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    options = {
        "user_data_dir": str(MC_BROWSER_PROFILE_DIR),
        "headless": headless,
        "args": _ANTI_AUTOMATION_ARGS,
        "bypass_csp": True,
        "locale": MC_BROWSER_LOCALE,
        "no_viewport": True,
    }
    try:
        context = await playwright.chromium.launch_persistent_context(
            channel="chrome", **options
        )
        logger.debug("Login browser: installed Google Chrome")
        return context, "chrome"
    except PlaywrightError as exc:
        logger.debug("Google Chrome unavailable (%s); using bundled Chromium", exc)
        context = await playwright.chromium.launch_persistent_context(**options)
        logger.debug("Login browser: bundled Chromium")
        return context, "chromium"


def _has_session_cookie(cookies: list[dict]) -> bool:
    """True once the browser holds a cookie that proves the MC session exists."""
    return any(
        cookie["domain"].lstrip(".").endswith(MC_DOMAIN)
        and cookie["name"].startswith(MC_SESSION_COOKIE_PREFIXES)
        for cookie in cookies
    )


async def _clear_stale_session(context) -> None:
    """Drop Monsieur Cuisine auth cookies left over in the persistent profile.

    The profile keeps the previous session, so without this the wait loop sees
    the old (possibly about-to-expire) cookies immediately, re-saves them and
    closes the window before the user can log in. Lidl SSO / bot-protection
    cookies are kept on purpose - see _launch_login_browser.
    """
    for cookie in await context.cookies():
        if cookie["domain"].lstrip(".").endswith(MC_DOMAIN) and cookie["name"].startswith(
            (*MC_SESSION_COOKIE_PREFIXES, "wordpress_sec_")
        ):
            await context.clear_cookies(name=cookie["name"], domain=cookie["domain"])
            logger.debug("Cleared stale session cookie %s", cookie["name"])


async def _wait_for_session(context, page, recorder: _LoginRecorder) -> None:
    """Wait until the manual login establishes a session, or fail clearly.

    Polls for the session cookies that load_session_cookies() actually needs,
    rather than matching the landing URL: a successful login that lands
    somewhere unexpected used to look exactly like a timeout. Each turn also
    records the screen currently on display, so the trace survives the window
    being closed.
    """
    deadline = time.monotonic() + MC_AUTHENTICATION_TIMEOUT_SECONDS
    announced_sso = False

    while True:
        if page.is_closed():
            raise LoginFailedError(
                "The login window was closed before the login finished. Run the "
                "command again and leave the window open until the Monsieur "
                "Cuisine page reloads."
            )
        try:
            if _has_session_cookie(await context.cookies()):
                return
            url = page.url
            await recorder.snapshot(page)
        except PlaywrightError as exc:
            raise LoginFailedError(
                "The login browser closed unexpectedly before the login finished."
            ) from exc

        if not announced_sso and MC_AUTHENTICATION_SSO_DOMAIN in url:
            announced_sso = True
            logger.info(
                "Now on %s - finish logging in in the browser window (including "
                "any CAPTCHA/2FA). Waiting for the redirect back to %s...",
                MC_AUTHENTICATION_SSO_DOMAIN,
                MC_DOMAIN,
            )

        if time.monotonic() > deadline:
            raise LoginFailedError(
                f"No session appeared within {MC_AUTHENTICATION_TIMEOUT_SECONDS // 60} "
                f"minutes"
                + (
                    " - the login page was reached, but the login never completed."
                    if announced_sso
                    else " - the Lidl login page was never reached."
                )
            )
        await asyncio.sleep(2)


def _final_url(page) -> str:
    """The page's last URL, or a marker if the window is already gone."""
    try:
        return page.url
    except PlaywrightError:
        return "(page closed)"


async def login_interactive(headless: bool = False) -> None:
    """Open a real browser window and wait for the user to log in manually.

    No credentials are handled by this code: the user completes the Lidl login
    (including any CAPTCHA/2FA) themselves in the opened window. Success is
    detected by polling for the Monsieur Cuisine session cookies, which is what
    the MCP server actually needs - URL matching is unreliable because the
    require-login page uses locale-specific slugs and the SSO round trip can
    land on several different URLs.

    Once the session exists it is persisted to STORAGE_STATE_PATH for reuse by
    the MCP server. If it does not, a diagnostics file is written to
    MC_LOGIN_DIAGNOSTICS_PATH describing what the browser did.
    """
    logger.debug("Session state directory: %s", STATE_DIR)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        context, browser_name = await _launch_login_browser(playwright, headless)
        await _clear_stale_session(context)
        recorder = _LoginRecorder()
        context.on("page", recorder.attach)
        page = context.pages[0] if context.pages else await context.new_page()
        recorder.attach(page)

        try:
            logger.debug("Navigating to %s", MC_LOGIN_ENTRY_URL)
            await page.goto(MC_LOGIN_ENTRY_URL, wait_until="domcontentloaded", timeout=45000)
            logger.info(
                "Log in manually in the browser window that opened (including any "
                "CAPTCHA/2FA). Waiting for the session..."
            )

            try:
                await _wait_for_session(context, page, recorder)
            except LoginFailedError as exc:
                recorder.write(
                    outcome=str(exc),
                    browser=browser_name,
                    final_url=_final_url(page),
                )
                raise

            cookies = await context.cookies()
            logger.debug(
                "Session contains %d cookies (count only, no values logged)", len(cookies)
            )
            await context.storage_state(path=str(STORAGE_STATE_PATH))
            logger.info("Logged in. Session saved to %s", STORAGE_STATE_PATH)
        finally:
            try:
                await context.close()
            except PlaywrightError:
                pass
            logger.debug("Browser closed")


def has_saved_session() -> bool:
    exists = STORAGE_STATE_PATH.exists()
    logger.debug("Checking saved session %s -> %s", STORAGE_STATE_PATH, exists)
    return exists


def load_session_cookies() -> dict[str, str]:
    """Load the Monsieur Cuisine session cookies saved by login_interactive.

    Only cookies scoped to MC_DOMAIN are returned, since those are what the
    site's own `/proxy-api` endpoint (used for authenticated calls like
    favorite recipes) checks - the accounts.lidl.com/SSO cookies saved
    alongside them are not needed for it.

    Raises NotLoggedInError if no session has been saved yet.
    """
    if not has_saved_session():
        raise NotLoggedInError()

    state = json.loads(STORAGE_STATE_PATH.read_text())
    cookies = {
        cookie["name"]: cookie["value"]
        for cookie in state.get("cookies", [])
        if cookie["domain"].lstrip(".").endswith(MC_DOMAIN)
    }
    logger.debug("Loaded %d session cookies scoped to %s", len(cookies), MC_DOMAIN)
    return cookies


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive login to Monsieur Cuisine")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose event logging (DEBUG level)",
    )
    args = parser.parse_args()
    setup_logging(logging.DEBUG if args.debug else None)
    try:
        asyncio.run(login_interactive())
    except LoginFailedError as exc:
        # A failed manual login is an expected outcome, not a crash - report it
        # as a message rather than a traceback.
        logger.error("Login failed: %s", exc)
        logger.error("Details: %s", MC_LOGIN_DIAGNOSTICS_PATH)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.error("Login cancelled.")
        sys.exit(1)


if __name__ == "__main__":
    main()
