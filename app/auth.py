"""HTTP Basic Auth gate for the hosted deployment.

Local dev (running against 127.0.0.1) has never needed this -- the whole point of
this file is the moment the app leaves this Mac. Two people, one shared login,
checked against environment variables set on the host. No user database, no
external service: the credentials never exist anywhere but the host's own env.

Applied as a single dependency on the FastAPI app itself (see main.py), not on
each route individually -- FastAPI's app-level `dependencies=` covers every
`@app.get`/`@app.post` route but does NOT reach into `app.mount("/static", ...)`,
which is a separate ASGI app. That is exactly the split CLAUDE.md's data-handling
rule implies: static assets (CSS/JS/fonts, no applicant data) stay reachable
without a login; every templated route and the upload endpoint do not.
"""
import base64
import os
import secrets

from fastapi import HTTPException, Request

USER_ENV = "AADA_USER"
PASS_ENV = "AADA_PASS"
REQUIRE_ENV = "AADA_REQUIRE_AUTH"


def _configured():
    return os.environ.get(USER_ENV) is not None or os.environ.get(PASS_ENV) is not None


def enforced():
    """Auth turns on automatically the moment either credential var is set, so a
    host can't go live half-configured. Local dev with neither var set is
    unaffected -- this is what keeps `serve.py` working exactly as it always has.
    """
    return os.environ.get(REQUIRE_ENV) == "1" or _configured()


def check_config():
    """Called once at import time. Refuses to boot on a half-configured
    credential pair rather than silently falling back to no auth -- a typo in
    one of the two env var names should be loud, not a silent security hole."""
    if not enforced():
        return
    user, pw = os.environ.get(USER_ENV), os.environ.get(PASS_ENV)
    if not user or not pw:
        raise RuntimeError(
            "Auth is enabled (%s set) but %s/%s are missing or empty. Set both "
            "on the host, or unset %s for local development."
            % (REQUIRE_ENV if os.environ.get(REQUIRE_ENV) == "1" else "a credential",
               USER_ENV, PASS_ENV, REQUIRE_ENV)
        )


_CHALLENGE = HTTPException(
    status_code=401, detail="Authentication required.",
    headers={"WWW-Authenticate": 'Basic realm="AADA Funnel"'})


async def require_login(request: Request):
    """FastAPI dependency -- see main.py's `FastAPI(dependencies=[...])`."""
    if not enforced():
        return

    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        raise _CHALLENGE

    try:
        given_user, _, given_pass = (
            base64.b64decode(header[6:]).decode("utf-8", "replace").partition(":")
        )
    except Exception:
        raise _CHALLENGE

    want_user = os.environ.get(USER_ENV, "")
    want_pass = os.environ.get(PASS_ENV, "")
    ok = (secrets.compare_digest(given_user, want_user)
          and secrets.compare_digest(given_pass, want_pass))
    if not ok:
        raise _CHALLENGE
