from typer.testing import CliRunner

from aiarb import __version__
from aiarb.cli.app import app


def test_version_flag_prints_the_installed_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"aiarb {__version__}"
