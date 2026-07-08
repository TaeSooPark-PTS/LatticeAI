import json
from pathlib import Path

from latticeai.runtime.audit_runtime import build_audit_runtime
from latticeai.services.setup_detection import (
    detect_cuda,
    detect_tools,
    detect_wsl_from_text,
    parse_windows_video_controllers,
)


class _Logger:
    def warning(self, *_args, **_kwargs):
        pass


def test_audit_runtime_appends_jsonl_without_rewriting_legacy_json(tmp_path: Path):
    audit_file = tmp_path / "audit_log.json"
    audit_file.write_text(json.dumps([{"event_type": "legacy", "timestamp": "2026-01-01T00:00:00"}]), encoding="utf-8")
    runtime = build_audit_runtime(audit_file=audit_file, logging=_Logger())

    runtime["append_audit_event"]("new_event", message="hello")

    assert json.loads(audit_file.read_text(encoding="utf-8"))[0]["event_type"] == "legacy"
    jsonl_file = tmp_path / "audit_log.json.jsonl"
    lines = jsonl_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event_type"] == "new_event"
    assert [item["event_type"] for item in runtime["get_audit_log"]()] == ["legacy", "new_event"]


def test_setup_detection_shared_windows_gpu_parser_handles_json_and_wmic():
    parsed = parse_windows_video_controllers('[{"Name":"NVIDIA RTX","AdapterRAM":4294967296}]')
    assert parsed == [{"name": "NVIDIA RTX", "vram_mb": 4096}]

    parsed = parse_windows_video_controllers("Name=Intel Arc\nAdapterRAM=2147483648\n")
    assert parsed == [{"name": "Intel Arc", "vram_mb": 2048}]


def test_setup_detection_shared_cuda_wsl_and_tools_helpers():
    paths = {"nvidia-smi": "/bin/nvidia-smi", "git": "/bin/git"}

    def which(binary: str):
        return paths.get(binary)

    def run(args):
        if "--query-gpu=driver_version" in args:
            return "555.42\n"
        return ""

    assert detect_cuda(which, run) == (True, "555.42", "/bin/nvidia-smi", None)
    assert detect_wsl_from_text("linux", "Linux microsoft-standard-WSL2") == (True, "2")
    assert detect_tools(which, ["git", "node"]) == {"git": "/bin/git", "node": None}
