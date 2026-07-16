from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_idtracker_launcher_is_headless():
    script = (ROOT / "slurm" / "idtracker_one_cell.slurm").read_text(
        encoding="utf-8"
    )
    assert 'idtrackerai --track --load "$PIPELINE_TOML"' in script
    assert 'idtrackerai --load "$PIPELINE_TOML"' not in script


def test_quick_installer_does_not_update_conda():
    script = (ROOT / "scripts" / "firebird" / "quick_install.sh").read_text(
        encoding="utf-8"
    )
    assert "conda env update" not in script
    assert "conda env create" not in script
