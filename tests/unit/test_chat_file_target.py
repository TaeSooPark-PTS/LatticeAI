"""Regression tests for chat file-action target/content extraction.

Two caution bugs shipped in the 8.4.0 direct-write path:

1. ``file_action_target`` allowed spaces inside path segments, so
   "create a text file report.txt" resolved the target to the whole phrase
   instead of ``report.txt`` — writing a file with a nonsense name.
2. ``inline_file_action_content`` treated the bare word "text" (and a bare
   "with") as a content marker, so "create a text file report.txt" captured
   "file report.txt" as literal content instead of generating it.
"""

from latticeai.api.chat import file_action_target, inline_file_action_content


def test_target_is_single_token_not_preceding_words():
    assert file_action_target("create a text file report.txt") == "report.txt"
    assert file_action_target("make report.md with a summary of the project") == "report.md"


def test_target_preserves_paths_and_korean_commands():
    assert file_action_target("notes/no-model.txt 파일 만들어줘") == "notes/no-model.txt"
    assert file_action_target("hello.txt 파일 만들어줘. 내용은 hi") == "hello.txt"


def test_ambiguous_words_are_not_treated_as_content():
    # "text" / bare "with" must NOT become literal file content.
    assert inline_file_action_content("create a text file report.txt") is None
    assert inline_file_action_content("make report.md with a summary of the project") is None


def test_explicit_binders_still_extract_content():
    assert inline_file_action_content("hello.txt 파일 만들어줘. 내용은 hi") == "hi"
    assert inline_file_action_content("save report.txt with content Hello World") == "Hello World"
    assert inline_file_action_content("report.txt content: Hello World") == "Hello World"
    assert inline_file_action_content("report.txt content is Hello World") == "Hello World"
