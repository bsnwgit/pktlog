"""
Resonance embed integration — the shared assistant surface for pkt* apps.

This package is vendored: it is byte-identical across pktlog, pktflow, pktIPAM
and pktWiFi except for APP_SLUG below. It is deliberately not a published
package — install.sh builds a venv from requirements.txt on customer hosts, and
a private index would put a credentialed network dependency in the middle of
every install. Copy the directory; bump the version when the contract changes
so drift between apps is visible.

How the pieces fit:

  browser                pkt app                     resonance
  ───────                ───────                     ─────────
  embed.js  ──GET──▶  /api/resonance/code  ──POST──▶  /embed/session
            ◀─code──                       ◀─code───
  frame ────────────────────────────────────────────▶  /embed?c=<code>

The app's own secret never reaches the browser, and resonance never sees the
app's user credentials — the app vouches for its user and gets a short-lived,
single-use code back.
"""
from __future__ import annotations

# Bump when the wire contract or endpoint shape changes, so an app running an
# older copy is identifiable from its Settings page rather than by inspection.
RESONANCE_MODULE_VERSION = "1.1.0"   # 1.1.0: session payload carries user.name

# The only line that differs between pkt* apps. Prefixed onto the user id sent
# to resonance ("pktlog-alice"), so its logs show both who and which app. A
# constant rather than a setting on purpose: an admin must not be able to make
# their install report itself as a different app.
APP_SLUG = "pktlog"

__all__ = ["RESONANCE_MODULE_VERSION", "APP_SLUG"]
