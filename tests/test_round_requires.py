"""Taxonomy tests for the GOODS_SALE round kind: the new enum member,
ITEM_SALE_KINDS, ingest keyword mapping, and label/emoji coverage."""

from app.domain.ingest import _guess_kind
from app.domain.types import ITEM_SALE_KINDS, RoundKind


def test_goods_sale_is_a_round_kind():
    assert RoundKind.GOODS_SALE.value == "goods_sale"


def test_item_sale_kinds_are_the_two_item_kinds():
    assert ITEM_SALE_KINDS == {RoundKind.ELIGIBILITY_ITEM_SALE, RoundKind.GOODS_SALE}


def test_guess_kind_maps_goods_keywords():
    assert _guess_kind("グッズ販売") is RoundKind.GOODS_SALE
    assert _guess_kind("Tour Goods Pre-order") is RoundKind.GOODS_SALE
    assert _guess_kind("会場物販") is RoundKind.GOODS_SALE
    # The serial-code sale stays what it was:
    assert _guess_kind("シリアル対象CD発売") is not RoundKind.GOODS_SALE


def test_label_and_emoji_cover_every_kind():
    from app.bot.messages import KIND_EMOJI
    from app.db.service import LABEL_BY_ROUND_KIND

    for kind in RoundKind:
        assert kind in LABEL_BY_ROUND_KIND
        assert kind.value in KIND_EMOJI
