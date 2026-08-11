import os
import shutil

from latticeai.setup import wizard as setup
from latticeai.setup.wizard import catalog as wizard_catalog
from latticeai.setup.wizard import detect as wizard_detect
from latticeai.setup.wizard import install as wizard_install
from latticeai.setup.wizard import paths as wizard_paths
from latticeai.setup.wizard import plans as wizard_plans
from latticeai.setup.wizard import recommend as wizard_recommend

# ── v11.3.0 split shim ────────────────────────────────────────────────────────
# ``latticeai/setup/wizard.py`` became a package (paths / detect / plans /
# catalog / recommend / install). Reading a name through the package still
# works, so the calls below are unchanged — but *patching* a name on the
# package ``__init__`` does not reach a submodule's own global. Every stub is
# therefore installed on every module that binds the name, which is exactly
# the one binding the single-file module used to have.
_WIZARD_MODULES = (
    setup,
    wizard_catalog,
    wizard_detect,
    wizard_install,
    wizard_paths,
    wizard_plans,
    wizard_recommend,
)


def _patch(monkeypatch, name, value):
    targets = [module for module in _WIZARD_MODULES if hasattr(module, name)]
    assert targets, f"no wizard module binds {name!r}"
    for module in targets:
        monkeypatch.setattr(module, name, value)


def test_scan_environment_includes_components_and_paths():
    env = setup.scan_environment()

    assert "components" in env
    assert "python" in env["components"]
    assert "official_url" in env["components"]["python"]
    assert "path" in env
    assert "active" in env["path"]


def test_recommendations_include_components():
    env = setup.scan_environment()
    recs = setup.get_recommendations(env)

    assert "components" in recs
    assert any(item["id"].startswith("component_") for item in recs["components"])


def test_repair_path_can_find_binary_from_common_dir(monkeypatch):
    python_path = shutil.which("python3") or shutil.which("python")
    assert python_path
    python_dir = os.path.dirname(python_path)

    _patch(monkeypatch, "COMMON_PATH_DIRS", [python_dir])
    monkeypatch.setenv("PATH", "")

    setup.repair_path_for("python3" if python_path.endswith("python3") else "python")

    assert python_dir in os.environ["PATH"].split(os.pathsep)
