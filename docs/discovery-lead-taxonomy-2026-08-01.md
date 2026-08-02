# What 443 discovery leads actually look like

Read on 2026-08-01, from the first full Eventernote sweep after every catalogue
tag carried an `eventernote_url`. The source is the sweep's own output — the
admin DM's copy block, which is the same content `/admin/discoveries` renders.

This exists because the `triage-leads` entry in WISHLIST.md was written before
anyone had seen a real sweep, and its own caveat said so: *its value is in the
specifics of what real leads look like*. These are the specifics. Nothing here
is a design decision; it is what the data says, so that the skill can be written
against the queue that exists rather than the one we imagined.

**Counts below are from reading the titles, not from running a query.** The
classifier at the end of this file produces real numbers, and where it disagrees
with a figure here, it wins.

## Scope ruling (owner, 2026-08-02)

**Two classes get catalogued: A (ticketed concert/tour) and D (radio/talk/番組
イベント). Every other class is a dismissal.**

This is recorded here rather than only in WISHLIST because it changes how this
document should be read. The seven classes below were written as a survey of
what exists; five of them are now a description of what gets waved off, and the
`DismissReason` value beside each is the button you press.

| Class | Ruling | Reason value |
|---|---|---|
| A. Ticketed concert / tour | **Catalogue** | — |
| D. Radio / talk / 番組イベント | **Catalogue** | — |
| B. Multi-performance stage run | Dismiss | `stage` |
| C. Release event / お渡し会 | Dismiss | `release` |
| E. Festival | Dismiss | `festival` |
| F. Fan meeting / birthday | Dismiss | `fanmeet` |
| G. Free public appearance | Dismiss, permanently | `free` |
| A real live not worth tracking | Dismiss | `live` |

Three consequences worth stating, because each removes work the sections below
still describe as open:

- **The classify pass is binary.** It no longer needs a judgment per class about
  whether and how to catalogue it. Keep A and D; dismiss the rest with a reason.
- **A/B casts is descoped by consequence.** The gap named below exists only
  inside class B, and ミュージカル信長 is the sole production among all 443 leads
  that has a cast split. It is filed at WISHLIST #9, not solved.
- **The per-tag "concerts only" preference is no longer needed to cut the
  queue.** The classifier now does that for every tag at once. It survives as an
  entry only for the narrower case the ruling does not cover -- an artist whose
  talk shows a particular user does not want.

Roughly a third of the 443 survive the ruling, and the title-stem collapse below
takes that third to something on the order of fifty productions.

## The headline: 443 leads is not 443 things

The single most useful property of this queue is that a large fraction of it is
**one production surfacing many times**. Grouping by title stem — before any
research, any fetching, any judgment — collapses the queue to something on the
order of **120-150 distinct productions**.

| Production | Leads |
|---|---|
| スクールアイドルミュージカル | 13 (8 days x 本編公演 / 文化祭＆後夜祭 variants) |
| ねお・りーでぃんぐ『スライム倒して300年』 | 10 |
| ミュージカル信長〜朧炎ノ刻〜 | 9 (with A/B casts) |
| 『グノーシア ザ・ライブプレイングシアター』 | 9 |
| 朗読劇「ネコたん！肆〜猫町怪異奇譚〜」 | 9 |
| 学園アイドルマスター LIVE TOUR -標- | 8 (a genuine 4-city tour) |
| 無情報　本公演vol.11 | 8 |
| 伊波杏樹 LIVE TOUR 2026 | 7 |
| 花岩香奈 1st写真集発売記念イベント | 7 (第1部〜第7部, one venue, one day) |
| 『Liella!と結ぶプロジェクト』お渡し会 | 11 (one per member, same day, same venue) |

Two different mechanisms produce this, and they want opposite treatment:

- **A tour or a run.** 学園アイドルマスター LIVE TOUR is ONE concert with eight
  legs. This is exactly the grouping the DM's copy block already asks for, and
  the app models it natively.
- **A per-member or per-part split.** 『Liella!と結ぶプロジェクト』お渡し会 is
  eleven Eventernote events because each member gets her own slot at the same
  venue on the same day. These are not legs of one tour; they are one event the
  catalogue would record once, if at all.

A skill that groups purely on title stem gets the first right and the second
wrong. Both need to collapse, but only the first becomes a multi-leg concert.

## The seven classes

### A. Ticketed concert or tour — FITS

学園アイドルマスター LIVE TOUR -標-, 伊波杏樹 LIVE TOUR 2026, LoveLive! Series
15th Anniversary ラブライブ！フェス (バンテリンドーム), 蓮ノ空 Link Live Dream
(日本武道館 x3, 石川県産業展示館 x2), Liella! 結女体育祭, ≒JOY 全国ツアー2026,
i☆Ris 14th Anniversary Live, ブシロード20周年記念ライブ, 斉藤朱夏Birthday Tour
朱演, 菅叶和 NEW BORN LIVE, 来栖りんワンマンライブ, スタァライト九九組
オーケストラライブ.

What the app was built for: real lottery ladders, real legs, real deadlines.
**Roughly 15-20% of the queue.** This is the part that pays for the feature.

