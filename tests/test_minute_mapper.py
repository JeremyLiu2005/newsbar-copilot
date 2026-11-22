"""spec §2, §7 — T01–T04, T16–T18, T22, T24."""

import pytest

from newsbar.minute_mapper import ContractError, map_events, minute_labels
from newsbar.schemas import Event, EventType as T

CH = ["tvbs", "ettv", "ftv", "cts"]


def values(cells, ch):
    return {m: c.value for m, c in cells[ch].items()}


def test_T01_continuation_is_blank():
    evs = [Event("tvbs", 10, T.NEWS, "土城翁輾亡"), Event("tvbs", 200, T.NEWS, "土城翁輾亡"),
           Event("tvbs", 330, T.NEWS, "高金遭起訴")]
    v = values(map_events(evs, CH), "tvbs")
    assert v == {"1200": "土城翁輾亡", "1205": "高金遭起訴"}


def test_T02_same_minute_joined_in_time_order():
    evs = [Event("tvbs", 28 * 60 + 40, T.NEWS, "台股二補跌"), Event("tvbs", 28 * 60 + 5, T.NEWS, "持刀警開槍")]
    assert values(map_events(evs, CH), "tvbs") == {"1228": "持刀警開槍/台股二補跌"}


def test_T03_label_rewritten_after_ad_even_for_same_story():
    evs = [Event("cts", 30 * 60, T.NEWS, "妙慘遭排擠", "s1"),
           Event("cts", 32 * 60 + 5, T.AD),
           Event("cts", 35 * 60 + 2, T.NEWS, "妙慘遭排擠", "s1")]
    assert values(map_events(evs, CH), "cts") == {"1230": "妙慘遭排擠", "1232": "廣告", "1235": "妙慘遭排擠"}


def test_T04_ad_continuation_written_once():
    evs = [Event("ftv", 40 * 60, T.AD), Event("ftv", 41 * 60 + 30, T.AD)]
    assert values(map_events(evs, CH), "ftv") == {"1240": "廣告"}


def test_status_words():
    evs = [Event("cts", 50 * 60, T.WEATHER), Event("cts", 52 * 60, T.OUTAGE)]
    assert values(map_events(evs, CH), "cts") == {"1250": "氣象", "1252": "訊號中斷"}


def test_T16_floor_to_minute():
    evs = [Event("tvbs", 3 * 60 + 59.8, T.NEWS, "談判砍男傷")]
    assert values(map_events(evs, CH), "tvbs") == {"1203": "談判砍男傷"}


def test_T17_carry_in_written_at_first_minute():
    evs = [Event("tvbs", -120, T.NEWS, "阿美麻糬亡"), Event("tvbs", 125, T.NEWS, "追阿美麻糬")]
    assert values(map_events(evs, CH), "tvbs") == {"1200": "阿美麻糬亡", "1202": "追阿美麻糬"}


def test_T17b_carry_in_off():
    evs = [Event("tvbs", -120, T.NEWS, "阿美麻糬亡")]
    assert values(map_events(evs, CH, carry_in=False), "tvbs") == {}


def test_T18_after_end_not_written():
    evs = [Event("tvbs", 3605, T.NEWS, "周星馳女足")]
    assert values(map_events(evs, CH), "tvbs") == {}


def test_T22_four_channels_same_minute_independent():
    evs = [Event(ch, 5 * 60 + i, T.NEWS, lbl) for i, (ch, lbl) in
           enumerate(zip(CH, ["談判砍男傷", "阿美麻糬亡", "鐵皮壓電纜", "攻擊站長室"]))]
    cells = map_events(evs, CH)
    assert [cells[ch]["1205"].value for ch in CH] == ["談判砍男傷", "阿美麻糬亡", "鐵皮壓電纜", "攻擊站長室"]


@pytest.mark.parametrize("bad", ["", " 土城翁 ", "甲/乙", "廣告"])
def test_invalid_news_labels_rejected(bad):
    with pytest.raises((ContractError, ValueError)):
        map_events([Event("tvbs", 0, T.NEWS, bad or None)], CH)


def test_unknown_channel_rejected():
    with pytest.raises(ContractError):
        map_events([Event("sanlih", 0, T.AD)], CH)


def test_minute_labels_roll_over_the_hour():
    assert minute_labels("1258", 4) == ["1258", "1259", "1300", "1301"]


