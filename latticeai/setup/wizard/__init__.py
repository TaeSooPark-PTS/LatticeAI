"""
Smart Setup Wizard — Environment Scanner, Recommender & Auto-Installer
Detects hardware, tools, and API keys; returns tailored recommendations;
streams SSE installation progress.

Formerly the root ``setup.py``; renamed in v4 so it no longer collides with
the setuptools build entrypoint and actually ships in the wheel
(``pyproject.toml`` py-modules). Packaging is owned entirely by
``pyproject.toml`` — there is deliberately no root ``setup.py``.

Split into cohesive submodules in v11.3.0 (no behaviour change): ``paths``
(PATH repair + binary lookup + the module-level ``COMMON_PATH_DIRS``),
``detect`` (hardware/OS/tool probes and ``scan_environment``), ``plans``
(SSE framing + confirmation-token command plans), ``catalog`` (model tables),
``recommend`` (``get_recommendations``) and ``install`` (verify/repair and the
SSE ``install_stream``). This module re-exports every name the single file
exposed, so ``latticeai.setup.wizard.X`` keeps working.

Stubbing note: rebinding one of these names *here* changes only this module's
name. The submodule that calls it holds its own reference, so a test standing
in for a helper patches the submodule that uses it.
"""

from __future__ import annotations

# The single file had no ``__all__``, so its public surface was "every module
# global" — including the names it imported for its own use. Every re-export
# below therefore uses the redundant-alias form: it reproduces exactly that
# surface, and it marks each name as deliberate rather than a leftover import.
from latticeai.core.quiet import quiet as quiet
from latticeai.services.process_audit import (
    CommandConfirmationError as CommandConfirmationError,
)
from latticeai.services.process_audit import (
    append_process_audit_event as append_process_audit_event,
)
from latticeai.services.process_audit import command_plan as command_plan
from latticeai.services.process_audit import (
    command_plan_for_commands as command_plan_for_commands,
)
from latticeai.services.process_audit import (
    require_command_confirmation as require_command_confirmation,
)
from latticeai.services.setup_detection import detect_cuda as detect_cuda
from latticeai.services.setup_detection import detect_tools as detect_tools
from latticeai.services.setup_detection import (
    detect_wsl_from_text as detect_wsl_from_text,
)
from latticeai.setup.wizard.catalog import (
    _BEST_MODEL_TIERS as _BEST_MODEL_TIERS,
)
from latticeai.setup.wizard.catalog import (
    _CROSS_PLATFORM_MODEL_CATALOG as _CROSS_PLATFORM_MODEL_CATALOG,
)
from latticeai.setup.wizard.catalog import (
    _MODEL_CATALOG as _MODEL_CATALOG,
)
from latticeai.setup.wizard.catalog import (
    _VERSIONED_MODEL_PATTERNS as _VERSIONED_MODEL_PATTERNS,
)
from latticeai.setup.wizard.catalog import (
    _best_model_for_engine as _best_model_for_engine,
)
from latticeai.setup.wizard.catalog import (
    _catalog_row_family_version as _catalog_row_family_version,
)
from latticeai.setup.wizard.catalog import (
    _filter_lower_family_versions as _filter_lower_family_versions,
)
from latticeai.setup.wizard.catalog import (
    _version_tuple as _version_tuple,
)
from latticeai.setup.wizard.detect import _cmd as _cmd
from latticeai.setup.wizard.detect import _detect_api_keys as _detect_api_keys
from latticeai.setup.wizard.detect import _detect_chip as _detect_chip
from latticeai.setup.wizard.detect import _detect_cpu as _detect_cpu
from latticeai.setup.wizard.detect import _detect_cuda as _detect_cuda
from latticeai.setup.wizard.detect import (
    _detect_disk_free_gb as _detect_disk_free_gb,
)
from latticeai.setup.wizard.detect import _detect_gpu as _detect_gpu
from latticeai.setup.wizard.detect import _detect_mlx as _detect_mlx
from latticeai.setup.wizard.detect import _detect_ram_gb as _detect_ram_gb
from latticeai.setup.wizard.detect import _detect_tools as _detect_tools
from latticeai.setup.wizard.detect import _detect_wsl as _detect_wsl
from latticeai.setup.wizard.detect import (
    _parse_windows_video_controllers as _parse_windows_video_controllers,
)
from latticeai.setup.wizard.detect import scan_environment as scan_environment
from latticeai.setup.wizard.install import _brew_install as _brew_install
from latticeai.setup.wizard.install import _pip_install as _pip_install
from latticeai.setup.wizard.install import _repair_action as _repair_action
from latticeai.setup.wizard.install import _verify_action as _verify_action
from latticeai.setup.wizard.install import _verify_binary as _verify_binary
from latticeai.setup.wizard.install import _wait_for_binary as _wait_for_binary
from latticeai.setup.wizard.install import install_stream as install_stream
from latticeai.setup.wizard.install import open_url as open_url
from latticeai.setup.wizard.paths import COMMON_PATH_DIRS as COMMON_PATH_DIRS
from latticeai.setup.wizard.paths import OFFICIAL_DOWNLOADS as OFFICIAL_DOWNLOADS
from latticeai.setup.wizard.paths import PACKAGE_MODULES as PACKAGE_MODULES
from latticeai.setup.wizard.paths import (
    WINDOWS_BINARY_CANDIDATES as WINDOWS_BINARY_CANDIDATES,
)
from latticeai.setup.wizard.paths import _component_detail as _component_detail
from latticeai.setup.wizard.paths import _merge_path_dirs as _merge_path_dirs
from latticeai.setup.wizard.paths import _module_available as _module_available
from latticeai.setup.wizard.paths import _package_module as _package_module
from latticeai.setup.wizard.paths import _persist_extra_path as _persist_extra_path
from latticeai.setup.wizard.paths import _project_env_file as _project_env_file
from latticeai.setup.wizard.paths import _update_env_file as _update_env_file
from latticeai.setup.wizard.paths import _which_any as _which_any
from latticeai.setup.wizard.paths import _which_detail as _which_detail
from latticeai.setup.wizard.paths import repair_path_for as repair_path_for
from latticeai.setup.wizard.plans import _action_command_plan as _action_command_plan
from latticeai.setup.wizard.plans import _action_commands as _action_commands
from latticeai.setup.wizard.plans import _attach_action_plan as _attach_action_plan
from latticeai.setup.wizard.plans import (
    _hydrate_install_actions as _hydrate_install_actions,
)
from latticeai.setup.wizard.plans import _sse as _sse
from latticeai.setup.wizard.plans import (
    _verify_action_confirmation as _verify_action_confirmation,
)
from latticeai.setup.wizard.recommend import (
    get_recommendations as get_recommendations,
)
