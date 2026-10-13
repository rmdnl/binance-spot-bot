from __future__ import annotations

import os
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install.sh"


def _fake_python(path: Path) -> Path:
    executable = path / "fake-python"
    executable.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "-m" && "$2" == "venv" ]]; then\n'
        '  mkdir -p .venv/bin\n'
        "  for name in python pip pytest; do\n"
        '    printf "#!/usr/bin/env bash\\nexit 0\\n" > ".venv/bin/$name"\n'
        '    chmod +x ".venv/bin/$name"\n'
        "  done\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _run_installer(app_dir: Path, fake_python: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(INSTALL_SCRIPT)],
        cwd=REPO_ROOT,
        env={**os.environ, "APP_DIR": str(app_dir), "PYTHON": str(fake_python)},
        text=True,
        capture_output=True,
        check=False,
    )


def test_installer_restricts_permissions_of_preexisting_env_file(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / ".env.example").write_text("APP_ENV=development\n", encoding="utf-8")
    env_file = app_dir / ".env"
    env_file.write_text("APP_SECRET_KEY=private\n", encoding="utf-8")
    env_file.chmod(0o644)

    result = _run_installer(app_dir, _fake_python(tmp_path))

    assert result.returncode == 0, result.stderr
    assert env_file.stat().st_mode & 0o777 == 0o600


def test_installer_rejects_env_symlink_before_creating_virtualenv(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / ".env.example").write_text("APP_ENV=development\n", encoding="utf-8")
    target = tmp_path / "outside.env"
    target.write_text("APP_SECRET_KEY=private\n", encoding="utf-8")
    (app_dir / ".env").symlink_to(target)
    fake_python = _fake_python(tmp_path)

    result = _run_installer(app_dir, fake_python)

    assert result.returncode != 0
    assert ".env must be a regular file, not a symlink." in result.stderr
    assert not (app_dir / ".venv").exists()
    assert target.stat().st_mode & 0o777 == 0o644
