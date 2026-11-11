import os


def _existing_browser_path():
    candidates = [
        os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def browser_launch_options(**overrides):
    options = {
        "args": [
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-crash-reporter",
            "--no-first-run",
            "--no-default-browser-check",
        ]
    }
    executable = _existing_browser_path()
    if executable:
        options["executable_path"] = executable
    options.update(overrides)
    return options
