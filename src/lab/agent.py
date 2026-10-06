"""GUIDE Phần 1 - Dựng tác tử (agent) bằng Deep Agents.   >>> SINH VIÊN CÀI ĐẶT make_backend VÀ build_agent <<<

Pseudo-code: guides/pseudocode/01_agent.md
Kiểm tra:    pytest tests/test_02_agent.py
"""
from pathlib import Path
import shlex
import sys

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from .model import make_model
from .subagents import get_subagents

# ---- CÓ SẴN, KHÔNG SỬA: system prompt dùng chung cho mọi sinh viên (để đường cơ sở so sánh được) ----
PATHS_NOTE = (
    "PATHS: every path is relative to the sandbox root and never starts with '/'. "
    "The task files are in the folder workspace/ (for example workspace/app.log). "
    "Use exactly this relative form both in the file tools and in the shell (execute); "
    "the shell starts in the sandbox root. "
)
BASE_PROMPT = (
    "You are an engineering assistant working in a sandbox. "
    + PATHS_NOTE
    + "Use the shell to run Python and tests. "
    "When you are done, reply with a short summary that mentions only files you really created or changed."
)
SKILLS_NOTE = (
    " Skills are in the folder skills/ (one sub-folder per skill with a SKILL.md). "
    "As your FIRST action, read the SKILL.md of every skill whose description could apply to the task, "
    "then follow them. Never modify skills/."
)
SUBAGENTS_NOTE = (
    " You have specialised subagents (see the description of the task tool). "
    "For anything beyond a trivial step, delegate to a suitable subagent and put ALL the task rules and file paths "
    "in the delegation message, because a subagent sees only what you send. "
    "Check what a subagent returns before you rely on it."
)
# --------------------------------------------------------------------------------------------------


SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
# Directories with personal data or other projects; the agent's shell may not read them.
PRIVATE_DIRS = ("/Users", "/Volumes", "/private/tmp", "/private/var/folders", "/private/var/root", "/home")


def _sbpl(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _seatbelt_profile(sandbox: Path, python_prefix: Path) -> str:
    """macOS Seatbelt profile: read/write only inside the sandbox, read the Python install, no network.

    LocalShellBackend runs commands directly on the host (virtual_mode only guards the file tools), so
    without this the agent can read the whole disk, including the lab's own check.py files.
    """
    keep = [sandbox, python_prefix]
    ancestors = {p for k in keep for p in k.parents}
    return "\n".join([
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        "(deny file-read* " + " ".join(f"(subpath {_sbpl(Path(d))})" for d in PRIVATE_DIRS) + ")",
        "(allow file-read* " + " ".join(f"(subpath {_sbpl(k)})" for k in keep) + ")",
        # path resolution (realpath, stat) needs the metadata of the parent folders, not their content
        "(allow file-read-metadata " + " ".join(f"(literal {_sbpl(a)})" for a in sorted(ancestors)) + ")",
        "(deny file-write*)",
        f'(allow file-write* (subpath {_sbpl(sandbox)}) (regex #"^/dev/"))',
    ])


class SandboxedShellBackend(LocalShellBackend):
    """LocalShellBackend whose shell commands run under `sandbox-exec` (macOS)."""

    def __init__(self, *args, profile: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._profile = profile

    def execute(self, command, *, timeout=None):
        if not command or not isinstance(command, str):
            return super().execute(command, timeout=timeout)
        wrapped = f"{SANDBOX_EXEC} -p {shlex.quote(self._profile)} /bin/sh -c {shlex.quote(command)}"
        return super().execute(wrapped, timeout=timeout)


def make_backend(sandbox: Path):
    """Tạo backend (môi trường thực thi) cho tác tử.

    Yêu cầu:
      - Thư mục gốc (root_dir) là `sandbox`; đường dẫn tương đối `workspace/...` và `skills/...`
        phải dùng được ở CẢ công cụ tệp lẫn shell (shell chạy với thư mục làm việc = `sandbox`).
      - Tác tử chạy được lệnh shell và gọi được `python` (cần đặt PATH).
      - KHÔNG chuyển biến môi trường của bạn vào shell của tác tử (khóa API không được lộ).
    Trên macOS, shell còn bị giới hạn bằng sandbox-exec (chỉ đọc/ghi trong `sandbox`, không có mạng).
    """
    sandbox = Path(sandbox).resolve()
    tmp = sandbox / ".tmp"
    tmp.mkdir(exist_ok=True)
    python_dir = str(Path(sys.executable).parent)
    env = {
        "PATH": python_dir + ":/usr/local/bin:/usr/bin:/bin",
        "HOME": str(sandbox),
        "TMPDIR": str(tmp),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    kwargs = dict(root_dir=sandbox, virtual_mode=True, inherit_env=False, env=env, timeout=120)
    if not SANDBOX_EXEC.exists():   # Linux/WSL: no Seatbelt; run the lab in Docker to isolate the shell
        return LocalShellBackend(**kwargs)
    return SandboxedShellBackend(profile=_seatbelt_profile(sandbox, Path(sys.prefix).resolve()), **kwargs)
def build_agent(sandbox: Path, mode: str = "single", use_skills: bool = False, model=None):
    """Tạo tác tử Deep Agents.

    Tham số:
      sandbox:    thư mục chứa `workspace/` (và `skills/` nếu có).
      mode:       "single"    -> tác tử mặc định (có subagent `general-purpose` sẵn của Deep Agents)
                  "subagents" -> thêm các subagent từ `get_subagents()` (nối PATHS_NOTE vào `system_prompt` của MỖI subagent,
                                 vì subagent không nhận BASE_PROMPT) và thêm SUBAGENTS_NOTE vào prompt chính
      use_skills: True -> nạp thư mục "/skills/" qua tham số `skills=` của create_deep_agent
                  và thêm SKILLS_NOTE vào prompt.
      model:      mô hình ngôn ngữ; None -> dùng `make_model()`.
    mode không hợp lệ -> ném ValueError.
    Trả về: đồ thị (graph) đã biên dịch, gọi bằng `.invoke({"messages": [...]})`.
    """
    if mode not in ("single", "subagents"):
        raise ValueError(f"unknown mode: {mode!r} (expected 'single' or 'subagents')")

    kwargs = {}
    prompt = BASE_PROMPT

    if mode == "subagents":
        kwargs["subagents"] = [
            {**sub, "system_prompt": sub["system_prompt"] + " " + PATHS_NOTE}
            for sub in get_subagents()
        ]
        prompt = prompt + SUBAGENTS_NOTE

    if use_skills:
        kwargs["skills"] = ["/skills/"]
        prompt = prompt + SKILLS_NOTE

    return create_deep_agent(
        model=model if model is not None else make_model(),
        system_prompt=prompt,
        backend=make_backend(sandbox),
        **kwargs,
    )

