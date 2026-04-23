from pathlib import Path

from rcc.ignore import GitignoreMatcher


def make(patterns):
    return GitignoreMatcher(patterns)


def test_directory_pattern_matches_dir_and_contents():
    matcher = make([".git/"])
    assert matcher.match(Path(".git"), is_dir=True)
    assert matcher.match(Path(".git/config"), is_dir=False)


def test_directory_pattern_does_not_match_like_named_file():
    matcher = make([".git/"])
    assert not matcher.match(Path("gitfile.git"), is_dir=False)


def test_glob_pattern():
    matcher = make(["*.pyc"])
    assert matcher.match(Path("a.pyc"), is_dir=False)
    assert matcher.match(Path("pkg/a.pyc"), is_dir=False)
    assert not matcher.match(Path("a.py"), is_dir=False)


def test_root_anchored_pattern():
    matcher = make(["/build"])
    assert matcher.match(Path("build"), is_dir=True)
    assert not matcher.match(Path("pkg/build"), is_dir=True)


def test_negation():
    matcher = make(["*.log", "!keep.log"])
    assert matcher.match(Path("debug.log"), is_dir=False)
    assert not matcher.match(Path("keep.log"), is_dir=False)


def test_blank_and_comment_lines_ignored():
    matcher = make(["", "# comment", "*.pyc"])
    assert matcher.match(Path("a.pyc"), is_dir=False)


def test_load_from_file(tmp_path):
    ignore = tmp_path / "ignore"
    ignore.write_text(".git/\n# c\n*.pyc\n")
    matcher = GitignoreMatcher.from_file(ignore)
    assert matcher.match(Path(".git/config"), is_dir=False)
    assert matcher.match(Path("a.pyc"), is_dir=False)
