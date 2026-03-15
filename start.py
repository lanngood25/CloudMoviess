#!/usr/bin/env python3
"""
CloudMovies — Auto-start script
Installs deps if needed, starts the backend, and opens the browser.
"""
import subprocess, sys, os, time, webbrowser, threading

FRONTEND = os.path.join(os.path.dirname(__file__), "index.html")
BACKEND  = os.path.join(os.path.dirname(__file__), "backend.py")

def install_deps():
    deps = ["fastapi", "uvicorn[standard]", "moviebox-api", "httpx"]
    print("📦 Checking dependencies…")
    subprocess.check_call([sys.executable, "-m", "pip", "install", *deps, "-q"])
    print("✅ Dependencies OK")

def open_browser():
    time.sleep(2.5)
    webbrowser.open(f"file://{os.path.abspath(FRONTEND)}")
    print(f"\n🌐 Frontend opened: {FRONTEND}")

if __name__ == "__main__":
    install_deps()
    threading.Thread(target=open_browser, daemon=True).start()
    print("\n🚀 Starting CloudMovies backend on http://localhost:8000 …")
    print("   Press Ctrl+C to stop.\n")
    os.system(f'{sys.executable} -m uvicorn backend:app --host 0.0.0.0 --port 8000 --reload --app-dir "{os.path.dirname(__file__)}"')