### B. Multi-performance stage run — FITS STRUCTURALLY, STRESSES THE MODEL

朗読劇, ミュージカル, 舞台, リーディングシアター, プレイングシアター. Large —
plausibly the biggest single class by lead count, because a run of nine
performances is nine leads.

It fits: performances are legs, and the tickets really do have 先行 (pre-sale
lottery) and 一般発売 rounds. But nothing in the catalogue has ever been this
shape, and two properties are new:

- **Thirteen legs on one concert.** スクールアイドルミュージカル runs 本編公演
  and 文化祭＆後夜祭スペシャル公演 on the same days. Every surface that renders
  legs — the board ladder, Coming up's per-concert block, the concert page's
  per-leg folds — was designed against two-to-four.
- **A/B casts.** See the gaps section; this is the one true model gap.

### C. Release event, お渡し会, 特典会, 写真集 — MOSTLY DOES NOT FIT

『Liella!と結ぶプロジェクト』ミニアルバム発売記念お渡し会 (11), 大西亜玖璃
9thシングル発売記念イベント (7), 花岩香奈 1st写真集発売記念イベント (7),
七瀬つむぎ1st写真集発売記念イベント (4, plus a 2ショット写メ会), ≒JOY
サイン入りソロジャケットお渡し会, ≒JOYメンバー個別撮影会, コミックマーケット108
お渡し会.

There is no lottery with a deadline to remind anyone about: you buy the product
and the slot arrives with it. The app has nothing to say about these, and
`dismissed_at` is the right outcome.

**Signal worth knowing: the `!_` venue prefix.** Eventernote writes
`!_東京都内某所` ("somewhere in Tokyo") when the venue is disclosed only to
attendees, and it tracks this class closely. It is not a perfect rule — a few
cruises and fan events use it too — but as a first-pass filter it is the
strongest single feature in the data.

**The exception that proves it needs care:** 【当選者限定】花岩香奈
1st写真集発売記念イベント. "Winners only" means there *was* a lottery, with a
deadline, that somebody had to enter. A blanket dismiss-on-keyword rule loses
exactly the ones in this class that mattered.

### D. Radio, talk, 番組イベント — FITS, LOW STAKES

The YATTEKURU series alone appears ~15 times across artists.
岬なこのそんなこんなこラジオ, あぐのんる〜むらぼ, 音泉 events, みみぺこ,
はないわーるど, なっチャンネル, サシバナ.

Genuinely ticketed, usually plain general sale, occasionally an FC lottery. It
fits the model without difficulty. The open question is not "can we" but
"do we want to" — this class alone could double the catalogue.

### E. Festival / multi-artist bill — FITS, SEMANTICALLY AWKWARD

TOKYO IDOL FESTIVAL 2026, @JAM EXPO 2026, ANIMAX MUSIX 2026, LuckyFes'26,
ANISAMA MALAYSIA 2026, AniCore 2026, ABEMAアニメ祭, 音MABUSHI2026, NAGANO
ANIERA FESTA, KIMCHIKURA Fes '26.

The concert is the *festival*, not the artist — but the lead arrives via one
artist, and the bill may run to dozens of performers across two days. Ticketing
is its own shape (day tickets, two-day passes, blocks). Catalogueable, but the
tag-attachment question is different from every other class.

### F. Fan meeting and birthday event — FITS WELL

伊達さゆり Fan Meeting Tour 2026 (東京/福岡, 昼夜), 伊波杏樹 Asia Fan Meeting
Tour 台北公演 (overseas — the tour-package round kind earns its keep), 陽高真白
FANMEETING, AIKA BIRTHDAY 2026, 法元明菜 バースデーイベント, Homoto Akina Hong
Kong Fan Meeting, 櫻井陽菜ファンクラブイベント.

FC lotteries are the norm here, which is the app's core case.

### G. Free public appearance — DOES NOT FIT AT ALL

音泉祭り2026山口 餅まき (mochi-throwing at a department store), 佐々木琴子
1日駅長就任式 at JR山口駅, 令和8年 神田明神納涼祭り アニソン盆踊り, 第69回
オールスター競輪GⅠ トークショー at 松山競輪場, 京都国際マンガ・アニメフェア
スペシャルステージ, ゲーマーズ30周年応援隊お披露目イベント.

No ticket, no deadline, nothing to remind. Dismiss without research.

### The oddity bucket

ほーみんと沖縄旅2026 〜FIRST TRIP〜 (a two-day trip to Okinawa with the talent),
田中ちえ美BIRTHDAY EVENT しあわせ航路【サンセットクルーズ】/【ディナークルーズ】,
内田秀 声優デビュー10周年記念クルーズ, 超吟醸祭2026〜秋の吟醸酒を味わう会〜,
BAR恵海人in長野, POKER CHASE FESTIVAL, パ・リーグ×ラブライブ！ オリックス・
バファローズ (a baseball game with a live attached).

These *are* ticketed and *do* run lotteries, often expensive FC ones. They fit
the machinery while being nothing like a concert. Worth a ruling rather than a
guess, because the machinery working is not the same as the vocabulary fitting.

