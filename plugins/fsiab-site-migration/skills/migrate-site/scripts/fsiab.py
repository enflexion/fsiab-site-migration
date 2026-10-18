"""Minimal FSIAB API v3 client used by the migration tooling."""
import json, time, urllib.request, urllib.error, uuid, mimetypes, os

BASE = "https://www.myweblogin.com/api/v3"


class ApiError(Exception):
    def __init__(self, code, message, body=None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.body = code, message, body


class Fsiab:
    def __init__(self, key, base=BASE, pause=1.1, log=None):
        self.key, self.base, self.pause, self.log = key, base.rstrip("/"), pause, log
        self._last = 0.0

    def _throttle(self):
        # API limit is 60/min per key. Stay under it.
        wait = self.pause - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def _send(self, method, path, body=None, headers=None, query=None, raw=None):
        self._throttle()
        url = self.base + path
        if query:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
        h = {"Authorization": f"Bearer {self.key}", "User-Agent": "FSIAB-migrate/1.0"}
        data = None
        verb = method
        if method in ("PUT", "DELETE"):
            verb = "POST"
            h["X-HTTP-Method-Override"] = method
        if raw is not None:
            data = raw
        elif body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        if headers:
            h.update(headers)
        for attempt in range(5):
            req = urllib.request.Request(url, data=data, headers=h, method=verb)
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    txt = r.read().decode("utf-8", "replace")
                    status = r.status
            except urllib.error.HTTPError as e:
                txt = e.read().decode("utf-8", "replace")
                status = e.code
                if status == 429:
                    ra = int(e.headers.get("Retry-After", "10") or 10)
                    time.sleep(min(ra, 65))
                    continue
            except urllib.error.URLError as e:
                if attempt < 4:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise ApiError(0, f"network error: {e}")
            break
        try:
            j = json.loads(txt)
        except Exception:
            raise ApiError(status, f"non-JSON response: {txt[:300]}")
        if self.log:
            def short(x):
                s = json.dumps(x, ensure_ascii=False)
                return x if len(s) <= 1500 else s[:1500] + "...(truncated)"
            self.log.write(json.dumps({"t": time.strftime("%H:%M:%S"), "secs": round(time.time() - self._last, 1),
                                       "m": method, "p": path, "s": status,
                                       "req": short(body) if raw is None else f"<multipart {len(raw)} bytes>",
                                       "res": short(j)}, ensure_ascii=False) + "\n")
            self.log.flush()
        if j.get("status") != "ok":
            raise ApiError(status, j.get("message", txt[:300]), j)
        return j["data"]

    def get(self, path, **q):
        return self._send("GET", path, query=q or None)

    def post(self, path, body):
        return self._send("POST", path, body=body)

    def put(self, path, body):
        return self._send("PUT", path, body=body)

    def delete(self, path):
        return self._send("DELETE", path)

    def upload(self, path, files, fields=None):
        """files: list of (fieldname, filepath). fields: list of (name, value)."""
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in (fields or []):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        for name, fp in files:
            ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
            with open(fp, "rb") as f:
                content = f.read()
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{os.path.basename(fp)}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
                         + content + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        raw = b"".join(parts)
        return self._send("POST", path, raw=raw, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})


import urllib.parse  # noqa: E402  (used in _send)