# T24: the real 10/26 TVBS-N column, round-tripped.
TVBS_1026 = {
    "1200": "阿美麻糬亡", "1202": "追阿美麻糬", "1205": "談判砍男傷", "1208": "追打害溺斃",
    "1210": "萬聖節亂丟", "1212": "多慧山宿罰", "1214": "沈玉琳奪獎", "1216": "熱炒店勝出",
    "1218": "毒駕撞分隔", "1221": "蔣迫學生掃", "1223": "沈蔣悄備辯", "1226": "酸春米沒生",
    "1228": "持刀警開槍/台股二補跌", "1230": "Youbike椅太高", "1232": "唐嘉鴻奪金",
    "1234": "MUSIC EXPO", "1236": "廣告", "1239": "巧川戰新北", "1241": "館長談統一",
    "1243": "美烏俄和談", "1245": "機車水上漂", "1247": "廣告", "1250": "薔琵強襲日",
    "1252": "彩帶害跳電", "1254": "廣告", "1256": "追打害溺斃", "1258": "周星馳女足",
}


def events_from_column(col: dict[str, str], channel: str) -> list[Event]:
    evs = []
    for minute, value in col.items():
        base = (int(minute[:2]) - 12) * 3600 + int(minute[2:]) * 60
        for j, item in enumerate(value.split("/")):
            t = base + 5 + j * 30
            if item == "廣告":
                evs.append(Event(channel, t, T.AD))
            else:
                evs.append(Event(channel, t, T.NEWS, item, f"{item}@{minute}"))
    return evs


def test_T24_round_trip_real_example_column():
    cells = map_events(events_from_column(TVBS_1026, "tvbs"), CH)
    assert values(cells, "tvbs") == TVBS_1026


# ---- minute_rule = "next"（三立 11/22：秒級換題從下一分鐘起寫，一格一則） ----------------------

def test_next_rule_mid_minute_change_goes_to_next_minute():
    evs = [Event("tvbs", 3 * 60 + 40, T.NEWS, "談判砍男傷")]
    assert values(map_events(evs, CH, minute_rule="next"), "tvbs") == {"1204": "談判砍男傷"}


def test_next_rule_change_on_the_minute_stays():
    evs = [Event("tvbs", 3 * 60, T.NEWS, "談判砍男傷")]
    assert values(map_events(evs, CH, minute_rule="next"), "tvbs") == {"1203": "談判砍男傷"}


def test_next_rule_one_entry_per_cell_last_one_wins():
    evs = [Event("tvbs", 28 * 60 + 5, T.NEWS, "持刀警開槍"), Event("tvbs", 28 * 60 + 40, T.NEWS, "台股二補跌")]
    assert values(map_events(evs, CH, minute_rule="next"), "tvbs") == {"1229": "台股二補跌"}


def test_next_rule_overwrite_back_to_same_story_is_continuation():
    evs = [Event("tvbs", 0, T.NEWS, "土城翁輾亡", "s1"), Event("tvbs", 60 + 10, T.NEWS, "高金遭起訴", "s2"),
           Event("tvbs", 60 + 50, T.NEWS, "土城翁輾亡", "s1")]
    assert values(map_events(evs, CH, minute_rule="next"), "tvbs") == {"1200": "土城翁輾亡"}


def test_next_rule_ad_and_return():
    evs = [Event("cts", 30 * 60, T.NEWS, "妙慘遭排擠", "s1"), Event("cts", 32 * 60 + 5, T.AD),
           Event("cts", 35 * 60 + 2, T.NEWS, "妙慘遭排擠", "s1")]
    assert values(map_events(evs, CH, minute_rule="next"), "cts") == {"1230": "妙慘遭排擠", "1233": "廣告", "1236": "妙慘遭排擠"}


def test_next_rule_change_in_last_minute_is_dropped():
    evs = [Event("tvbs", 59 * 60 + 30, T.NEWS, "周星馳女足")]
    assert values(map_events(evs, CH, minute_rule="next"), "tvbs") == {}


def test_next_rule_from_labels():
    from newsbar.schemas import Labels

    evs = [Event("tvbs", 3 * 60 + 40, T.NEWS, "談判砍男傷")]
    labels = Labels.from_rules({"output": {"minute_rule": "next"}})
    assert values(map_events(evs, CH, labels=labels), "tvbs") == {"1204": "談判砍男傷"}


def test_unknown_minute_rule_rejected():
    with pytest.raises(ContractError):
        map_events([Event("tvbs", 0, T.AD)], CH, minute_rule="round")


def test_enforce_max_caps_labels_at_five_units():
    from newsbar.labeler import LabelRules, enforce_max, label_length

    r = LabelRules(min_len=5, max_len=5)  # 剛好 5 字開關
    assert r.max_len == 5
    assert enforce_max("土城翁輾亡", r) == "土城翁輾亡"
    capped = enforce_max("新北土城翁輾亡", r)
    assert label_length(capped) <= 5 and capped.startswith("新北土城")
    assert label_length(enforce_max("MUSIC EXPO 開幕大秀", r)) <= 5
