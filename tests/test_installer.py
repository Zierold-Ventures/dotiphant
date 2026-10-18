"""Static checks on install.sh, the script behind `curl ... | sh`."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.sh"
SITE_COPY = ROOT / "site" / "public" / "install.sh"


def script() -> str:
    return INSTALLER.read_text()


def test_site_serves_the_same_installer():
    assert SITE_COPY.read_bytes() == INSTALLER.read_bytes()


def test_parses_as_posix_sh():
    subprocess.run(["sh", "-n", str(INSTALLER)], check=True)


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_is_clean():
    subprocess.run(["shellcheck", "-s", "sh", str(INSTALLER)], check=True)


def test_main_runs_only_on_the_last_line():
    # A download cut off before the last line must run nothing.
    lines = [line for line in script().splitlines() if line.strip()]
    assert lines[-1] == 'main "$@"'
    top_level_calls = [line for line in lines if line.startswith("main ")]
    assert top_level_calls == ['main "$@"']


def test_every_curl_call_is_https_only():
    calls = [
        line for line in script().splitlines()
        if re.search(r"\bcurl -", line) and not line.lstrip().startswith("#")
    ]
    assert calls
    for line in calls:
        assert "--proto" in line and "--tlsv1.2" in line, line


def test_uv_install_ignores_user_index_config():
    text = script()
    installs = [line for line in text.splitlines() if "uv tool install --upgrade --no-config" in line]
    assert len(installs) == 2
    for line in installs:
        assert '--default-index "$index"' in line
    assert 'index="${DOTIPHANT_INDEX_URL:-$PYPI_URL}"' in text
    for var in ("UV_INDEX", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_EXTRA_INDEX_URL"):
        assert re.search(rf"unset[^\n]*\b{var}\b", text), var


def test_release_install_uses_checked_constraints():
    text = script()
    assert "check_sum constraints.txt" in text
    assert '--constraints "$2"' in text
