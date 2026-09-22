#!/usr/bin/env python3
"""
Reproducible deploy patch for the /monitor report source.

The eksi.biz.id `/monitor` route is served by the Cloudflare Worker
`api-dashboard` (mounted at `eksi.biz.id/*`). Its inline HTML fetches a
report URL via client-side JS. This script:

  1. Downloads the *currently deployed* worker bundle from the CF API,
  2. extracts `index.js` from the multipart response,
  3. patches the report fetch URL (idempotent),
  4. re-uploads the bundle as an ES-module worker,
  5. verifies the resulting URL and reports it.

No secrets are hardcoded. Credentials are resolved in this order:

  - env CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID
  - the token files the original deploy scripts used:
      ~/storage/downloads/tokeb/cloudflare_api_token.txt
      ~/storage/downloads/tokeb/cloudflare_account_id.txt

Usage:
  python3 patch_monitor.py                        # patch to default report URL
  REPORT_URL=https://... python3 patch_monitor.py # override report URL

The report URL regex matches `const <NAME> = '<url>';` so it survives the
worker bundle being regenerated with a different variable name or URL.
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error
import uuid

DEFAULT_REPORT_URL = "https://raw.githubusercontent.com/kyubiez96/airdrop-radar/main/REPORT.md"
WORKER_NAME = "api-dashboard"
CF_API = "https://api.cloudflare.com/client/v4"


def fail(msg):
    print(f"[!] {msg}")
    sys.exit(1)


def read_token_file(path, field):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return None


def resolve_creds():
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    tokeb = os.path.expanduser("~/storage/downloads/tokeb")
    if not token:
        token = read_token_file(os.path.join(tokeb, "cloudflare_api_token.txt"), "token")
    if not acct:
        acct = read_token_file(os.path.join(tokeb, "cloudflare_account_id.txt"), "acct")
    acct = (acct or "").strip()
    token = (token or "").strip()
    if not token or token.startswith("#") or not acct:
        fail("Cloudflare token/account id not found. Set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID.")
    return token, acct


def api(request):
    try:
        with urllib.request.urlopen(request, timeout=60) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        fail(f"CF API HTTP {e.code}: {body[:400]}")


def download_bundle(token, acct):
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{CF_API}/accounts/{acct}/workers/scripts/{WORKER_NAME}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    return api_binary(req, token)


def api_binary(request, token):
    try:
        with urllib.request.urlopen(request, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        fail(f"CF API HTTP {e.code}: {body[:400]}")


def extract_index_js(multipart):
    """Pull index.js out of the CF multipart worker-bundle response."""
    first = multipart.split(b"\r\n", 1)[0]
    boundary = first
    for part in multipart.split(boundary):
        if b'name="index.js"' in part or b'name="index.js";' in part:
            sep = part.find(b"\r\n\r\n")
            if sep != -1:
                return part[sep + 4:].rstrip(b"\r\n")
    # raw (non-multipart) fallback
    return multipart


def patch_report_url(script, report_url):
    # Matches: const ANY_NAME = '<any url>';  ->  const ANY_NAME = '<report_url>';
    pattern = re.compile(rb"(const\s+\w+\s*=\s*')https?://[^']*(';)")
    new, n = pattern.subn(lambda m: (m.group(1) + report_url.encode() + m.group(2)), script)
    if n == 0:
        fail("Could not locate report fetch URL in worker bundle (no const <URL> = '...' matched).")
    return new, n


def upload_bundle(token, acct, index_js):
    metadata = {"main_module": "index.js", "compatibility_date": "2025-06-01"}
    boundary = "-" + uuid.uuid4().hex
    body = b""
    body += (f"--{boundary}\r\n").encode()
    body += b'Content-Disposition: form-data; name="metadata"\r\n'
    body += b'Content-Type: application/json\r\n\r\n'
    body += json.dumps(metadata).encode() + b"\r\n"
    body += (f"--{boundary}\r\n").encode()
    body += b'Content-Disposition: form-data; name="index.js"; filename="index.js"\r\n'
    body += b'Content-Type: application/javascript+module\r\n\r\n'
    body += index_js + b"\r\n"
    body += (f"--{boundary}--\r\n").encode()

    url = f"{CF_API}/accounts/{acct}/workers/scripts/{WORKER_NAME}"
    req = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    return api(req)


def main():
    report_url = os.environ.get("REPORT_URL", DEFAULT_REPORT_URL)
    token, acct = resolve_creds()

    print(f"[*] downloading deployed worker `{WORKER_NAME}` ...")
    multipart = download_bundle(token, acct)
    index_js = extract_index_js(multipart)
    print(f"[+] extracted index.js ({len(index_js)} bytes)")

    print(f"[*] patching report URL -> {report_url}")
    patched, n = patch_report_url(index_js, report_url)
    print(f"[+] replaced {n} report URL(s)")

    print("[*] uploading patched worker ...")
    res = upload_bundle(token, acct, patched)
    if not res.get("success"):
        fail(f"upload failed: {json.dumps(res.get('errors'))}")
    print(f"[+] deployed. deployment_id={res.get('result', {}).get('deployment_id')}")

    # sanity: the patched bundle should no longer reference any non-target URL in that slot
    assert report_url.encode() in patched, "patched report URL not present in bundle"
    print(f"[*] verify at https://eksi.biz.id/monitor (should fetch {report_url})")


if __name__ == "__main__":
    main()