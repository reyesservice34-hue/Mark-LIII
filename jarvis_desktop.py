"""
JARVIS — die Desktop-App.

    python jarvis_desktop.py        (oder JARVIS.bat doppelklicken)

Ein Fenster, das die offene Leitung zum Server hält: reden, Antwort hören,
dazwischenreden. Gleichzeitig meldet sie den Rechner beim Server an, damit er
auf Zuruf Programme öffnen und den PC bedienen kann — beides in einer App,
weil beides zusammengehört.

Was sie NICHT ist: die alte main.py. Die hält eine eigene Gemini-Sitzung und
braucht dafür einen eigenen Schlüssel. Diese hier hat kein eigenes Gehirn:
sie ist das Gesicht des Servers auf diesem Rechner. Ein Satz hier und ein
Satz im Dashboard landen in derselben Unterhaltung, mit denselben Werkzeugen,
demselben Gedächtnis und denselben Freigaben.

Eingerichtet wird sie mit `python install_desktop.py`. Gebraucht wird nur das
Maschinen-Token.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal
    from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen
    from PyQt6.QtWidgets import (
        QApplication, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
        QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget,
    )
except ImportError:  # pragma: no cover - ohne Qt gibt es kein Fenster
    print("\n  PyQt6 fehlt. Nachinstallieren mit:  python setup.py\n")
    raise SystemExit(1)

from core.live_client import LiveClient, load_settings

# Dieselben Farben wie das Dashboard — es ist dasselbe JARVIS, also sieht es
# auch gleich aus.
BG_0, BG_1, BG_2 = "#06080c", "#0a0e14", "#0f141c"
LINE = "rgba(146,172,204,0.18)"
TEXT, TEXT_2, TEXT_3 = "#e8eef6", "#a4b3c6", "#6b7b91"
ACCENT, OK, WARN, ERR = "#5ac8ff", "#3ddc97", "#ffb454", "#ff5d6c"

PHASE = {
    "closed": ("BEREIT", TEXT_3),
    "connecting": ("VERBINDE", WARN),
    "listening": ("ICH HÖRE", ACCENT),
    "thinking": ("DENKE NACH", WARN),
    "speaking": ("ANTWORTE", OK),
}


class Core(QWidget):
    """Der Kern. Zeigt durch Bewegung und Farbe, was gerade passiert —
    ohne das weiß niemand, ob er reden soll oder warten muss."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(190, 190)
        self.phase = "closed"
        self._angle = 0.0
        self._pulse = 0.0
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(33)

    def set_phase(self, phase: str) -> None:
        self.phase = phase
        self.update()

    def _tick(self) -> None:
        speed = {"closed": 0.3, "connecting": 2.2, "listening": 1.2,
                 "thinking": 2.6, "speaking": 0.8}.get(self.phase, 0.4)
        self._angle = (self._angle + speed) % 360
        self._pulse = (self._pulse + (0.06 if self.phase in ("listening", "speaking") else 0.015)) % 6.283
        self.update()

    def paintEvent(self, _ev) -> None:  # noqa: N802
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        cx = cy = w / 2
        colour = {"thinking": WARN, "connecting": WARN, "speaking": OK}.get(self.phase, ACCENT)
        c = QColor(colour)

        for i, (radius, alpha, dashed) in enumerate(((w / 2 - 6, 80, True),
                                                     (w / 2 - 28, 120, False),
                                                     (w / 2 - 52, 170, False))):
            pen = QPen(QColor(c.red(), c.green(), c.blue(), alpha))
            pen.setWidthF(1.4)
            if dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.save()
            p.translate(cx, cy)
            p.rotate(self._angle * (1 if i % 2 == 0 else -0.7) * (1 + i * 0.3))
            p.drawEllipse(int(-radius), int(-radius), int(radius * 2), int(radius * 2))
            p.restore()

        glow = 1.0 + 0.22 * math.sin(self._pulse)
        r = 17 * (glow if self.phase in ("listening", "speaking") else 1.0)
        p.setPen(Qt.PenStyle.NoPen)
        for ring, a in ((r * 2.6, 26), (r * 1.7, 55)):
            p.setBrush(QColor(c.red(), c.green(), c.blue(), a))
            p.drawEllipse(int(cx - ring), int(cy - ring), int(ring * 2), int(ring * 2))
        p.setBrush(c)
        p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))


