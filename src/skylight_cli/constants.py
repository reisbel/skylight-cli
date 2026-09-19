"""Endpoints and OAuth parameters for Skylight's private API.

None of this is documented or supported by Skylight. The values below come from
community reverse-engineering of the official apps and can stop working whenever
Skylight ships an update. See the README for the full caveat.
"""

from __future__ import annotations

BASE_URL = "https://app.ourskylight.com"
API_PREFIX = "/api"
DEFAULT_TIMEOUT = 30.0

#: Sent on API calls so Skylight can identify this client in their logs.
USER_AGENT = "skylight-cli (+https://github.com/reisbel/skylight-cli)"

#: The OAuth login pages are served to browsers and behave badly without a
#: browser user agent, so the login flow uses this one instead.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

OAUTH_CLIENT_ID = "skylight-mobile"
OAUTH_SCOPE = "everything"
OAUTH_REDIRECT_URI = "skylight-family://welcome"
OAUTH_CODE_CHALLENGE_METHOD = "S256"
