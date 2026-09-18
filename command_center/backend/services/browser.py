"""
Ein Browser auf dem Server — für das, was mit einem reinen Abruf nicht geht.

`web.fetch` holt HTML. Das reicht für die meisten Seiten und ist billig. Es
reicht NICHT, wenn eine Seite ihren Inhalt erst per JavaScript aufbaut, wenn
man sich anmelden muss, wenn ein Formular abzuschicken ist oder wenn man
sehen will, was dort tatsächlich steht. Dafür gibt es hier einen echten
Browser, den er steuern kann.

Zwei Wege, und der Unterschied ist wichtig:

* **Auf dem PC des Nutzers** — über die Desktop-App und `desktop.run` mit der
  Aktion `browser_control`. Das ist der Browser des Nutzers, mit seinen
  Anmeldungen. Dafür muss der Rechner laufen.
* **Hier auf dem Server** — dieses Modul. Läuft ohne den PC, hat aber keine
  Anmeldungen und kein Profil. Muss im Image eingeschaltet sein:
  `--build-arg JARVIS_CC_BROWSER=true`.

Fehlt Playwright oder der Browser im Image, sagen die Werkzeuge das mit dem
Befehl, der es nachrüstet. Ein Werkzeug, das sich verfügbar meldet und dann
scheitert, ist schlimmer als eines, das ehrlich fehlt.

Grenzen, bewusst gesetzt:
* Adressen im eigenen Netz sind gesperrt (dieselbe Prüfung wie beim Klonen) —
  ein Browser auf dem Server wäre sonst der bequemste Weg an die Nachbarn im
  Docker-Netz.
* Klicken, Tippen und Abschicken brauchen eine Genehmigung. Lesen nicht.
* Eine Sitzung, nicht beliebig viele: ein Browser, ein Tab, damit niemand
  versehentlich zwanzig Chromium-Prozesse auf einem kleinen Server startet.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from .repos import RepoError, check_url

NAV_TIMEOUT = 30_000
MAX_TEXT = 12_000


class BrowserError(RuntimeError):
    """Grund, der dem Modell und dem Nutzer gesagt wird."""


def available() -> tuple[bool, str]:
    """(geht es, warum nicht) — beides, damit niemand raten muss."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False, ("Playwright ist im Server-Image nicht installiert. Bauen mit: "
                       "--build-arg JARVIS_CC_BROWSER=true")
    return True, ""


class BrowserSession:
    """Ein Browser, ein Tab, so lange er gebraucht wird.

    Absichtlich träge: gestartet wird erst beim ersten Aufruf, nicht beim
    Start des Servers. Ein Chromium, der nur wartet, kostet Arbeitsspeicher,
    den ein kleiner Server nicht übrig hat.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self._pw = None
        self._browser = None
        self._page = None
        self._lock = asyncio.Lock()

    async def page(self):
        ok, why = available()
        if not ok:
            raise BrowserError(why)
        if self._page is not None and not self._page.is_closed():
            return self._page
        from playwright.async_api import async_playwright
        try:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await self._browser.new_context(
                viewport={"width": 1400, "height": 900},
                user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"))
            self._page = await context.new_page()
            self._page.set_default_timeout(NAV_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            await self.close()
            raise BrowserError(
                f"Der Browser startet nicht: {e.__class__.__name__}. Fehlt der Chromium im Image? "
                "Bauen mit --build-arg JARVIS_CC_BROWSER=true") from e
        return self._page

    async def close(self) -> None:
        for obj, name in ((self._page, "page"), (self._browser, "browser"), (self._pw, "pw")):
            try:
                if obj is None:
                    continue
                await (obj.stop() if name == "pw" else obj.close())
            except Exception:  # noqa: BLE001
                pass
        self._page = self._browser = self._pw = None

    # ── Handlungen ───────────────────────────────────────────────────────
    async def goto(self, url: str) -> dict:
        try:
            url = check_url(url)
        except RepoError as e:
            raise BrowserError(str(e)) from e
        async with self._lock:
            page = await self.page()
            await page.goto(url, wait_until="domcontentloaded")
            return await self._snapshot(page)

    async def read(self) -> dict:
        async with self._lock:
            page = await self.page()
            return await self._snapshot(page)

    async def click(self, what: str) -> dict:
        async with self._lock:
            page = await self.page()
            target = page.get_by_text(what, exact=False).first
            try:
                await target.click(timeout=8000)
            except Exception:  # noqa: BLE001
                try:
                    await page.click(what, timeout=8000)      # dann eben als CSS-Auswahl
                except Exception as e:  # noqa: BLE001
                    raise BrowserError(f"Nichts gefunden, worauf '{what}' passt.") from e
            await page.wait_for_load_state("domcontentloaded")
            return await self._snapshot(page)

    async def type(self, field: str, text: str, submit: bool = False) -> dict:
        async with self._lock:
            page = await self.page()
            try:
                box = page.get_by_label(field, exact=False).first
                await box.fill(text, timeout=5000)
            except Exception:  # noqa: BLE001
                try:
                    await page.fill(field, text, timeout=5000)
                except Exception as e:  # noqa: BLE001
                    raise BrowserError(f"Kein Eingabefeld gefunden, auf das '{field}' passt.") from e
            if submit:
                await page.keyboard.press("Enter")
                await page.wait_for_load_state("domcontentloaded")
            return await self._snapshot(page)

    async def screenshot(self, name: str = "browser.png") -> dict:
        safe = "".join(c for c in name if c.isalnum() or c in "._-")[:60] or "browser.png"
        if not safe.endswith(".png"):
            safe += ".png"
        target = self.workspace / "downloads" / safe
        target.parent.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            page = await self.page()
            await page.screenshot(path=str(target), full_page=False)
        return {"path": f"downloads/{safe}", "url": page.url,
                "hinweis": "Im Dashboard unter Dateien ansehbar."}

    @staticmethod
    async def _snapshot(page) -> dict:
        """Was auf der Seite steht — als Text, nicht als HTML-Wüste."""
        try:
            text = await page.inner_text("body")
        except Exception:  # noqa: BLE001
            text = ""
        links = []
        try:
            for a in (await page.locator("a[href]").all())[:40]:
                label = (await a.inner_text() or "").strip()[:80]
                href = await a.get_attribute("href") or ""
                if label and href:
                    links.append({"text": label, "href": href})
        except Exception:  # noqa: BLE001
            pass
        return {"url": page.url, "title": await page.title(),
                "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT, "links": links}


__all__ = ["BrowserSession", "BrowserError", "available"]
