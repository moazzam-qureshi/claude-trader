from typer.testing import CliRunner

from trading_sandwich.cli import app

runner = CliRunner()


def test_cli_has_phase2_commands():
    result = runner.invoke(app, ["--help"])
    assert "proposals" in result.output
    assert "orders" in result.output
    assert "positions" in result.output
    assert "calibration" in result.output


def test_trading_status_command_exists():
    result = runner.invoke(app, ["trading", "--help"])
    assert "status" in result.output
    assert "pause" in result.output
    assert "resume" in result.output
