"""小说状态工作台的本地启动器与运行状态菜单。

重复执行本文件是安全的：先通过后端健康接口和前端端口检查运行状态，
只启动缺失的服务，不会重复拉起已经运行的进程。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"
RUNTIME_DIR = ROOT / ".runtime"
LOG_DIR = ROOT / "data" / "logs"
BACKEND_URL = "http://127.0.0.1:8000"
FRONTEND_URL = "http://127.0.0.1:5173"
PROJECT_ENV_KEYS = frozenset({
    "DATABASE_URL",
    "DATA_DIR",
    "DEFAULT_LLM_BASE_URL",
    "DEFAULT_LLM_MODEL",
    "RUNTIME_ENVIRONMENT",
    "CORS_ORIGINS",
})


def load_project_env() -> dict[str, str]:
    """Read the local .env without adding a runtime dependency to the launcher."""
    values: dict[str, str] = {}
    env_path = ROOT / ".env"
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in PROJECT_ENV_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


PROJECT_ENV = load_project_env()
PID_FILES = {
    "backend": RUNTIME_DIR / "backend.pid",
    "frontend": RUNTIME_DIR / "frontend.pid",
}
SERVICE_LABELS = {"backend": "后端", "frontend": "前端"}


def request_ok(url: str, timeout: float = 1.2) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 400
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def backend_running() -> bool:
    return request_ok(f"{BACKEND_URL}/api/health")


def frontend_running() -> bool:
    return request_ok(FRONTEND_URL)


def read_pid(service: str) -> int | None:
    try:
        return int(PID_FILES[service].read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def process_exists(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, check=False)
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def status() -> dict[str, str | bool | int | None]:
    backend = backend_running()
    frontend = frontend_running()
    return {
        "backend": backend,
        "frontend": frontend,
        "running": backend and frontend,
        "backend_pid": read_pid("backend"),
        "frontend_pid": read_pid("frontend"),
    }


def print_status(current: dict[str, str | bool | int | None] | None = None) -> None:
    current = current or status()
    backend_label = "运行中" if current["backend"] else "未运行"
    frontend_label = "运行中" if current["frontend"] else "未运行"
    overall = "运行中" if current["running"] else "未完全启动"
    print("\n系统状态")
    print(f"  整体：{overall}")
    print(f"  后端：{backend_label}  {BACKEND_URL}/docs")
    print(f"  前端：{frontend_label}  {FRONTEND_URL}")
    if current["backend_pid"]:
        print(f"  后端进程：{current['backend_pid']}")
    if current["frontend_pid"]:
        print(f"  前端进程：{current['frontend_pid']}")


def start_process(service: str) -> bool:
    RUNTIME_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{service}.log"

    log_file = log_path.open("a", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    for key, value in PROJECT_ENV.items():
        env.setdefault(key, value)

    if service == "backend":
        command = [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000"]
        cwd = ROOT
    elif service == "frontend":
        npm = "npm.cmd" if os.name == "nt" else "npm"
        command = [npm, "run", "dev", "--", "--host", "127.0.0.1"]
        cwd = FRONTEND_DIR
    else:
        log_file.close()
        raise ValueError(f"未知服务：{service}")

    flags = 0
    startupinfo = None
    if os.name == "nt":
        # Keep the launcher console visible for the status menu, but start all
        # managed services without creating child console windows.
        flags = subprocess.CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        startupinfo=startupinfo,
    )
    PID_FILES[service].write_text(str(process.pid), encoding="utf-8")
    log_file.close()
    print(f"已启动{SERVICE_LABELS[service]}，日志：{log_path}")
    return True


def ensure_started() -> dict[str, str | bool | int | None]:
    current = status()
    if current["running"]:
        print("检测到系统已经启动，不重复创建进程。")
        print_status(current)
        return current

    if not current["backend"]:
        start_process("backend")
    if not current["frontend"]:
        start_process("frontend")

    print("正在等待服务就绪…")
    deadline = time.time() + 60
    while time.time() < deadline:
        current = status()
        if current["backend"] and current["frontend"]:
            break
        time.sleep(0.5)
    print_status(current)
    if not current["running"]:
        print("提示：如果服务未就绪，请查看 data/logs/backend.log 或 data/logs/frontend.log。")
    return current


def stop_service(service: str) -> None:
    pid = read_pid(service)
    if not process_exists(pid):
        PID_FILES[service].unlink(missing_ok=True)
        print(f"{service} 当前没有由启动器管理的运行进程。")
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:
        os.kill(pid, signal.SIGTERM)
    PID_FILES[service].unlink(missing_ok=True)
    print(f"已停止{SERVICE_LABELS[service]}。")


def show_menu() -> None:
    while True:
        print_status()
        print("\n菜单：")
        print("  1. 刷新状态")
        print("  2. 启动未运行的服务")
        print("  3. 停止由本启动器管理的服务")
        print("  4. 打开前端页面")
        print("  0. 退出菜单")
        try:
            choice = input("请选择：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "0":
            return
        if choice == "1":
            continue
        if choice == "2":
            ensure_started()
            continue
        if choice == "3":
            stop_service("frontend")
            stop_service("backend")
            continue
        if choice == "4":
            if frontend_running():
                webbrowser.open(FRONTEND_URL)
            else:
                print("前端尚未运行，请先选择 2。")
            continue
        print("请输入菜单中的数字。")


def main() -> None:
    print("小说状态工作台 · 本地启动与状态菜单")
    ensure_started()
    show_menu()


if __name__ == "__main__":
    main()