class Window(QMainWindow):
    # Die Rückrufe des Clients laufen in seinem eigenen Thread. Über Signale
    # landen sie im Qt-Thread — direkt aufgerufen stürzt ein Fenster früher
    # oder später ab, und niemand findet den Grund.
    sig_state = pyqtSignal(str)
    sig_heard = pyqtSignal(str)
    sig_said = pyqtSignal(str, bool)
    sig_tool = pyqtSignal(str, bool)
    sig_error = pyqtSignal(str)
    sig_ready = pyqtSignal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("JARVIS")
        self.resize(760, 560)
        self.client: LiveClient | None = None
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.runner = None
        self._said_block = ""

        self.setStyleSheet(f"""
            QMainWindow {{ background: {BG_0}; }}
            QWidget {{ color: {TEXT}; font-size: 14px; }}
            QLabel#phase {{ color: {TEXT_3}; font-size: 11px; letter-spacing: 3px; }}
            QLabel#meta {{ color: {TEXT_3}; font-size: 11px; }}
            QTextEdit {{ background: {BG_1}; border: 1px solid {LINE}; border-radius: 10px;
                         padding: 12px; font-size: 14px; }}
            QLineEdit {{ background: {BG_2}; border: 1px solid {LINE}; border-radius: 8px;
                         padding: 9px 12px; color: {TEXT}; }}
            QPushButton {{ background: {ACCENT}; color: #04202e; border: 0; border-radius: 8px;
                           padding: 11px 22px; font-weight: 600; }}
            QPushButton:hover {{ background: #7ad4ff; }}
            QPushButton#stop {{ background: {ERR}; color: #2a0a0e; }}
            QPushButton:disabled {{ background: {BG_2}; color: {TEXT_3}; }}
        """)

        self.core = Core()
        self.phase_label = QLabel("BEREIT"); self.phase_label.setObjectName("phase")
        self.meta = QLabel(""); self.meta.setObjectName("meta")
        self.transcript = QTextEdit(); self.transcript.setReadOnly(True)
        self.input = QLineEdit(); self.input.setPlaceholderText("… oder tippen und Enter")
        self.input.returnPressed.connect(self._typed)
        self.button = QPushButton("Leitung öffnen")
        self.button.clicked.connect(self._toggle)

        left = QVBoxLayout()
        left.addWidget(self.core, alignment=Qt.AlignmentFlag.AlignHCenter)
        left.addSpacing(10)
        left.addWidget(self.phase_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        left.addWidget(self.meta, alignment=Qt.AlignmentFlag.AlignHCenter)
        left.addStretch(1)
        left.addWidget(self.button)

        right = QVBoxLayout()
        right.addWidget(self.transcript, 1)
        right.addWidget(self.input)

        row = QHBoxLayout()
        row.addLayout(left)
        row.addSpacing(22)
        row.addLayout(right, 1)
        box = QWidget(); box.setLayout(row)
        box.setContentsMargins(22, 22, 22, 22)
        self.setCentralWidget(box)

        self.sig_state.connect(self._on_state)
        self.sig_heard.connect(lambda t: self._append("Du", t, TEXT_2))
        self.sig_said.connect(self._on_said)
        self.sig_tool.connect(lambda n, ok: self._append("", f"[{n}: {'ok' if ok else 'fehlgeschlagen'}]",
                                                         OK if ok else ERR))
        self.sig_error.connect(lambda t: self._append("", t, ERR))
        self.sig_ready.connect(self._on_ready)

        url, token = load_settings()
        if not url or not token:
            self._append("", "Es fehlt die Verbindung zum Server. Erst einrichten: "
                             "INSTALL-JARVIS.bat oder python install_desktop.py", WARN)
            self.button.setEnabled(False)
        else:
            self.meta.setText(url.replace("https://", ""))
            self._append("", "Bereit. Leitung öffnen und einfach losreden — du kannst ihm "
                             "jederzeit ins Wort fallen.", TEXT_3)
            self._start_runner()

    # ── Fernsteuerung im Hintergrund ─────────────────────────────────────
    def _start_runner(self) -> None:
        """Den Rechner beim Server anmelden, damit er ihn bedienen kann.

        Läuft still im Hintergrund: schlägt es fehl, ist das kein Grund, das
        Sprechen zu verhindern — die beiden sind unabhängig.
        """
        try:
            from core.desktop_runner import DesktopRunner
            runner = DesktopRunner()
            if not runner.configured():
                return
            self.runner = runner
            threading.Thread(target=runner.run_forever, daemon=True).start()
            self._append("", f"Dieser Rechner ist angemeldet — {len(runner.declarations())} "
                             f"Fähigkeiten stehen dem Server zur Verfügung.", TEXT_3)
        except Exception as e:  # noqa: BLE001
            self._append("", f"Fernsteuerung nicht gestartet: {e}", WARN)

    # ── Leitung ──────────────────────────────────────────────────────────
    def _toggle(self) -> None:
        if self.client is None:
            self._open()
        else:
            self._close()

    def _open(self) -> None:
        url, token = load_settings()
        self.client = LiveClient(
            url, token,
            on_state=self.sig_state.emit,
            on_ready=self.sig_ready.emit,
            on_heard=self.sig_heard.emit,
            on_said=self.sig_said.emit,
            on_tool=self.sig_tool.emit,
            on_error=self.sig_error.emit)
        self.button.setText("Leitung schließen")
        self.button.setObjectName("stop")
        self.button.setStyleSheet(self.button.styleSheet())

        def work() -> None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            reason = self.loop.run_until_complete(self.client.run())
            if reason:
                self.sig_error.emit(reason)
            self.sig_state.emit("closed")

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def _close(self) -> None:
        if self.client and self.loop:
            self.loop.call_soon_threadsafe(self.client.stop)
        self.client = None
        self.button.setText("Leitung öffnen")
        self.button.setObjectName("")
        self.button.setStyleSheet(self.button.styleSheet())

    def _typed(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._append("Du", text, TEXT_2)
        if self.client and self.loop:
            self.loop.call_soon_threadsafe(self.client.say, text)
        else:
            self._append("", "Die Leitung ist zu — erst öffnen.", WARN)

    # ── Anzeige ──────────────────────────────────────────────────────────
    def _on_state(self, phase: str) -> None:
        label, colour = PHASE.get(phase, ("…", TEXT_3))
        self.phase_label.setText(label)
        self.phase_label.setStyleSheet(f"color: {colour}; font-size: 11px; letter-spacing: 3px;")
        self.core.set_phase(phase)
        if phase == "closed" and self.client is not None:
            self._close()

    def _on_ready(self, ev: dict) -> None:
        self.meta.setText(f"{ev.get('voice', '')} · {ev.get('tools', 0)} Werkzeuge")

    def _on_said(self, text: str, done: bool) -> None:
        # Während er spricht, wächst derselbe Absatz, statt dass jede Silbe
        # eine neue Zeile bekommt.
        if self._said_block:
            cursor = self.transcript.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            for _ in range(len(self._said_block.splitlines()) or 1):
                cursor.select(cursor.SelectionType.LineUnderCursor)
                cursor.removeSelectedText()
                cursor.deletePreviousChar()
        self._said_block = text if not done else ""
        self._append("JARVIS", text, TEXT)

    def _append(self, who: str, text: str, colour: str) -> None:
        if not text:
            return
        prefix = f"<b style='color:{TEXT_3}'>{who}</b> " if who else ""
        self.transcript.append(f"<div style='color:{colour}; margin:4px 0'>{prefix}{text}</div>")
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def closeEvent(self, ev) -> None:  # noqa: N802
        self._close()
        if self.runner is not None:
            try:
                self.runner.stop()
            except Exception:  # noqa: BLE001
                pass
        ev.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("JARVIS")
    icon = Path(__file__).resolve().parent / "config" / "jarvis.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    app.setFont(QFont("Segoe UI" if sys.platform == "win32" else "Inter", 10))
    win = Window()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
