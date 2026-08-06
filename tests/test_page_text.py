"""HTML in, the one text the model reads and evidence is checked against.

The property that matters is not prettiness: it is that ONE function produces
the text both the prompt and the verifier see. A page that reaches the model
one way and the verifier another turns the evidence rule into theatre.
"""

from app.domain.page_text import PAGE_TEXT_CAP, collapse, html_to_text, normalize_page_text


def test_script_and_style_contents_never_reach_the_text():
    html = """
    <html><head><style>.a{color:red}</style><script>var x = "先行抽選";</script></head>
    <body><p>1次先行抽選 申込締切 2026年1月10日(土)23:59</p></body></html>
    """
    text = html_to_text(html)
    assert "申込締切 2026年1月10日(土)23:59" in text
    assert "color:red" not in text
    assert "var x" not in text


def test_block_elements_are_separated_so_two_lines_do_not_fuse():
    # Without a separator, <td>23:59</td><td>受付終了</td> becomes "23:59受付終了",
    # and a quote of either half then fails to match the page it came from.
    text = html_to_text("<table><tr><td>23:59</td><td>受付終了</td></tr></table>")
    assert "23:59 受付終了" in text


def test_runs_of_whitespace_collapse_to_single_spaces():
    assert collapse("a  \n\t b　c") == "a b c"


def test_text_is_capped():
    text = normalize_page_text("あ" * (PAGE_TEXT_CAP + 500))
    assert len(text) == PAGE_TEXT_CAP


def test_normalize_is_idempotent():
    once = normalize_page_text("  a \n b  ")
    assert normalize_page_text(once) == once


def test_normalize_is_idempotent_when_the_cut_lands_on_a_space():
    # Engineer a string whose cut at PAGE_TEXT_CAP falls right after a space:
    # collapse() would have stripped that space had it been the end of the
    # string, so truncation must not leave it behind for a second pass to trim.
    text = "a" * (PAGE_TEXT_CAP - 1) + " " + "b" * 500
    once = normalize_page_text(text)
    assert once.endswith(" ") is False
    assert normalize_page_text(once) == once
