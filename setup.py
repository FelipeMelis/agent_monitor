"""py2app build script.

Build with:  .venv/bin/python setup.py py2app
"""

from setuptools import setup

APP = ["packaging/agent_monitor_launcher.py"]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "packaging/AgentMonitor.icns",
    "extra_scripts": ["packaging/agent-monitor-hook.py"],
    "plist": {
        "CFBundleName": "Agent Monitor",
        "CFBundleDisplayName": "Agent Monitor",
        "CFBundleIdentifier": "dev.felipemelis.agentmonitor",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSUIElement": True,
        "NSHumanReadableCopyright": "",
        # GUI-launched (LaunchServices) apps don't inherit LANG/LC_ALL
        # from a shell, so Python's filesystem-encoding detection can
        # fall back to ASCII and raise UnicodeDecodeError on any
        # non-ASCII project folder name. Force UTF-8 explicitly.
        "LSEnvironment": {
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "PYTHONUTF8": "1",
        },
    },
    "packages": ["agent_monitor"],
}

setup(
    app=APP,
    name="Agent Monitor",
    options={"py2app": OPTIONS},
)
