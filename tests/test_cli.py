import subprocess
import sys

import pytest

from lil_memory import __version__
from lil_memory.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"lil-memory {__version__}"


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage: lil-memory" in capsys.readouterr().out


def test_module_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "lil_memory", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == f"lil-memory {__version__}"
