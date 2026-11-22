#!/usr/bin/env python3
"""Small management-network reverse proxy for the replicated Policy Mapper."""
import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN = ("192.168.201.254", 5000)
BACKENDS = [(f"192.168.201.{i}", 15002) for i in (1, 2, 3)]
HEALTH_PATH = "/health/ha"
TIMEOUT = 5
LOCK = threading.Lock()
HEALTHY = []
NEXT = 0
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length"}


def probe_loop():
    global HEALTHY
    while True:
        good = []
        for host, port in BACKENDS:
            try:
                req = urllib.request.Request(f"http://{host}:{port}{HEALTH_PATH}")
                with urllib.request.urlopen(req, timeout=1.5) as response:
                    payload = json.load(response)
                if payload.get("status") == "ready":
                    good.append((host, port))
            except Exception:
                pass
        with LOCK:
            HEALTHY = good
        time.sleep(2)


def select_backends():
    global NEXT
    with LOCK:
        if not HEALTHY:
            return []
        start = NEXT % len(HEALTHY)
        ordered = HEALTHY[start:] + HEALTHY[:start]
        NEXT = (start + 1) % len(HEALTHY)
        return ordered


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MapperGateway/1"

    def log_message(self, fmt, *args):
        print("%s %s" % (self.client_address[0], fmt % args), flush=True)

    def do_GET(self): self.forward()
    def do_HEAD(self): self.forward()
    def do_POST(self): self.forward()
    def do_PUT(self): self.forward()
    def do_PATCH(self): self.forward()
    def do_DELETE(self): self.forward()
    def do_OPTIONS(self): self.forward()

    def forward(self):
        if self.path == "/_gateway/health":
            with LOCK:
                nodes = [f"{host}:{port}" for host, port in HEALTHY]
            body = json.dumps({"status": "ready" if nodes else "unavailable", "backends": nodes}).encode()
            self.send_response(200 if nodes else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length > 10 * 1024 * 1024:
            self.send_error(413)
            return
        body = self.rfile.read(length) if length else None
        backends = select_backends()
        if not backends:
            self.reply_error(503, "No healthy mapper backend")
            return
        last_error = None
        for host, port in backends:
            conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
            try:
                # A failed connect is safe to retry because no request bytes were sent.
                conn.connect()
            except OSError as exc:
                last_error = exc
                conn.close()
                continue
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
            headers["X-Forwarded-For"] = self.client_address[0]
            headers["X-Forwarded-Proto"] = "http"
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                response = conn.getresponse()
                data = response.read()
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in HOP_HEADERS:
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)
                conn.close()
                return
            except Exception as exc:
                # Do not retry once a request may have reached the mapper.
                last_error = exc
                conn.close()
                self.reply_error(502, "Mapper request failed; retry only at the client")
                return
        self.reply_error(503, "Mapper backends unreachable")
        if last_error:
            print("backend connect failed: %s" % type(last_error).__name__, flush=True)

    def reply_error(self, status, detail):
        body = json.dumps({"detail": detail}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    threading.Thread(target=probe_loop, daemon=True).start()
    ThreadingHTTPServer(LISTEN, Proxy).serve_forever()
