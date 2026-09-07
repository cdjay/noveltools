import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load_launcher():
    spec = importlib.util.spec_from_file_location("noveltools_launcher", ROOT / "run.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_only_manages_story_bible_backend_and_frontend(monkeypatch):
    launcher = load_launcher()
    monkeypatch.setattr(launcher, "backend_running", lambda: True)
    monkeypatch.setattr(launcher, "frontend_running", lambda: True)

    current = launcher.status()

    assert current == {
        "backend": True,
        "frontend": True,
        "running": True,
        "backend_pid": launcher.read_pid("backend"),
        "frontend_pid": launcher.read_pid("frontend"),
    }
    assert set(launcher.PID_FILES) == {"backend", "frontend"}
    assert set(launcher.PROJECT_ENV) <= launcher.PROJECT_ENV_KEYS