## Two gaps that are real work

### 1. A/B casts have nowhere to live

ミュージカル信長 runs `9月19日17:30公演(A)` and `9月19日12:30公演(B)` — same
day, same venue, different cast. Nothing in the schema carries this.

Today they would become two legs distinguished only by their free-text labels,
which renders acceptably but breaks the thing that matters: a user who won an
(A) ticket cannot say which one they hold. Per-leg outcome truth
(`RoundOutcomeDay`) is per *performance*, and here the performance identity is
the cast, not the time.

This is the one genuine model gap the data surfaced. It needs its own design
pass; do not let a skill paper over it with label conventions.

### 2. A dismissal records no reason

`DiscoveredEvent` has `dismissed_at` and nothing beside it. Triage 250 leads as
"release event" and that judgment evaporates the moment it is made — the next
sweep cannot learn from it, and neither can the skill.

A reason column turns triage into training data: the classifier below stops
being a guess and becomes a thing measured against recorded human decisions.
Cheap, and it compounds with every sweep.

### And a softer one

A per-tag "concerts only" preference would cut this queue by roughly a third on
its own, by suppressing class B for artists whose stage work the owner does not
follow. Worth deciding *before* writing a skill that assumes every lead gets
triaged by a human.

## What this means for `triage-leads`

The entry described a single research pass: find the ticket page, extract the
rounds, group the legs, write the titles. That pass cannot be the whole skill,
for two independent reasons this data makes concrete.

**Volume.** 443 leads at even two minutes each is over thirteen hours. The
research pass has to run on what survives filtering, not on the queue.

**No reject path.** Classes C and G — plausibly half the queue — should never
become concerts. The entry has no vocabulary for saying so, and the app's own
`DiscoveredEvent` docstring already does: a lead says *"this exists and you are
not tracking it"* and nothing more.

So the shape is at least three passes, cheapest first:

1. **Collapse** by title stem. Mechanical, no network, and the largest single
   reduction available. Distinguish tour-legs (become one multi-leg concert)
   from per-member splits (become one event, or none).
2. **Classify and dismiss.** Title-driven, no network. Classes C and G out,
   with the 【当選者限定】 exception handled rather than ignored.
3. **Research** what survives. The expensive pass, and the only one that needs
   the official ticket page.

`import_commit` remains the only write path into `concerts`, and the commit
stays manual. None of the above changes that.

## The classifier

First-draft rules derived from reading these titles, matched top-down so
specific patterns win. The `?? unmatched` bucket is the interesting output —
that is where a class nobody has named yet lives.

```python
import sqlite3, re, collections
RULES = [
 ("G free/public",   r"餅まき|盆踊り|駅長|お披露目|マンガ・アニメフェア|アニメ文化祭"),
 ("C release/渡し会", r"発売記念|お渡し会|特典会|リリースイベント|写メ会|サイン会|撮影会|写真集|グッズ発売"),
 ("B stage run",     r"朗読劇|ミュージカル|リーディング|演劇|舞台|プレイングシアター|りーでぃんぐ"),
 ("E festival",      r"FES|FESTIVAL|フェス|EXPO|MUSIX|ANISAMA|祭り|サミット"),
 ("F fanmeet/bday",  r"FAN ?MEETING|ファンミーティング|BIRTHDAY|バースデー|生誕"),
 ("D talk/radio",    r"YATTEKURU|ラジオ|番組イベント|トークショー|公開収録|イベント"),
 ("A live/concert",  r"LIVE|ライブ|TOUR|ツアー|公演|コンサート"),
]
rows = sqlite3.connect("app.db").execute(
  "SELECT title, venue FROM discovered_events WHERE dismissed_at IS NULL").fetchall()
c, undisclosed = collections.Counter(), 0
for title, venue in rows:
    if venue.startswith("!_"): undisclosed += 1
    for name, pat in RULES:
        if re.search(pat, title, re.I): c[name] += 1; break
    else: c["?? unmatched"] += 1
for k, v in c.most_common(): print(f"{v:5d}  {k}")
print(f"\n{len(rows)} open leads, {undisclosed} with an undisclosed (!_) venue")
```

Note the ordering carefully: `公演` appears in tour legs (福岡公演) *and* in
stage performances (9月19日17:30公演(A)), so the stage rule must precede the
concert rule. Likewise `イベント` is broad enough to swallow most of the queue
and sits second-to-last deliberately.

## One thing to verify

The lead sources visible in this sweep include the Gakumas *seiyuu* (長月あおい,
飯田ヒカル, 陽高真白, 春咲暖, 湊みや, 伊藤舞音, 七瀬つむぎ, 花岩香奈, 川村玲奈,
小鹿なお, 天音ゆかり) and the group tag 学園アイドルマスター — but no CHARACTER
tag appears as a source. Either this sweep predates the reformat import, or the
six characters' actor pages carried nothing their seiyuu's pages did not.

Worth confirming, because "the character URLs are being walked" is an assumption
the whole character-discovery story rests on, and it has not yet been observed
to be true in production.
