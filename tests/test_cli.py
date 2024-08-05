import tempfile

import pytest
from typer.testing import CliRunner

from src.typedal.__about__ import __version__
from src.typedal.cli import app, get_output_format

# by default, click's cli runner mixes stdout and stderr for some reason...
runner = CliRunner(mix_stderr=False)


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_generate_dry():
    result = runner.invoke(app, ["migrations.generate", "--dry-run"])
    assert result.exit_code == 0

    assert "would run" in result.stderr.lower()
    assert "create" in result.stderr

    with tempfile.NamedTemporaryFile() as f:
        f.write(b"...")

        result = runner.invoke(app, ["migrations.generate", "--dry-run", "--output-file", f.name])
        assert result.exit_code == 0
        assert "would run" in result.stderr.lower()
        assert "alter" in result.stderr


def test_stub():
    # mostly tested in pydal2sql already
    result = runner.invoke(app, ["migrations.stub", "my_test_migration", "--dry-run"])
    assert result.exit_code == 0

    assert "@migration" in result.stdout
    assert "def my_test_migration" in result.stdout


def test_run_dry():
    result = runner.invoke(app, ["migrations.run", "--dry-run"])
    assert result.exit_code == 0

    print(result.stdout)
    print(result.stderr)

    assert "would run" in result.stderr.lower()


def test_show_config():
    result = runner.invoke(app, ["--show-config"])
    assert result.exit_code == 0
    assert not result.stderr
    assert result.stdout.strip().startswith("<TypeDAL")


def test_get_output_format(capsys):
    import json

    import tomli
    import yaml

    with pytest.raises(ValueError):
        assert not get_output_format("bleepbloop")

    get_output_format("json")({"some": {"nested": "data"}})
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result
    assert result["some"]["nested"] == "data"

    get_output_format("yaml")({"some": {"nested": "data"}})
    captured = capsys.readouterr()
    result = yaml.load(captured.out, yaml.Loader)
    assert result
    assert result["some"]["nested"] == "data"

    get_output_format("toml")({"some": {"nested": "data"}})
    captured = capsys.readouterr()
    result = tomli.loads(captured.out)
    assert result
    assert result["some"]["nested"] == "data"

    plaintext = get_output_format("plaintext")
    assert plaintext
    plaintext({"some": {"nested": "data"}})
    captured = capsys.readouterr()
    assert captured.out
    assert not captured.err
