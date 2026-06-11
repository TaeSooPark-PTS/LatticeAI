import os
import shutil

import setup_wizard as setup


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

    monkeypatch.setattr(setup, "COMMON_PATH_DIRS", [python_dir])
    monkeypatch.setenv("PATH", "")

    setup.repair_path_for("python3" if python_path.endswith("python3") else "python")

    assert python_dir in os.environ["PATH"].split(os.pathsep)
