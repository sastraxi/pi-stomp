"""v3-specific system menu behaviour."""

import subprocess
from unittest.mock import MagicMock, patch

from tests.types import SystemFixture


def _fake_completed(stdout: str = "", stderr: str = "") -> MagicMock:
    """A CompletedProcess-like MagicMock for subprocess.run."""
    cp = MagicMock()
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def test_system_info_load(v3_system: SystemFixture):
    """On v3 (no relay), system_info_load reads bypass state from the audiocard."""
    handler = v3_system.handler
    handler.audiocard = MagicMock()
    handler.audiocard.get_switch_parameter.return_value = True
    handler.audiocard.get_bypass_left.return_value = False
    handler.audiocard.get_bypass_right.return_value = True

    with patch("subprocess.check_output", return_value=b"v1.0.0-abc\n"):
        handler.system_info_load()

    assert handler.software_version == "v1.0.0-abc\n"
    assert handler.eq_status is True
    assert handler.bypass_left is False
    assert handler.bypass_right is True


def test_system_info_load_dpkg_clean(v3_system: SystemFixture):
    """dpkg fallback: a clean package (dpkg --verify empty) yields a bare version."""
    handler = v3_system.handler
    handler.audiocard = MagicMock()
    handler.audiocard.get_switch_parameter.return_value = True
    handler.audiocard.get_bypass_left.return_value = False
    handler.audiocard.get_bypass_right.return_value = True

    def check_output_side(cmd, *a, **k):
        # git describe fails → fall into the dpkg-query branch; dpkg-query
        # returns the bare version.
        if "dpkg-query" in cmd:
            return b"1.2.3\n"
        raise subprocess.CalledProcessError(1, cmd)

    with (
        patch("subprocess.check_output", side_effect=check_output_side),
        patch("subprocess.run", return_value=_fake_completed("", "")) as mock_run,
    ):
        handler.system_info_load()

    assert handler.software_version == "1.2.3"
    # dpkg --verify was called and produced no output → no asterisk
    verify_calls = [c for c in mock_run.call_args_list if c.args and c.args[0][:3] == ["dpkg", "--verify", "pi-stomp"]]
    assert len(verify_calls) == 1


def test_system_info_load_dpkg_drifted(v3_system: SystemFixture):
    """dpkg fallback: a drifted package (dpkg --verify reports differences) appends '*'."""
    handler = v3_system.handler
    handler.audiocard = MagicMock()
    handler.audiocard.get_switch_parameter.return_value = True
    handler.audiocard.get_bypass_left.return_value = False
    handler.audiocard.get_bypass_right.return_value = True

    def check_output_side(cmd, *a, **k):
        # git describe fails → fall into the dpkg-query branch; dpkg-query
        # returns the bare version.
        if "dpkg-query" in cmd:
            return b"1.2.3\n"
        raise subprocess.CalledProcessError(1, cmd)

    verify_out = "??5?????? /opt/pistomp/pi-stomp/modalapi/modhandler.py\n"

    with (
        patch("subprocess.check_output", side_effect=check_output_side),
        patch("subprocess.run", return_value=_fake_completed(verify_out, "")),
    ):
        handler.system_info_load()

    assert handler.software_version == "1.2.3*"
