"""The "Glass" control center (/desktop-glass) sits NEXT TO /desktop and must not replace it."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"


def _ids_used(js: str) -> set[str]:
    return set(re.findall(r"getElementById\('([\w-]+)'\)", js)) | set(re.findall(r"\$\('([\w-]+)'\)", js))


def test_glass_page_served_and_desktop_untouched(client):
    g = client.get("/desktop-glass")
    assert g.status_code == 200 and "mia-glass.js" in g.text and "__ASSISTANT__" not in g.text
    d = client.get("/desktop")
    assert d.status_code == 200 and "mia-desktop.js" in d.text and "mia-glass" not in d.text
    for name in ("mia-glass.css", "mia-glass.js"):
        assert client.get(f"/static/{name}").status_code == 200


def test_glass_page_has_every_id_the_scripts_look_up():
    html = (STATIC / "desktop-glass.html").read_text(encoding="utf-8")
    have = set(re.findall(r'id="([\w-]+)"', html))
    # ids that shared.js / mia-glass.js create themselves at runtime or only read defensively
    runtime = {"mvp-shell", "mvp-item", "mvp-files", "voice-setup"}
    need = (_ids_used((STATIC / "shared.js").read_text(encoding="utf-8"))
            | _ids_used((STATIC / "mia-glass.js").read_text(encoding="utf-8"))) - runtime
    assert not (need - have), f"missing ids: {sorted(need - have)}"


def test_glass_files_contain_no_orange():
    """Orange belongs to the brain canvas only (mia-core.js)."""
    orange = re.compile(r"#(?:ff[5-9a][0-9a-f]{3}|f[5-9a][0-9a-f]{4})\b|rgba?\(\s*2[0-5]\d\s*,\s*(?:6|7|8|9|1[0-6])\d\s*,\s*\d{1,2}\b", re.I)
    for name in ("mia-glass.css", "mia-glass.js", "desktop-glass.html"):
        assert not orange.search((STATIC / name).read_text(encoding="utf-8")), name
