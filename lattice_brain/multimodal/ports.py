"""The injected capability ports, and the ffmpeg fallback behind one of them.

:class:`MultimodalPorts` is how every heavy model reaches Brain Core: as a
plain callable the app layer supplies, never as an import. The keyframe port is
the one with a built-in fallback — ffmpeg on ``PATH`` — so the probe
(:func:`_which_ffmpeg`), the runner (:func:`_run_ffmpeg`) and the extraction
(:func:`extract_keyframes`) live here beside it rather than in ``video.py``.
Splitting them would put ``ffmpeg_available``'s view of the probe and
``extract_keyframes``'s view of it in two different module namespaces, which is
two things to stub instead of one.
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404 — one fixed binary, argv list, never a shell
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .common import MODALITY_IMAGE, VIDEO_UNAVAILABLE_DETAIL

#: The decoder. Looked up by name on PATH and never bundled — a product that
#: cannot decode a ``.mov`` says so instead of shipping a codec pack.
FFMPEG_BINARY = "ffmpeg"
#: Keyframes kept per video. Four is a memory of a video, not a copy of it.
DEFAULT_KEYFRAMES = 4
#: Frames ffmpeg's ``thumbnail`` filter considers before picking one. Larger
#: windows mean more representative frames and a slower pass.
KEYFRAME_WINDOW = 300
KEYFRAME_TIMEOUT_SECONDS = 120


# ── injected capability ports ────────────────────────────────────────────────
@dataclass
class MultimodalPorts:
    """The optional model-backed capabilities Brain Core cannot ship itself.

    Every field is a plain callable so the app layer can build it from
    ``latticeai.core.embedding_providers`` without Brain Core ever importing
    that package. ``None`` everywhere is the honest default: OCR still runs
    (``pytesseract`` is a local binary, not a model download), and everything
    else reports itself as unavailable.
    """

    #: ``(image_path) -> caption or None`` — a loaded VLM, or nothing.
    captioner: Optional[Callable[[str], Optional[str]]] = None
    #: ``(image_path) -> vector`` in the image space (raises when it cannot).
    vision_embedder: Optional[Callable[[str], List[float]]] = None
    #: ``(audio_path) -> transcript`` (raises/returns empty when it cannot).
    transcriber: Optional[Callable[[str], str]] = None
    #: ``(video_path, dest_dir, count) -> [frame paths]`` (v11.2.0). ``None``
    #: falls back to ffmpeg on PATH; absent ffmpeg is reported, never faked.
    keyframe_extractor: Optional[Callable[..., Any]] = None
    #: ``(query_text) -> vector`` in the *image* space (v11.2.0). Only a
    #: genuinely shared-space vision model can supply one, which is why it is
    #: its own port instead of being assumed from ``vision_embedder``.
    text_to_image_embedder: Optional[Callable[[str], List[float]]] = None
    #: Identity of the vision model, recorded next to every image vector.
    vision_model_id: str = ""
    #: ``image`` (own index + late fusion) or ``shared`` (same space as text).
    vision_space: str = MODALITY_IMAGE

    def describe(self) -> Dict[str, Any]:
        """What this install can honestly do with a picture or a recording."""
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
    """Absolute path to ffmpeg, or ``None``. The one probe, seamed for tests."""
    return shutil.which(FFMPEG_BINARY)


def ffmpeg_available() -> bool:
    """Whether this machine can decode a video at all (honest, never assumed)."""
    return _which_ffmpeg() is not None


def _run_ffmpeg(binary: str, args: List[str]) -> int:
    """Run one fixed binary with an argv list — no shell, no user strings."""
    completed = subprocess.run(  # noqa: S603 — argv list, fixed binary, no shell
        [binary, *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=KEYFRAME_TIMEOUT_SECONDS,
        check=False,
    )
    return int(completed.returncode)


def extract_keyframes(
    path: Any,
    dest_dir: Any,
    *,
    count: int = DEFAULT_KEYFRAMES,
    ports: Optional[MultimodalPorts] = None,
) -> Dict[str, Any]:
    """Pull up to ``count`` representative stills out of a video.

    An injected ``ports.keyframe_extractor`` wins outright — that is the seam
    an install with its own decoder (or a test) uses. Otherwise ffmpeg's
    ``thumbnail`` filter picks the most representative frame from each window
    of :data:`KEYFRAME_WINDOW` frames, which is one pass and no probing.

    Never raises. A missing decoder, a non-zero exit, and a video too short to
    yield a single frame are three different states and each says so.
    """
    ports = ports or MultimodalPorts()
    video = Path(str(path))
    dest = Path(str(dest_dir))
    wanted = max(1, int(count))
    if ports.keyframe_extractor is not None:
        return _injected_keyframes(ports.keyframe_extractor, video, dest, wanted)
    binary = _which_ffmpeg()
    if binary is None:
        return {"status": "unavailable", "frames": [], "detail": VIDEO_UNAVAILABLE_DETAIL}
    dest.mkdir(parents=True, exist_ok=True)
    args = [
        "-nostdin", "-loglevel", "error", "-y",
        "-i", str(video),
        "-vf", f"thumbnail={KEYFRAME_WINDOW}",
        "-frames:v", str(wanted),
        "-vsync", "vfr",
        str(dest / "keyframe-%03d.jpg"),
    ]
    try:
        code = _run_ffmpeg(binary, args)
    except Exception as exc:  # noqa: BLE001 — a broken decoder is a state
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
    """Run a caller-supplied extractor; a failure is reported, never raised."""
    try:
        produced = extractor(str(video), str(dest), wanted)
    except Exception as exc:  # noqa: BLE001 — an injected port is not trusted more
        return {"status": "failed", "frames": [], "detail": f"keyframe port failed: {exc}"}
    frames = [str(item) for item in (produced or [])][:wanted]
    if not frames:
        return {
            "status": "empty",
            "frames": [],
            "detail": "the keyframe port produced no frames",
        }
    return {"status": "ok", "frames": frames, "detail": ""}
