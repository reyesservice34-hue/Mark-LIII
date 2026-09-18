"""
Offline verification of the Command Center's outward-facing services:
calendar, e-mail, GitHub and web search.

No network, no mailbox, no Google account, no GitHub token: every HTTP call is
stubbed and the calendar runs against a temporary local store.

Run:  python tests/test_command_center_tools.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "GOOGLE_CALENDAR_ID",
          "EMAIL_USER", "EMAIL_PASSWORD", "EMAIL_IMAP_HOST", "EMAIL_SMTP_HOST", "EMAIL_SENDER_NAME",
          "GITHUB_TOKEN", "GITHUB_DEFAULT_REPO"):
    os.environ.pop(k, None)

from command_center.backend.services import external as EXT  # noqa: E402
from command_center.backend.services.calendar_service import CalendarService  # noqa: E402
from command_center.backend.services.email_service import EmailService, MailError, load_account  # noqa: E402

fails: list[str] = []


def check(label: str, cond, detail="") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  :: {detail}"))
    if not cond:
        fails.append(label)


def run(coro):
    return asyncio.run(coro)


tmp = tempfile.mkdtemp(prefix="jarvis-cc-tools-")

print("\n1. calendar — the local backend works with no credentials at all")
cal = CalendarService(store_file=Path(tmp) / "events.json")
check("calendar core importable", cal.available(), cal.unavailable_reason())
check("backend is local without Google env", cal.backend_name() == "local")
health = run(cal.health())
check("health is healthy and says it is local", health["status"] == "healthy" and "local" in health["detail"],
      health)

tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
event, backend, note = run(cal.create(title="Baustelle Meier", when=tomorrow, at="14:00", duration=90,
                                      location="Musterstraße 3"))
check("appointment created locally", backend == "local" and event["title"] == "Baustelle Meier", event)
check("duration honoured", (datetime.fromisoformat(event["end"]) -
                            datetime.fromisoformat(event["start"])).total_seconds() == 90 * 60)
check("the answer says it is only local", "local" in note.lower() and "google" in note.lower(), note)
check(".ics written next to the store", event["file"] and Path(event["file"]).exists())
ics = Path(event["file"]).read_text(encoding="utf-8")
check("ics is a real VEVENT", "BEGIN:VEVENT" in ics and "SUMMARY:Baustelle Meier" in ics)

events, backend = run(cal.list(days=7))
check("listing finds it", any(e["title"] == "Baustelle Meier" for e in events), events)
check("listing reports the backend", backend == "local")

print("\n2. calendar — refuses what it cannot read, never guesses which appointment")
try:
    run(cal.create(title="X", when="irgendwann", at="14:00"))
    check("unreadable date refused", False)
except Exception as e:
    check("unreadable date refused with a spoken reason", "irgendwann" in str(e).lower()
          or "date" in str(e).lower(), str(e))
try:
    # A date it cannot read must be reported as such, not as a missing time.
    run(cal.create(title="X", when="irgendwann"))
    check("unreadable date beats the missing time in the message", False)
except Exception as e:
    check("unreadable date beats the missing time in the message", "time" not in str(e).lower(), str(e))
try:
    run(cal.create(title="X", when=tomorrow))
    check("missing time refused", False)
except Exception as e:
    check("missing time refused", "time" in str(e).lower(), str(e))

run(cal.create(title="Kunde Schmidt Termin", when=tomorrow, at="09:00"))
run(cal.create(title="Kunde Schmidt Nachtermin", when=tomorrow, at="16:00"))
try:
    run(cal.cancel("Kunde Schmidt"))
    check("ambiguous cancel refused", False)
except Exception as e:
    check("ambiguous cancel lists the candidates instead of guessing",
          "matches 2" in str(e) or "which one" in str(e).lower(), str(e))
try:
    run(cal.cancel("gibt es nicht"))
    check("unknown cancel refused", False)
except Exception as e:
    check("unknown appointment named plainly", "no appointment" in str(e).lower(), str(e))

moved, backend = run(cal.move("Baustelle Meier", tomorrow, "17:30"))
check("move keeps the duration and applies the new time",
      moved["start"].endswith("17:30:00") and
      (datetime.fromisoformat(moved["end"]) - datetime.fromisoformat(moved["start"])).total_seconds() == 90 * 60,
      moved)
cancelled, _ = run(cal.cancel("Baustelle Meier"))
check("cancel removes it", cancelled["title"] == "Baustelle Meier"
      and not any(e["title"] == "Baustelle Meier" for e in run(cal.list(days=7))[0]))

print("\n3. calendar — Google is used when configured, and nothing is written to disk for it")
os.environ.update({"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec", "GOOGLE_REFRESH_TOKEN": "ref"})
gcal = CalendarService(store_file=Path(tmp) / "events.json")
check("backend switches to google", gcal.backend_name() == "google")

sent: list[dict] = []


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
        self.content = b"{}" if payload is not None else b""

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None, **kw):
        sent.append({"method": "POST", "url": url, "data": data})
        return FakeResponse({"access_token": "tok", "expires_in": 3600})

    async def request(self, method, url, headers=None, **kw):
        sent.append({"method": method, "url": url, "json": kw.get("json"), "params": kw.get("params")})
        if method == "POST":
            body = kw.get("json") or {}
            return FakeResponse({"id": "gid1", "iCalUID": "gid1@google", "summary": body.get("summary", ""),
                                 "start": {"dateTime": body["start"]["dateTime"]},
                                 "end": {"dateTime": body["end"]["dateTime"]},
                                 "location": body.get("location", ""), "description": body.get("description", "")})
        return FakeResponse({"items": [
            {"id": "gid1", "iCalUID": "gid1@google", "summary": "Google Termin",
             "start": {"dateTime": "2026-09-24T10:00:00+02:00"}, "end": {"dateTime": "2026-09-24T11:00:00+02:00"},
             "location": "Büro", "description": ""}]})


EXT_HTTPX = None
import command_center.backend.services.calendar_service as CAL  # noqa: E402
real_httpx_client = CAL.httpx.AsyncClient
CAL.httpx.AsyncClient = FakeClient
try:
    events, backend = run(gcal.list(days=7))
    check("google listing parsed", backend == "google" and events[0]["title"] == "Google Termin", events)
    check("offset dropped to floating local time", events[0]["start"] == "2026-09-24T10:00:00", events[0])
    created, backend, note = run(gcal.create(title="Angebot besprechen", when=tomorrow, at="11:00"))
    check("google create used the API, not the local store", backend == "google" and note == "", note)
    check("no local note when it really is in Google", not note)
    posted = [s for s in sent if s["method"] == "POST" and "calendar/v3" in s["url"]]
    check("create sent an offset-aware start", posted and "+" in posted[0]["json"]["start"]["dateTime"]
          or "Z" in str(posted[0]["json"]["start"]["dateTime"]), posted[:1])
    store = json.loads((Path(tmp) / "events.json").read_text())
    check("google appointments are not duplicated into the local store",
          not any(e["title"] == "Angebot besprechen" for e in store), [e["title"] for e in store])
finally:
    CAL.httpx.AsyncClient = real_httpx_client
    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
        os.environ.pop(k, None)

print("\n4. e-mail — configuration, guessing and refusal without credentials")
check("no account without env", load_account() is None)
mail = EmailService()
check("service reports why it cannot run", not mail.configured() and "EMAIL_USER" in mail.unavailable_reason())
check("health says not_configured", run(mail.health())["status"] == "not_configured")
try:
    mail.build("a@b.de", "s", "b")
    check("build refuses without a mailbox", False)
except MailError as e:
    check("build refuses without a mailbox", "configured" in str(e).lower(), str(e))

os.environ.update({"EMAIL_USER": "chef@reyes-service.de", "EMAIL_PASSWORD": "pw"})
mail.reload()
acc = mail.account
check("account loaded", mail.configured() and acc.address == "chef@reyes-service.de")
check("servers guessed from the domain", acc.imap_host == "imap.reyes-service.de"
      and acc.smtp_host == "smtp.reyes-service.de", acc)
os.environ["EMAIL_USER"] = "chef@gmail.com"
mail.reload()
check("known provider uses its real servers", mail.account.imap_host == "imap.gmail.com"
      and mail.account.smtp_port == 465, mail.account)
os.environ["EMAIL_IMAP_HOST"] = "mail.example.com"
os.environ["EMAIL_SENDER_NAME"] = "Reyes Service"
mail.reload()
check("explicit host overrides the guess", mail.account.imap_host == "mail.example.com")
check("sender name used in the From header", "Reyes Service" in mail.account.sender())

print("\n5. e-mail — drafting validates, nothing is sent by building a message")
msg = mail.build("kunde@example.com, zweite@example.com", "Angebot", "Guten Tag,\n\nanbei das Angebot.")
check("recipients kept", msg["To"] == "kunde@example.com, zweite@example.com")
check("subject and body set", msg["Subject"] == "Angebot" and "anbei das Angebot" in msg.get_content())
check("From uses the configured sender", "Reyes Service" in msg["From"])
for bad in ("", "not-an-address", "a b@c.de"):
    try:
        mail.build(bad, "s", "b")
        check(f"invalid recipient refused: {bad!r}", False)
    except MailError:
        check(f"invalid recipient refused: {bad!r}", True)

print("\n6. e-mail — HTML bodies are read as text, not markup")
import email as email_mod  # noqa: E402
from command_center.backend.services.email_service import _body_of  # noqa: E402

html_mail = email_mod.message_from_string(
    "MIME-Version: 1.0\nContent-Type: text/html; charset=utf-8\n\n"
    "<html><head><style>p{color:red}</style></head><body><p>Hallo <b>Chef</b></p>"
    "<script>alert(1)</script></body></html>")
body = _body_of(html_mail)
check("tags removed", "<" not in body and "Hallo" in body and "Chef" in body, body)
check("script and style content dropped", "alert" not in body and "color:red" not in body, body)

multipart = email_mod.message_from_string(
    'MIME-Version: 1.0\nContent-Type: multipart/alternative; boundary="b"\n\n'
    "--b\nContent-Type: text/plain; charset=utf-8\n\nDer echte Text.\n"
    "--b\nContent-Type: text/html; charset=utf-8\n\n<p>ignoriert</p>\n--b--\n")
check("plain part preferred over html", _body_of(multipart).strip() == "Der echte Text.", _body_of(multipart))

print("\n7. GitHub — refuses cleanly without a token, parses with one")
gh = EXT.GitHubService()
check("unconfigured", not gh.configured() and gh.unavailable_reason() == "GITHUB_TOKEN not set")
check("health says not_configured", run(gh.health())["status"] == "not_configured")
try:
    run(gh.repo_overview("a/b"))
    check("call without token refused", False)
except EXT.ExternalError:
    check("call without token refused", True)

os.environ["GITHUB_TOKEN"] = "ghp_fake"
gh = EXT.GitHubService()
captured: list[str] = []


class FakeGH:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None, params=None):
        captured.append(url)
        import base64
        if url.endswith("/contents/README.md"):
            return FakeResponse({"path": "README.md", "size": 12, "encoding": "base64",
                                 "content": base64.b64encode(b"# Titel\nText").decode(),
                                 "html_url": "https://github.com/x/y/blob/main/README.md"})
        if url.endswith("/issues"):
            return FakeResponse([{"number": 7, "title": "Bug", "state": "open", "user": {"login": "someone"},
                                  "html_url": "u", "updated_at": "2026-09-01T00:00:00Z", "labels": [{"name": "bug"}]}])
        return FakeResponse({"full_name": "x/y", "description": "d", "default_branch": "main",
                             "open_issues_count": 2, "stargazers_count": 5, "html_url": "u",
                             "pushed_at": "2026-09-01T00:00:00Z", "language": "Python"})


real_gh_client = EXT.httpx.AsyncClient
EXT.httpx.AsyncClient = FakeGH
try:
    check("repo shorthand accepted", run(gh.repo_overview("x/y"))["full_name"] == "x/y")
    check("full URL accepted too", run(gh.repo_overview("https://github.com/x/y"))["full_name"] == "x/y")
    try:
        run(gh.repo_overview("just-a-name"))
        check("owner/repo required", False)
    except EXT.ExternalError as e:
        check("owner/repo required", "owner/repo" in str(e))
    f = run(gh.read_file("x/y", "README.md"))
    check("file content decoded from base64", f["content"].startswith("# Titel"), f)
    issues = run(gh.list_issues("x/y"))
    check("issues parsed", issues[0]["number"] == 7 and issues[0]["labels"] == ["bug"], issues)
finally:
    EXT.httpx.AsyncClient = real_gh_client
    os.environ.pop("GITHUB_TOKEN", None)

print("\n8. web search — results parsed, redirect wrappers unwrapped")
check("duckduckgo redirect unwrapped",
      EXT._real_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fseite&rut=x") == "https://example.com/seite")
check("direct url untouched", EXT._real_url("https://example.com/a") == "https://example.com/a")

SAMPLE = '''
<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fone">
Erstes <b>Ergebnis</b></a><a class="result__snippet" href="#">Ein Schnipsel &amp; Text</a></div>
<div class="result"><a class="result__a" href="https://example.org/two">Zweites</a>
<a class="result__snippet" href="#">Noch einer</a></div>
'''


class FakeSearch:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None):
        class R:
            status_code = 200
            text = SAMPLE
        return R()


real_search_client = EXT.httpx.AsyncClient
EXT.httpx.AsyncClient = FakeSearch
try:
    results = run(EXT.web_search("dämmung preise", limit=5))
    check("two results parsed", len(results) == 2, results)
    check("title html stripped and entities decoded", results[0]["title"] == "Erstes Ergebnis", results[0])
    check("snippet decoded", results[0]["snippet"] == "Ein Schnipsel & Text", results[0])
    check("wrapped url resolved", results[0]["url"] == "https://example.com/one", results[0])
    check("limit honoured", len(run(EXT.web_search("x", limit=1))) == 1)
    try:
        run(EXT.web_search("   "))
        check("empty query refused", False)
    except EXT.ExternalError:
        check("empty query refused", True)
finally:
    EXT.httpx.AsyncClient = real_search_client

print("\n9. voice — disabled and honest without a backend, real with one")
from command_center.backend.services import voice_service as VS  # noqa: E402

for k in ("JARVIS_CC_STT_URL", "JARVIS_CC_TTS_URL", "JARVIS_CC_STT_API_KEY", "JARVIS_CC_TTS_API_KEY",
          "OPENAI_API_KEY"):
    os.environ.pop(k, None)
voice = VS.VoiceService()
caps = voice.capabilities()
check("speech-to-text reported unavailable", not caps["speech_to_text"]["available"])
check("and names the variable to set", "JARVIS_CC_STT_URL" in caps["speech_to_text"]["detail"],
      caps["speech_to_text"]["detail"])
check("health says not_configured", run(voice.health())["status"] == "not_configured")
for coro, label in ((voice.transcribe(b"x", "audio/webm"), "transcribe"), (voice.speak("hallo"), "speak")):
    try:
        run(coro)
        check(f"{label} refuses without a backend", False)
    except VS.VoiceError as e:
        check(f"{label} refuses without a backend", "configured" in str(e).lower(), str(e))

check("endpoint from a bare host", VS._endpoint("http://w:8000", "/audio/speech") == "http://w:8000/v1/audio/speech")
check("endpoint from a /v1 root", VS._endpoint("http://w:8000/v1/", "/audio/speech") == "http://w:8000/v1/audio/speech")
check("a full endpoint url is left alone",
      VS._endpoint("http://w:8000/v1/audio/transcriptions", "/audio/transcriptions")
      == "http://w:8000/v1/audio/transcriptions")

os.environ.update({"JARVIS_CC_STT_URL": "http://whisper:8000", "JARVIS_CC_TTS_URL": "http://kokoro:8880",
                   "JARVIS_CC_STT_MODEL": "whisper-large", "JARVIS_CC_TTS_VOICE": "nova"})
voice = VS.VoiceService()
caps = voice.capabilities()
check("becomes available once configured", caps["speech_to_text"]["available"] and caps["text_to_speech"]["available"])
check("names the model and voice it will use",
      caps["speech_to_text"]["model"] == "whisper-large" and caps["text_to_speech"]["voice"] == "nova", caps)

posted: list[dict] = []


class FakeVoiceClient:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, files=None, data=None, json=None, headers=None):
        posted.append({"url": url, "data": data, "json": json, "file": files["file"][0] if files else None})

        class R:
            status_code = 200
            headers = {"content-type": "audio/mpeg"}
            content = b"ID3audio"
            text = ""

            @staticmethod
            def json():
                return {"text": "  Trag den Termin ein.  ", "language": "de"}
        return R()


real_voice_client = VS.httpx.AsyncClient
VS.httpx.AsyncClient = FakeVoiceClient
try:
    result = run(voice.transcribe(b"fake-audio-bytes", "audio/webm", "de"))
    check("transcript trimmed and returned", result["text"] == "Trag den Termin ein.", result)
    check("posted to the transcriptions endpoint",
          posted[0]["url"] == "http://whisper:8000/v1/audio/transcriptions", posted[0]["url"])
    check("model and language sent", posted[0]["data"]["model"] == "whisper-large"
          and posted[0]["data"]["language"] == "de", posted[0]["data"])
    check("file named by its real container", posted[0]["file"] == "speech.webm", posted[0]["file"])
    audio, ctype = run(voice.speak("Erledigt."))
    check("speech returns audio bytes", audio == b"ID3audio" and ctype == "audio/mpeg")
    check("speech posted the configured voice", posted[1]["json"]["voice"] == "nova", posted[1]["json"])
    for bad_type in ("text/plain", "application/octet-stream"):
        try:
            run(voice.transcribe(b"x", bad_type))
            check(f"non-audio upload refused: {bad_type}", False)
        except VS.VoiceError:
            check(f"non-audio upload refused: {bad_type}", True)
    try:
        run(voice.transcribe(b"", "audio/webm"))
        check("empty recording refused", False)
    except VS.VoiceError:
        check("empty recording refused", True)
    try:
        run(voice.transcribe(b"x" * (VS.MAX_AUDIO_BYTES + 1), "audio/webm"))
        check("oversized recording refused", False)
    except VS.VoiceError as e:
        check("oversized recording refused", "MB" in str(e), str(e))
finally:
    VS.httpx.AsyncClient = real_voice_client
    for k in ("JARVIS_CC_STT_URL", "JARVIS_CC_TTS_URL", "JARVIS_CC_STT_MODEL", "JARVIS_CC_TTS_VOICE"):
        os.environ.pop(k, None)

print("\n10. the tool registry exposes them with the right risk and availability")
# Back to a bare environment: the registry must report what is *not* configured.
for k in ("EMAIL_USER", "EMAIL_PASSWORD", "EMAIL_IMAP_HOST", "EMAIL_SENDER_NAME"):
    os.environ.pop(k, None)
os.environ["JARVIS_CC_DATA_DIR"] = tmp + "/state"
from command_center.backend.config import reset_settings  # noqa: E402
reset_settings()
from command_center.backend.app import build_state  # noqa: E402

state = build_state()
by_name = {t.name: t for t in state.tools.all()}
check("calendar tools available without any credentials",
      all(by_name[n].available and by_name[n].handler for n in
          ("calendar.read", "calendar.create", "calendar.move", "calendar.cancel")))
check("cancelling an appointment needs approval", by_name["calendar.cancel"].needs_approval())
check("booking one does not", not by_name["calendar.create"].needs_approval())
check("email tools present but unavailable with a reason",
      all(not by_name[n].available and "EMAIL_USER" in by_name[n].reason
          for n in ("email.search", "email.read", "email.draft", "email.send")))
check("sending mail is gated", by_name["email.send"].needs_approval() and by_name["email.send"].risk == "high")
check("web.search needs no credentials", by_name["web.search"].available and by_name["web.search"].handler)
check("github tools declared", all(n in by_name for n in ("github.read", "github.issues", "github.commits")))
check("research agent can search the web",
      any(t.name == "web.search" for t in state.tools.for_agent(state.agents.get("research").tools, "operator")))
check("calendar agent reaches the calendar tools",
      {t.name for t in state.tools.for_agent(state.agents.get("calendar").tools, "operator")}
      >= {"calendar.read", "calendar.create"})
email_agent = state.agents.public("email", state.tools)
check("email agent shows as degraded while the mailbox is missing",
      email_agent["health"] == "degraded" and "email.*" in email_agent["missing_tools"], email_agent["health"])
check("and says which capability is missing", "email" in email_agent["health_detail"], email_agent["health_detail"])
check("calendar agent is healthy because the local backend works",
      state.agents.public("calendar", state.tools)["health"] == "healthy")
state.db.close()

print("\n11. .env.example must survive Docker's env_file parsing")
# Docker hands `KEY=value   # comment` to the container *including* the comment,
# so an inline comment silently turns an empty setting into a garbage value —
# JARVIS_CC_ROOT_PATH would have broken every route. Guard it here.
env_example = ROOT / "command_center" / ".env.example"
parsed, offenders = {}, []
for raw in env_example.read_text(encoding="utf-8").splitlines():
    stripped = raw.strip()
    if not stripped or stripped.startswith("#"):
        continue
    key, sep, value = raw.partition("=")
    if not sep:
        offenders.append(f"no '=' in: {raw!r}")
        continue
    parsed[key] = value
    if "#" in value:
        offenders.append(f"{key} carries a comment in its value: {value!r}")
    if value != value.strip():
        offenders.append(f"{key} has surrounding whitespace: {value!r}")
check("every line parses as KEY=value", not offenders, offenders[:3])
check("the file actually documents the settings", len(parsed) > 40, len(parsed))
for required in ("JARVIS_CC_ADMIN_USER", "JARVIS_CC_ADMIN_PASSWORD", "JARVIS_CC_BIND",
                 "JARVIS_CC_PORT", "JARVIS_CC_ROOT_PATH"):
    check(f"{required} documented", required in parsed)
check("no dead WhatsApp credentials are still asked for",
      "WHATSAPP_TOKEN" not in parsed and "WHATSAPP_PHONE_ID" not in parsed, list(parsed)[:0])

print("\n12. no source file may be swallowed by .gitignore")
# A rule meant for runtime output ("logs/") matched every directory of that name
# at any depth, so command_center/{backend/modules,frontend/src/modules}/logs/
# never reached the repository and the Docker build died on a missing import.
# Guard every source tree against that whole class of mistake.
import subprocess  # noqa: E402

source_files: list[str] = []
for tree, suffixes in ((ROOT / "command_center" / "backend", (".py",)),
                       (ROOT / "command_center" / "frontend" / "src", (".ts", ".tsx", ".css")),
                       (ROOT / "core", (".py", ".txt")),
                       (ROOT / "actions", (".py",)),
                       (ROOT / "plugins", (".py",)),
                       (ROOT / "tests", (".py",))):
    for path in tree.rglob("*"):
        if path.is_file() and path.suffix in suffixes and "__pycache__" not in path.parts:
            source_files.append(str(path.relative_to(ROOT)))

check("source trees were found at all", len(source_files) > 100, len(source_files))
ignored = subprocess.run(["git", "check-ignore", "--stdin"], cwd=ROOT, input="\n".join(source_files),
                         capture_output=True, text=True).stdout.split()
check("no source file is gitignored", not ignored, ignored[:5])

tracked = set(subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
              .stdout.splitlines())
untracked = [f for f in source_files if f not in tracked]
check("every source file is tracked by git", not untracked, untracked[:5])

# Both halves of every backend module must exist, or the app cannot even import.
from command_center.backend.modules import DEFAULT_MODULES  # noqa: E402
missing_modules = [m for m in DEFAULT_MODULES
                   if not (ROOT / "command_center" / "backend" / "modules" / m / "__init__.py").exists()]
check("every module in DEFAULT_MODULES exists on disk", not missing_modules, missing_modules)

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
