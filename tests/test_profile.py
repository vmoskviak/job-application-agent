import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.scrapers.profile import PROFILE_DIR, profile_exists


def test_profile_dir_matches_mcp_config():
    assert PROFILE_DIR.name == "erika"
    assert PROFILE_DIR.parent.name == ".playwright-mcp"


def test_profile_exists_returns_bool():
    assert isinstance(profile_exists(), bool)
