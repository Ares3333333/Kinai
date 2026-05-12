from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON = str(PY if PY.exists() else sys.executable)

REQUIRED_FILES = [
    "site_server.py",
    "vercel.json",
    "api/[...path].js",
    "pitch_site/index.html",
    "pitch_site/play.html",
    "pitch_site/camera_check.html",
    "pitch_site/demo.html",
    "pitch_site/evidence.html",
    "pitch_site/product_session.js",
    "pitch_site/camera_check.js",
    "pitch_site/evidence.js",
    "pitch_site/investor_demo.js",
    "pitch_site/browser_cv.js",
    "pitch_site/product.css",
    "tests-e2e/play-camera.spec.ts",
    "tests-e2e/play-layout.spec.ts",
    "tests-e2e/evidence-export.spec.ts",
    "tests-e2e/public-fallback.spec.ts",
    "Start-Site.ps1",
    "Start-KinaestheticAI.ps1",
]

MOJIBAKE_TOKENS = [
    "Рђ",
    "РЃ",
    "Рѓ",
    "РЅ",
    "СЃ",
    "С‚",
    "СЊ",
    "Р Т‘",
    "Р В°",
    "Р Вµ",
    "Р С‘",
    "Р С•",
    "Р В»",
    "Р Р…",
    "РЎРѓ",
    "РЎвЂљ",
    "РЎРЉ",
    "РЎвЂ№",
    "РІР‚",
    "Р’В·",
    "вЂ",
    "в†",
    "в–",
    "В·",
    "Гђ",
    "Г‘",
]


def run(label: str, args: list[str]) -> bool:
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    ok = result.returncode == 0
    print(f"{'PASS' if ok else 'FAIL'} {label}")
    if not ok:
      output = (result.stdout + "\n" + result.stderr).strip()
      print(output[-4000:])
    return ok


def check_files() -> bool:
    ok = True
    for rel in REQUIRED_FILES:
        exists = (ROOT / rel).exists()
        print(f"{'PASS' if exists else 'FAIL'} required file: {rel}")
        ok = ok and exists
    return ok


def check_mojibake() -> bool:
    ok = True
    paths = list((ROOT / "pitch_site").glob("**/*")) + [
        ROOT / "README.md",
        ROOT / "DEPLOYMENT.md",
        ROOT / "DEPLOY_READY.md",
    ]
    for path in paths:
        rel_parts = path.relative_to(ROOT).parts
        if "vendor" in rel_parts or "models" in rel_parts:
            continue
        if path.is_dir() or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".wasm"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        hit = next((token for token in MOJIBAKE_TOKENS if token in text), None)
        if hit:
            print(f"FAIL mojibake {path.relative_to(ROOT)} token={hit.encode('unicode_escape').decode('ascii')}")
            ok = False
    if ok:
        print("PASS mojibake scan")
    return ok


def check_http(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            body = response.read() if "/api/" in url else response.read(512)
            ok = 200 <= response.status < 300 and bool(body)
            print(f"{'PASS' if ok else 'FAIL'} http {url} status={response.status}")
            if "/api/" in url:
                json.loads(body.decode("utf-8"))
            return ok
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"FAIL http {url}: {exc}")
        return False


def main() -> int:
    urls = [
        "/",
        "/play",
        "/camera-check",
        "/demo",
        "/overlay",
        "/evidence",
        "/metrics",
        "/privacy",
        "/api/state",
        "/api/system-health",
        "/api/evidence",
        "/api/export-founder-deck",
    ]
    checks = [
        check_files(),
        run("python compile", [PYTHON, "-m", "py_compile", "site_server.py", "scoring.py", "atomic_io.py", "alerts.py", "product_intelligence.py"]),
        run("product_session syntax", ["node", "--check", "pitch_site/product_session.js"]),
        run("browser_cv syntax", ["node", "--check", "pitch_site/browser_cv.js"]),
        run("app.js syntax", ["node", "--check", "pitch_site/app.js"]),
        run("evidence.js syntax", ["node", "--check", "pitch_site/evidence.js"]),
        run("investor_demo.js syntax", ["node", "--check", "pitch_site/investor_demo.js"]),
        run("landing.js syntax", ["node", "--check", "pitch_site/landing.js"]),
        run("vercel api syntax", ["node", "--check", "api/[...path].js"]),
        check_mojibake(),
    ]
    checks.extend(check_http(f"http://localhost:8502{url}") for url in urls)
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
