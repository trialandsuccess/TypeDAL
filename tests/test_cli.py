import tempfile

from typer.testing import CliRunner

from src.typedal.__about__ import __version__
from src.typedal.cli import app

# by default, click's cli runner mixes stdout and stderr for some reason...
runner = CliRunner(mix_stderr=False)


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_generate_dry():
    result = runner.invoke(app, ["generate-migrations", "--dry-run"])
    assert result.exit_code == 0

    assert "would run" in result.stderr.lower()
    assert "create" in result.stderr

    with tempfile.NamedTemporaryFile() as f:
        f.write(b"...")

        result = runner.invoke(app, ["generate-migrations", "--dry-run", "--output-file", f.name])
        assert result.exit_code == 0
        assert "would run" in result.stderr.lower()
        assert "alter" in result.stderr


def test_run_dry():
    result = runner.invoke(app, ["run-migrations", "--dry-run"])
    assert result.exit_code == 0

    print(result.stdout)
    print(result.stderr)

    assert "would run" in result.stderr.lower()


def test_show_config():
    result = runner.invoke(app, ["--show-config"])
    assert result.exit_code == 0
    assert not result.stderr
    assert result.stdout.strip().startswith("<TypeDAL")
