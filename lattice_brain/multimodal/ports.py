from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .common import MODALITY_IMAGE, VIDEO_UNAVAILABLE_DETAIL

FFMPEG_BINARY = "ffmpeg"
DEFAULT_KEYFRAMES = 4
KEYFRAME_WINDOW = 300
KEYFRAME_TIMEOUT_SECONDS = 120


@dataclass
class MultimodalPorts:
    captioner: Optional[Callable[[str], Optional[str]]] = None
    vision_embedder: Optional[Callable[[str], List[float]]] = None
    transcriber: Optional[Callable[[str], str]] = None
    keyframe_extractor: Optional[Callable[..., Any]] = None
    text_to_image_embedder: Optional[Callable[[str], List[float]]] = None
    vision_model_id: str = ""
    vision_space: str = MODALITY_IMAGE

    def describe(self) -> Dict[str, Any]:
        return {
            "caption": self.captioner is not None,
            "vision_embedding": self.vision_embedder is not None,
            "transcription": self.transcriber is not None,
            "keyframes": self.keyframe_extractor is not None or ffmpeg_available(),
            "text_to_image_query": self.text_to_image_embedder is not None,
            "vision_model_id": self.vision_model_id,
            "vision_space": self.vision_space,
        }


def _which_ffmpeg() -> Optional[str]:
    return shutil.which(FFMPEG_BINARY)


def ffmpeg_available() -> bool:
    return _which_ffmpeg() is not None


def _run_ffmpeg(binary: str, args: List[str]) -> int:
    import asyncio

    async def _exec() -> int:
        proc = await asyncio.create_subprocess_exec(
            binary,
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=KEYFRAME_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise subprocess.TimeoutExpired([binary, *args], KEYFRAME_TIMEOUT_SECONDS)
        return int(proc.returncode or 0)

    return asyncio.run(_exec())


def extract_keyframes(
    path: Any,
    dest_dir: Any,
    *,
    count: int = DEFAULT_KEYFRAMES,
    ports: Optional[MultimodalPorts] = None,
) -> Dict[str, Any]:
    ports = ports or MultimodalPorts()
    video = Path(str(path))
    dest = Path(str(dest_dir))
    wanted = max(1, int(count))
    if ports.keyframe_extractor is not None:
        return _injected_keyframes(ports.keyframe_extractor, video, dest, wanted)
    binary = _which_ffmpeg()
    if binary is None:
        return {
            "status": "unavailable",
            "frames": [],
            "detail": VIDEO_UNAVAILABLE_DETAIL,
        }
    dest.mkdir(parents=True, exist_ok=True)
    args = [
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
        "-vf",
        f"thumbnail={KEYFRAME_WINDOW}",
        "-frames:v",
        str(wanted),
        "-vsync",
        "vfr",
        str(dest / "keyframe-%03d.jpg"),
    ]
    try:
        code = _run_ffmpeg(binary, args)
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        RuntimeError,
        TypeError,
    ) as exc:
        return {"status": "failed", "frames": [], "detail": f"ffmpeg failed: {exc}"}
    frames = sorted(str(p) for p in dest.glob("keyframe-*.jpg"))
    if code != 0 and not frames:
        return {
            "status": "failed",
            "frames": [],
            "detail": f"ffmpeg exited with status {code}",
        }
    if not frames:
        return {
            "status": "empty",
            "frames": [],
            "detail": "ffmpeg produced no frames from this video",
        }
    return {"status": "ok", "frames": frames[:wanted], "detail": ""}


def _injected_keyframes(
    extractor: Callable[..., Any], video: Path, dest: Path, wanted: int
) -> Dict[str, Any]:
    try:
        produced = extractor(str(video), str(dest), wanted)
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        RuntimeError,
        TypeError,
    ) as exc:
        return {
            "status": "failed",
            "frames": [],
            "detail": f"keyframe port failed: {exc}",
        }
    frames = [str(item) for item in (produced or [])][:wanted]
    if not frames:
        return {
            "status": "empty",
            "frames": [],
            "detail": "the keyframe port produced no frames",
        }
    return {"status": "ok", "frames": frames, "detail": ""}
