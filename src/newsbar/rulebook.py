"""判斷規則總表：程式裡每一條判斷規則，用白話中文寫在這一個檔案。

用途
  * `newsbar rules`                    在終端機印出全部規則（中文）
  * `newsbar rules --markdown 檔案`     寫成 Markdown（docs/rules.md 就是這樣產生的）
  * `newsbar rules --json 檔案`         寫成 JSON，給原型或其他工具讀
  * tests/test_rulebook.py             逐一核對這裡寫的數字等於程式實際使用的預設值，
                                       也核對「程式位置」都找得到，所以文件不會悄悄與程式脫節

怎麼改
  規則的編號沿用 docs/spec.md；spec 沒有的編號是實作時補上的。
  改了程式裡的門檻，就要同步改這裡的 value，否則測試會失敗。
  每條規則的 where 是「模組.函式」，程式裡對應的判斷處也有一行「# 規則 XXX」註解。
"""

from __future__ import annotations

import importlib
import inspect
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

PKG = "newsbar"


@dataclass(frozen=True)
class Param:
    """一個可調的數字：name 是程式裡的名字，label 是中文說明，actual 回傳程式實際的預設值。"""

    name: str
    label: str
    value: Any
    unit: str
    actual: Callable[[], Any] = field(repr=False, compare=False, default=lambda: None)


@dataclass(frozen=True)
class Rule:
    id: str
    group: str  # GROUPS 的 key
    text: str  # 「條件 → 決定」
    where: tuple[str, ...]  # 程式位置，模組.函式
    params: tuple[Param, ...] = ()


GROUPS: list[tuple[str, str, str]] = [
    ("noise", "① 只讀主標題（畫面雜訊）", "畫面上有很多字：跑馬燈、角標、角落天氣、左側標、受訪字幕。只有畫面下方最大的那一行主標題算數。"),
    ("ad", "② 廣告包框", "全螢幕廣告，以及主畫面播廣告、旁邊或下方還在輪播新聞標題的「包框廣告」，都要認出來，廣告期間不沿用舊標題。"),
    ("change", "③ 換題斷點", "標題換了不一定換題，鏡位切換、LIVE 字樣也不代表換題。要有標題換新，加上語音、主播鏡頭或記者名條的佐證。"),
    ("minute", "④ 秒級切換對準分鐘", "判斷在秒上做，表格只寫分鐘；分鐘要和收視率對齊，差一分鐘就算錯。"),
    ("label", "⑤ 五字標品質", "不是硬切前五個字：濃縮人事時地物，社會新聞要有地點，不把未確認的說法寫成定論，字都要出自來源。"),
    ("fallback", "⑥ 不確定時的容錯", "任何一步沒把握，都不能卡死或整支停擺：標記、跳過、繼續，留下依據讓人複查。"),
    ("optional", "⑦ RAG 與 ML（預設關閉）", "兩個可選的加強功能，沒有打開時，上面六組規則的行為完全不受影響。"),
]


def _call_default(modname: str, qualname: str, arg: str) -> Callable[[], Any]:
    """函式簽名上某個參數的預設值。"""

    def get() -> Any:
        obj: Any = importlib.import_module(f"{PKG}.{modname}")
        for part in qualname.split("."):
            obj = getattr(obj, part)
        return inspect.signature(obj).parameters[arg].default

    return get


def _dc(modname: str, cls: str, field_name: str) -> Callable[[], Any]:
    """dataclass 欄位的預設值（不必建立物件）。"""

    def get() -> Any:
        c = getattr(importlib.import_module(f"{PKG}.{modname}"), cls)
        return c.__dataclass_fields__[field_name].default

    return get


def _src_const(relpath: str, pattern: str, cast: Callable[[str], Any] = float) -> Callable[[], Any]:
    """原始碼裡 `pattern` 抓到的第一組數字（給不方便 import 的 macOS 專用模組用）。"""

    def get() -> Any:
        text = (Path(__file__).parent / relpath).read_text(encoding="utf-8")
        m = re.search(pattern, text)
        assert m, f"{relpath} 找不到 {pattern}"
        return cast(m.group(1))

    return get


def _lex(key: str) -> Callable[[], Any]:
    def get() -> Any:
        from . import lexicon

        return list(getattr(lexicon.get(), key))

    return get


def _hedges(strong: bool) -> Callable[[], Any]:
    def get() -> Any:
        from . import lexicon

        return [m for m in lexicon.get().hedge_markers if (len(m) >= 2) == strong]

    return get


def _const(modname: str, name: str) -> Callable[[], Any]:
    def get() -> Any:
        return getattr(importlib.import_module(f"{PKG}.{modname}"), name)

    return get


def _credit_len() -> tuple[int, int]:
    from . import segmenter

    m = re.search(r"\{(\d+),(\d+)\}", segmenter._CREDIT.pattern)
    assert m
    return int(m.group(1)), int(m.group(2))


def _labels(field_name: str) -> Callable[[], Any]:
    return _dc("schemas", "Labels", field_name)


# ------------------------------------------------------------------------------------------------
# 規則本體。每一條：編號、組別、白話「條件 → 決定」、程式位置、數字。
# ------------------------------------------------------------------------------------------------
RULES: list[Rule] = [
    # ============================== ① 只讀主標題 ==============================
    Rule("TXT-1", "noise",
         "要判斷換題或寫五字標時，只看畫面下方最大的主標題條 → 其他位置的字一律不採用。",
         ("segmenter.segment", "pipeline.run_channel")),
    Rule("TXT-2", "noise",
         "最下方的快訊跑馬燈、跑馬燈旁的四字標籤、角落天氣、畫面內的小廣告、左側標 → 永遠不寫進表，也不當換題依據。",
         ("schemas.SecondObs", "segmenter.segment")),
    Rule("TXT-4", "noise",
         "同一個畫面，取前後共 5 秒內的多次 OCR 讀數，多數相同的那個當作這一秒的標題；"
         "信心值低於門檻的讀數丟掉（PaddleOCR 用 0.6；Apple Vision 的信心值只有 0.3、0.5、1.0 三級，所以門檻是 0.3；各 OCR 後端的信心值尺度不同，門檻各自設定）。",
         ("ocr.stream.vote",),
         (Param("VoteParams.window_sec", "OCR 多數決視窗", 5, "秒", _dc("ocr.stream", "VoteParams", "window_sec")),
          Param("PaddleBackend.min_conf", "OCR 信心門檻", 0.6, "信心值", _src_const("ocr/paddle_backend.py", r"min_conf\s*=\s*([0-9.]+)")),
          Param("VisionBackend.min_conf", "OCR 信心門檻", 0.3, "信心值", _src_const("ocr/vision_backend.py", r"min_conf\s*=\s*([0-9.]+)")))),
    Rule("TXT-5", "noise",
         "主標題本身是天氣（例如「各地天氣」「颱風動態」）→ 這一格寫「氣象」；角落的天氣小圖不會觸發這條。",
         ("segmenter.is_weather", "segmenter.segment"),
         (Param("weather", "天氣新聞寫在格子裡的字", "氣象", "字串", _labels("weather")),)),
    Rule("TXT-6", "noise",
         "標題條裡單獨出現的疊字（LIVE、直播、獨家、最新、角落溫度）→ 先刪掉再判斷，"
         "這樣疊字閃一下既不會變成標題的一部分，也不會被當成「標題換了」；黏在標題裡的字（例如「最新民調」）不動。詞表在 config/lexicon.yaml。",
         ("textfilter.filter_obs", "lexicon.Lexicon.strip_overlay", "pipeline.run_channel")),
    Rule("TXT-7", "noise",
         "一個讀數裡中文字少於 2 個 → 當成 OCR 雜訊，視為這一秒沒有標題（台灣新聞標題一定有中文）。",
         ("segmenter.segment",),
         (Param("MIN_CJK_CHARS", "標題至少要有的中文字數", 2, "字", _const("segmenter", "MIN_CJK_CHARS")),)),

    # ============================== ② 廣告包框 ==============================
    Rule("AD-1", "ad",
         "同一秒內，下面十種廣告訊號同時成立兩種以上，而且連續 10 秒 → 進廣告；廣告的起點取第一個訊號出現的那一秒，但最多只往前推到「連續成立」開始前 10 秒。"
         "畫面上的新聞跑馬燈還在時，要多一種訊號才算（新聞圖卡有時會遮住台標、畫出框線，跑馬燈還在代表電視台的新聞版面還開著）。",
         ("ad_state.detect",),
         (Param("min_signals", "同時成立的訊號數", 2, "種", _dc("ad_state", "AdParams", "min_signals")),
          Param("min_duration_sec", "連續成立的秒數", 10, "秒", _dc("ad_state", "AdParams", "min_duration_sec")),
          Param("TICKER_EXTRA_SIGNALS", "跑馬燈還在時多要的訊號數", 1, "種", _const("ad_state", "TICKER_EXTRA_SIGNALS")))),
    Rule("AD-2", "ad",
         "只看主畫面：角落的小廣告不會改變主畫面版面，不算訊號，不能單獨觸發進廣告。",
         ("schemas.SecondObs.ad_signals",)),
    Rule("AD-3", "ad",
         "「還看得到新聞文字」不能否決廣告：主標題或側邊推播還在，只要兩種以上訊號持續夠久，仍判成廣告（廣告包框）。",
         ("ad_state.detect",)),
    Rule("AD-5", "ad",
         "廣告期間，主標題和側邊推播的文字全部不採用：不當換題候選，也不當五字標來源，這段寫「廣告」。",
         ("segmenter.segment",),
         (Param("ad", "廣告寫在格子裡的字", "廣告", "字串", _labels("ad")),)),
    Rule("AD-6", "ad",
         "進廣告前，記下最後一個主標題讀數，叫「殘留標題」，留給回新聞時比對。",
         ("ad_state.detect",)),
    Rule("AD-a", "ad", "訊號一：主畫面版面改變（主畫面縮成側框，包框廣告的特徵）。", ("schemas.SecondObs.ad_signals", "visual")),
    Rule("AD-b", "ad", "訊號二：標題條底色和該台設定的顏色差太遠（正常藍底、包框時黃底，顏色依台別設定）。",
         ("schemas.SecondObs.ad_signals", "visual"),
         (Param("bar_color_dist", "底色色差門檻", 80.0, "RGB 距離", _dc("visual", "VisualParams", "bar_color_dist")),)),
    Rule("AD-c", "ad", "訊號三：電視台台標消失或位置改變。", ("schemas.SecondObs.ad_signals", "visual")),
    Rule("AD-d", "ad", "訊號四：黑畫面（廣告切入常見）。", ("schemas.SecondObs.ad_signals", "visual")),
    Rule("AD-e", "ad", "訊號五：語音出現品牌、價格、促銷詞，而且沒有主播或記者的講話。", ("schemas.SecondObs.ad_signals", "asr.is_ad_speech")),
    Rule("AD-f", "ad", "訊號六：聲音指紋命中已知廣告（同一支廣告會在多台、多天重播；人工確認過的廣告會自動加入指紋庫）。",
         ("schemas.SecondObs.ad_signals", "fingerprint")),
    Rule("AD-g", "ad",
         "訊號七：新聞跑馬燈不見了。只有「這段影片裡超過一半的時間都有跑馬燈」的頻道才使用這個訊號。",
         ("ad_state.signal_fn", "schemas.SecondObs.ad_signals")),
    Rule("AD-h", "ad",
         "訊號八：標題條的位置出現廣告文案（電話號碼、0800，或「廣告後」「立即回到」「稍後回來」「馬上回來」「休息一下」這類「待會回來」的預告）。"
         "標題本身常有的「折」「優惠」「買一送一」不算，避免旅展新聞被誤判成廣告。",
         ("schemas.is_ad_copy", "schemas.SecondObs.ad_signals")),
    Rule("AD-i", "ad",
         "訊號九：標題輪播。120 秒內，至少 6 個不同標題各只停 7 到 12 秒，而且其中有標題重複出現 → 這是廣告包框裡的新聞預告輪播，"
         "整段輪播秒數算 1 個訊號，並且在計分時重複算一次（等於兩個）。正常新聞一則最多輪 2 到 4 個標題，預告輪播會到 10 個。",
         ("ad_state.carousel_seconds", "ad_state.signal_fn"),
         (Param("stable_sec", "輪播中每個標題至少停的秒數", 7, "秒", _dc("segmenter", "SegParams", "stable_sec")),
          Param("carousel_max_sec", "輪播中每個標題最多停的秒數", 12, "秒", _dc("segmenter", "SegParams", "carousel_max_sec")),
          Param("carousel_window_sec", "輪播偵測的時間視窗", 120, "秒", _dc("segmenter", "SegParams", "carousel_window_sec")),
          Param("carousel_min_titles", "輪播至少要有的不同標題數", 6, "個", _dc("segmenter", "SegParams", "carousel_min_titles")))),
    Rule("AD-j", "ad", "訊號十：頻道的時鐘消失（各台新聞播出時時鐘都在，全螢幕廣告會蓋掉它）。", ("schemas.SecondObs.ad_signals", "clock")),
    Rule("AD-7", "ad",
         "回新聞：下面條件在同一秒全部成立，而且連續維持 5 秒 → 廣告結束，廣告的終點是這 5 秒的第一秒。"
         "（1）主畫面恢復全畫面（2）標題條底色恢復（3）有主播或記者的講話，不是廣告詞（4）標題條出現和殘留標題不同的新標題，或殘留標題所屬的同一則被語音確認；"
         "（5）此時廣告訊號不能還有兩種以上。該頻道完全沒有語音時，只看版面與底色，標題條有讀數就算。",
         ("ad_state.detect", "ad_state._return_ok"),
         (Param("return_confirm_sec", "回新聞要連續維持的秒數", 5, "秒", _dc("ad_state", "AdParams", "return_confirm_sec")),)),
    Rule("AD-8", "ad",
         "回來的標題還是殘留標題，而且語音確認是同一則 → 這一分鐘重寫「廣告前那則」的五字標（回新聞的那一分鐘一定要寫字，不能留白）；否則當成新的一則重新命名。",
         ("segmenter.segment",)),
    Rule("AD-9", "ad",
         "主畫面已經恢復，但上面的（3）（4）遲遲不成立，超過 60 秒 → 強制判定回新聞，這一格改用語音替這則新聞命名，並標記低信心。",
         ("ad_state.detect", "segmenter.segment"),
         (Param("return_timeout_sec", "回新聞等待上限", 60, "秒", _dc("ad_state", "AdParams", "return_timeout_sec")),)),
    Rule("AD-10", "ad",
         "兩段廣告之間只隔 8 秒以內 → 當成同一段廣告（例如華視 12:47 到 12:48 一段廣告被切成 6 小段）。",
         ("ad_state.detect",),
         (Param("merge_gap_sec", "合併相鄰廣告的間隔上限", 8, "秒", _dc("ad_state", "AdParams", "merge_gap_sec")),)),
    Rule("AD-11", "ad",
         "整段「廣告」不到 25 秒 → 不算廣告，改回新聞（真正的廣告時段都超過 30 秒；12:00 整點那 13 秒的節目片頭不是廣告）。",
         ("ad_state.detect",),
         (Param("min_break_sec", "一段廣告至少要有的秒數", 25, "秒", _dc("ad_state", "AdParams", "min_break_sec")),)),

    # ============================== ③ 換題斷點 ==============================
    Rule("NEW-1", "change",
         "主標題出現新的字，而且不屬於「同一則的輪播或改寫」→ 這是換題的候選點。",
         ("segmenter.segment",)),
    Rule("SAME-3", "change",
         "新標題和目前這則的任何一個標題，共有連續兩個中文字（人名、地名都算，「新北」「台北」「今天」「記者」「現場」之類的泛稱不算）→ 視為同一則內主標與副標輪播，不換題。",
         ("segmenter.LexicalJudge.same", "segmenter.segment"),
         (Param("LexicalJudge.min_common", "共同詞至少幾個字", 2, "字", _call_default("segmenter", "LexicalJudge.__init__", "min_common")),)),
    Rule("NEW-2a", "change", "佐證一：候選點前後各 20 秒內，語音談的主題明顯不同。", ("segmenter.segment",),
         (Param("asr_window_sec", "語音主題比對視窗", 20, "秒", _dc("segmenter", "SegParams", "asr_window_sec")),)),
    Rule("NEW-2b", "change", "佐證二：候選點前後各 5 秒內，畫面切回主播台。", ("segmenter.segment",),
         (Param("visual_window_sec", "畫面佐證視窗", 5, "秒", _dc("segmenter", "SegParams", "visual_window_sec")),)),
    Rule("NEW-2c", "change",
         "佐證三：候選點前後各 5 秒內，講話的人換了。這一項只記錄、不能單獨算數（同一則新聞裡受訪者本來就會輪流講話）。",
         ("segmenter.segment",)),
    Rule("NEW-2d", "change",
         "佐證四：記者名條只閃 5 秒以內，而且之後 25 秒內開始了新標題 → 名條是換題的預告。",
         ("segmenter.segment", "segmenter.is_credit"),
         (Param("credit_max_sec", "名條最長停留", 5, "秒", _dc("segmenter", "SegParams", "credit_max_sec")),
          Param("credit_window_sec", "名條之後多久內的新標題算新題", 25, "秒", _dc("segmenter", "SegParams", "credit_window_sec")))),
    Rule("SAME-6", "change",
         "整段有語音時，新標題沒有上面任何一項佐證（語音主題沒變、沒有主播鏡頭、沒有名條）→ 留在同一則，不換題（避免同一則新聞輪播不同標題被切成好幾則）。",
         ("segmenter.segment",),
         (Param("asr_veto", "有語音時要求佐證", True, "開關", _dc("segmenter", "SegParams", "asr_veto")),)),
    Rule("SAME-7", "change",
         "標題只有 2 到 12 個中文字、沒有標點與數字、不是天氣字樣、也沒有「的了是我你他」這類人名裡不會有的字（記者名條），或以「主播」「記者」開頭 → 是名條，不是新的一則，只當換題的線索。",
         ("segmenter.is_credit", "segmenter.segment"),
         (Param("_CREDIT 字數下限", "名條最少字數", 2, "字", lambda: _credit_len()[0]),
          Param("_CREDIT 字數上限", "名條最多字數", 12, "字", lambda: _credit_len()[1]))),
    Rule("SAME-8", "change",
         "一個標題在 180 秒內回來了 → 中間夾的那些段落合併成同一則（例如法國罷工：鐵塔關閉→封路→物價→鐵塔關閉）。",
         ("segmenter.segment",),
         (Param("recur_window_sec", "標題回來的時間視窗", 180, "秒", _dc("segmenter", "SegParams", "recur_window_sec")),)),
    Rule("BAN-1", "change",
         "LIVE、直播、連線字樣出現或消失，不能當換題或同一則的依據（先由疊字過濾刪掉）。",
         ("textfilter.filter_obs", "segmenter.segment")),
    Rule("BAN-2", "change",
         "單純的鏡位切換（棚內轉現場、主播轉 SOT、假連線）不能單獨觸發換題。",
         ("segmenter.segment",)),
    Rule("CAROUSEL", "change",
         "標題輪播（定義見廣告訊號九）發生的那段時間，標題輪來輪去不算換題。",
         ("segmenter.segment", "ad_state.carousel_seconds")),
    Rule("NEW-3", "change",
         "廣告前不到 20 秒的「新聞」，或整段從沒讀到標題的「新聞」→ 其實是廣告文案造成的假新聞，併入廣告。",
         ("segmenter.segment",),
         (Param("min_story_before_ad_sec", "廣告前一則新聞至少要有的秒數", 20, "秒", _dc("segmenter", "SegParams", "min_story_before_ad_sec")),)),

    # ============================== ④ 秒級切換對準分鐘 ==============================
    Rule("TIME-1", "minute",
         "換題的時間 = 新標題第一次穩定出現的那一秒，穩定指連續 7 秒讀到同一個標題（11/06 實測：標題一般停 7 到 45 秒，字幕與名條只停 1 到 6 秒）。"
         "即使主播的語音比標題早開口，仍以標題為準。",
         ("segmenter.segment",),
         (Param("stable_sec", "標題穩定秒數", 7, "秒", _dc("segmenter", "SegParams", "stable_sec")),)),
    Rule("MAP-3", "minute",
         "秒級換題寫在哪一格由設定檔 output.minute_rule 決定。next（11/06 設定檔採用，依三立 11/22 說明）："
         "不是整分 00 秒的改變從下一分鐘開始寫，12:03:40 改變 → 寫在 1204；一格只放一則，同一分鐘內的多次改變只留那一分鐘開始時在播的。"
         "floor（程式內建預設，10/26 範例寫法）：捨去到所在分鐘，12:03:59 改變 → 寫在 1203。",
         ("minute_mapper.map_events",),
         (Param("Labels.minute_rule", "秒級換題的寫法（程式內建預設）", "floor", "", _labels("minute_rule")),)),
    Rule("OUT-2", "minute", "（minute_rule＝floor 時）某分鐘內發生狀態改變 → 在這一分鐘寫入新狀態；同一分鐘有好幾次改變 → 依先後用斜線連起來（例：持刀警開槍/台股二補跌）。minute_rule＝next 時一格只放一則，不會出現斜線。",
         ("minute_mapper.map_events",),
         (Param("Labels.multi_sep", "同分鐘多則的分隔字元", "/", "字元", _labels("multi_sep")),)),
    Rule("OUT-1", "minute",
         "空白 = 前一個有字的格子所代表的狀態還在持續（同一則新聞延續、廣告還沒結束），空白不是「沒資料」。",
         ("minute_mapper.map_events",)),
    Rule("OUT-4", "minute", "廣告只寫「廣告」兩個字，進廣告的那一分鐘寫、廣告持續中留白；不寫「廣告 2」「30秒廣告」。",
         ("minute_mapper.map_events",),
         (Param("ad", "廣告的寫法", "廣告", "字串", _labels("ad")),)),
    Rule("OUT-6", "minute",
         "從廣告或訊號中斷回到新聞，回來的那一分鐘一定寫五字標，即使和廣告前是同一則（否則留白會被讀成「還在廣告」）。",
         ("minute_mapper.map_events", "segmenter.segment")),
    Rule("MAP-5", "minute", "連續兩次相同狀態（同一則新聞、或都是廣告）視為延續，不重複寫。",
         ("minute_mapper.map_events",)),
    Rule("MAP-6", "minute", "表格開始之前就在進行的狀態（例如 11:58 開始的一則新聞），寫在第一格 1200。",
         ("minute_mapper.map_events",),
         (Param("map_events(start=)", "表格第一分鐘", "1200", "時分", _call_default("minute_mapper", "map_events", "start")),
          Param("map_events(carry_in=)", "開頭前已在進行的狀態是否寫入第一格", True, "開關", _call_default("minute_mapper", "map_events", "carry_in")))),
    Rule("MAP-7", "minute", "表格結束之後（13:00:00 起）發生的改變不寫入。",
         ("minute_mapper.map_events",),
         (Param("map_events(n_minutes=)", "表格分鐘數", 60, "分鐘", _call_default("minute_mapper", "map_events", "n_minutes")),)),
    Rule("MAP-1", "minute", "四台各自獨立判斷，任何一台的結果都不影響其他台的時間或狀態（唯一的例外是跨台沿用標籤，只影響命名）。",
         ("pipeline.run", "pipeline.run_channel")),

    # ============================== ⑤ 五字標品質 ==============================
    Rule("LBL-5", "label",
         "五字標長度 4 到 5 個字，超過 5 個字一律退件或截短，不會寫進表格；每個中文字算 1 字，每個英文詞（連續英數字）算 1 字（例：MUSIC EXPO 算 2 字）。",
         ("labeler.validate", "labeler.label_length"),
         (Param("min_len", "最短字數", 4, "字", _dc("labeler", "LabelRules", "min_len")),
          Param("max_len", "最長字數", 5, "字", _dc("labeler", "LabelRules", "max_len")))),
    Rule("SRC-2", "label",
         "標籤裡每個中文字都要出現在這則新聞的主標題裡，英文字母與數字組成的詞也一樣（沒讀到標題時才用語音）；只有少數助詞可以額外加：「中了再又仍已遭被將要的與和」。"
         "有標題時，不能用語音裡的字，因為整段語音幾乎含有所有常用字，會讓任何標籤都過關。",
         ("labeler.validate",),
         (Param("FUNCTION_CHARS", "可額外加入的助詞", "中了再又仍已遭被將要的與和", "字集", lambda: "".join(sorted(_const("labeler", "FUNCTION_CHARS")()))),)),
    Rule("OUT-grammar", "label",
         "標籤裡不可含斜線「/」（斜線是同分鐘多則的分隔字元），也不可以剛好等於「廣告」「氣象」「訊號中斷」（會和狀態混淆）。",
         ("labeler.validate", "minute_mapper.validate_label"),
         (Param("LabelRules.sep", "分隔字元", "/", "字元", _dc("labeler", "LabelRules", "sep")),
          Param("LabelRules.status_words", "保留給狀態的詞", ["廣告", "氣象", "訊號中斷"], "詞", lambda: list(_dc("labeler", "LabelRules", "status_words")())))),
    Rule("LBL-4", "label",
         "社會新聞（標題含車禍、撞、輾、火警、爆炸、槍、毒駕、詐騙、殺人、墜樓、逮捕、聲押等犯罪、意外、災害用語；詞表在 config/lexicon.yaml 的 social_keywords）要有地點："
         "標題裡有地點 → 標籤一定要含其中一個地點（優先用較小的地名，新北土城寫「土城」），沒有就退件重寫，兩次都不過就改用規則備援；"
         "標題裡沒有地點（地點只在語音裡）→ 不退件，保留標籤，但在複查表標「社會新聞無地點」請人看一眼，不可以因此丟掉好標籤。"
         "一般新聞（台股、政治、體育、藝人）不適用。",
         ("labeler.validate", "labeler.is_social", "labeler.review_reasons")),
    Rule("TONE-1", "label",
         "標題有兩個字以上的未確認說法（疑似、涉嫌、據傳、傳出、恐將、恐怕、可能、爆料），標籤把它拿掉了 → 退件，因為等於把傳聞寫成定論；"
         "標題只有單字的未確認字（疑、傳、恐、涉）→ 不退件，保留標籤，只在複查表標「未確認語氣」。"
         "「宣傳」「恐怖」「懷疑」這類剛好含有那個字、意思不是未確認的詞，先排除再比對（排除詞表在 config/lexicon.yaml 的 hedge_exclusions）。",
         ("labeler.validate", "labeler.review_reasons", "lexicon.Lexicon.has_strong_hedge"),
         (Param("hedge_markers（兩字以上）", "會退件的未確認詞", ["疑似", "傳出", "恐將", "恐怕", "涉嫌", "據傳", "可能", "爆料"], "詞", _hedges(True)),
          Param("hedge_markers（單字）", "只標記不退件的未確認字", ["疑", "傳", "恐", "涉"], "字", _hedges(False)))),
    Rule("§6.3", "label",
         "AI 產生的標籤沒通過上面的檢查 → 退件，把原因告訴 AI 重寫一次；還是不過 → 改用規則備援（FB-4），並標記請人複查。",
         ("labeler.make_label",),
         (Param("DEFAULT_RETRIES", "退件後重試次數", 1, "次", lambda: _const("labeler", "DEFAULT_RETRIES")()),
          Param("make_label(retries=)", "make_label 預設重試次數", 1, "次", _call_default("labeler", "make_label", "retries")))),
    Rule("FB-4", "label",
         "規則備援：把標題照標點和空白切成短句，丟掉開頭的驚嘆片語，挑最像「事件」的短句，從中擷取 4 到 6 字。每個字都是從標題複製來的，不另外編字。"
         "社會新聞：優先用「地點＋事件詞（撞、輾、縱火、拘提…）」；其他新聞：選含地名或事件詞的短句，或剛好 4 到 6 字的短句。標題有兩字以上的未確認詞時，標籤會保留它。",
         ("labeler.rule_label",),
         (Param("TARGET_LEN", "目標字數", 5, "字", _const("labeler", "TARGET_LEN")),)),
    Rule("LBL-7", "label",
         "規則備援的標籤不可以斷在字中間：不可以停在連接詞或助詞上（未、也、的、了、被、遭、在、與、和、及、為、是、將、已、還、更、又、就、都、才、並、且、而、或），"
         "也不可以用單獨的數字開頭或結尾（像 0050 這種代號算名稱，不在此限）。事件詞、地名、未確認詞整個保留，不從中間切開。",
         ("labeler.rule_label", "labeler._span", "labeler._anchored", "labeler._tidy"),
         (Param("END_BAD", "不可當結尾的字", "未也的了被遭在與和及為是將已還更又就都才並且而或", "字集", lambda: "".join(sorted(_const("labeler", "END_BAD")()))),
          Param("START_BAD", "不可當開頭的字", "未也的了在與和及為是已還更又就都才並且而或", "字集", lambda: "".join(sorted(_const("labeler", "START_BAD")()))))),
    Rule("LBL-8", "label",
         "標題開頭如果是「驚嘆號或冒號前最多 6 個字」的驚嘆片語（例：大膽！、囂張！、驚傳：），而且後面的內容夠長（至少 6 個字）→ 丟掉這個片語再擷取。",
         ("labeler.strip_lead_fragment",),
         (Param("LEAD_MAX_CHARS", "開頭片語最長字數", 6, "字", _const("labeler", "LEAD_MAX_CHARS")),)),
    Rule("X-1", "label",
         "另一台在前後 15 分鐘內，已經替同一件事命名（兩個標題共有至少 3 組連續兩字詞，或至少 2 組而且整體相似度達 0.35）→ 沿用那個標籤，讓四台對同一件事寫同樣的字；沿用的標籤仍須通過本台來源的字元檢查（X-2），所以不會把別台的事實帶進來。",
         ("cross_channel.unify", "cross_channel.same_event"),
         (Param("unify(window_sec=)", "跨台比對的時間視窗", 900, "秒", _call_default("cross_channel", "unify", "window_sec")),
          Param("same_event(min_shared=)", "共有的兩字詞至少幾組", 3, "組", _call_default("cross_channel", "same_event", "min_shared")),
          Param("same_event(min_ratio=)", "只有 2 組共同詞時的相似度門檻", 0.35, "比例", _call_default("cross_channel", "same_event", "min_ratio")))),

    # ============================== ⑥ 不確定時的容錯 ==============================
    Rule("R20-1", "fallback",
         "某一台在判斷、命名或排進分鐘格的任何一步出錯 → 只記下這一台失敗的原因，其他台照常產出；全部都失敗，或 Excel 寫不出來，才停下來並說明原因。",
         ("pipeline.run",)),
    Rule("R20-2", "fallback",
         "AI 服務沒有回應 → 不重試一個死掉的服務（會白白等逾時），這一格直接用規則備援，並標記「AI 服務無回應」；"
         "連續失敗 3 次後，後面的格子一律立即改用規則備援，不再等待。",
         ("labeler.make_label", "llm.GuardedGenerator"),
         (Param("GuardedGenerator.max_failures", "連續失敗幾次後不再呼叫 AI", 3, "次", _call_default("llm", "GuardedGenerator.__init__", "max_failures")),)),
    Rule("FB-1", "fallback",
         "換題的佐證只有「標題變了」一項 → 仍然判成換題（寧可多記一則也不要漏），但標記低信心請人複查。",
         ("segmenter.segment",)),
    Rule("FB-3", "fallback",
         "讀不到主標題 → 用語音主題決定換題，用語音當標籤來源命名，標記低信心；標籤仍要通過同樣的檢查。",
         ("segmenter.segment", "labeler.make_label")),
    Rule("FB-6", "fallback",
         "黑畫面超過 30 秒 → 判定訊號中斷，這一段寫「訊號中斷」，優先於廣告判斷。",
         ("ad_state.detect",),
         (Param("outage_black_sec", "判定訊號中斷的黑畫面秒數", 30, "秒", _dc("ad_state", "AdParams", "outage_black_sec")),
          Param("outage", "訊號中斷寫在格子裡的字", "訊號中斷", "字串", _labels("outage")))),
    Rule("R20-3", "fallback",
         "任何用字沒把握的格子（備援標籤、低信心、缺地點、語氣不確定）→ 在複查版 Excel 標黃，並附上畫面標題、語音和原因，以及每一格的依據檔（evidence.jsonl）供回頭比對影片時間點。",
         ("evidence.build", "excel_writer.flag_reasons", "excel_writer.write_review_copy")),

    # ============================== ⑦ RAG 與 ML（預設關閉） ==============================
    Rule("RAG-1", "optional",
         "預設關閉。打開後，替 AI 寫標籤前，先從「過去被人工確認過的標題與五字標」知識庫裡找最相似的幾組，放進提示當範例；"
         "知識庫的內容不通過字元檢查的不收；AI 的答案仍要通過上面的全部檢查，所以檢索不會繞過任何規則。",
         ("kb.rag_from_rules", "kb.KB.retrieve", "llm.build_messages"),
         (Param("labeler.rag.enabled", "是否預設打開", False, "開關", lambda: _rag_enabled_default()),
          Param("Rag.k", "每次放進提示的範例數", 3, "組", _dc("kb", "Rag", "k")))),
    Rule("ML-1", "optional",
         "預設關閉（模式是「規則」）。打開「學習式」後，廣告判斷和換題判斷改由用人工標準答案訓練的模型給機率，機率高於模型門檻才算；"
         "不論開不開，標籤的檢查規則完全一樣。",
         ("ad_state.detect", "segmenter.segment", "mlmodel.load_model"),
         (Param("AdParams.mode", "廣告判斷模式", "rules", "模式", _dc("ad_state", "AdParams", "mode")),
          Param("SegParams.mode", "換題判斷模式", "rules", "模式", _dc("segmenter", "SegParams", "mode")))),
    Rule("NFR-14", "optional",
         "外部 AI（Claude）預設不准用；准用時也只送主標題文字和不超過 200 字的語音節錄，不送影片或畫面。預設用本機 Ollama，資料不出公司。",
         ("llm.ClaudeGenerator", "llm.build_user_prompt"),
         (Param("MAX_ASR_CHARS", "最多送出的語音字數", 200, "字", _const("llm", "MAX_ASR_CHARS")),)),
]


def _rag_enabled_default() -> bool:
    from . import kb

    return kb.rag_from_rules({}) is not None


# ------------------------------------------------------------------------------------------------
# 取用與輸出
# ------------------------------------------------------------------------------------------------
def by_group() -> list[tuple[str, str, str, list[Rule]]]:
    return [(k, title, note, [r for r in RULES if r.group == k]) for k, title, note in GROUPS]


def resolve(where: str) -> Any:
    """把「模組.函式」找回真正的物件；找不到就丟 AttributeError / ImportError。"""
    parts = where.split(".")
    for i in range(len(parts), 0, -1):
        try:
            obj: Any = importlib.import_module(f"{PKG}." + ".".join(parts[:i]))
        except ModuleNotFoundError:
            continue
        for p in parts[i:]:
            obj = getattr(obj, p)
        return obj
    raise ImportError(where)


def _fmt_value(v: Any) -> str:
    if isinstance(v, bool):
        return "開" if v else "關"
    if isinstance(v, (list, tuple)):
        return "、".join(str(x) for x in v)
    return str(v)


def _param_text(p: Param) -> str:
    v = _fmt_value(p.value)
    unit = "" if p.unit in ("字串", "字集", "詞", "開關", "模式", "字元") else p.unit
    return f"{p.label}（{p.name}）= {v}{(' ' + unit) if unit else ''}"


def to_dict() -> dict:
    return {
        "title": "新聞監看判斷規則",
        "groups": [
            {
                "key": k, "title": title, "note": note,
                "rules": [
                    {
                        "id": r.id, "text": r.text, "code": list(r.where),
                        "params": [{"name": p.name, "label": p.label, "value": p.value, "unit": p.unit} for p in r.params],
                    }
                    for r in rs
                ],
            }
            for k, title, note, rs in by_group()
        ],
    }


def render_text() -> str:
    out = ["新聞監看判斷規則（編號沿用 docs/spec.md；數字與程式預設值由測試核對）", ""]
    for _, title, note, rs in by_group():
        out += [f"== {title}（{len(rs)} 條）", f"   {note}", ""]
        for r in rs:
            out.append(f"[{r.id}] {r.text}")
            for p in r.params:
                out.append(f"      數字：{_param_text(p)}")
            out.append(f"      程式：{'、'.join(r.where)}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_markdown() -> str:
    out = [
        "# 新聞監看判斷規則",
        "",
        "> 這份文件由 `newsbar rules --markdown docs/rules.md` 從 `src/newsbar/rulebook.py` 產生，請不要直接改這裡，改 rulebook.py 後重新產生。",
        "> 規則編號沿用 [spec.md](spec.md)；spec 沒有的編號是實作時補上的。",
        "> 表中的數字都是程式現在實際使用的預設值，`tests/test_rulebook.py` 會逐一核對，所以不會和程式脫節。",
        "> 程式裡每個判斷處也有一行「# 規則 編號：…」的中文註解，可以用編號搜尋。",
        "",
        "## 目錄",
        "",
    ]
    for k, title, _, rs in by_group():
        out.append(f"- {title}（{len(rs)} 條）")
    out.append("")
    for k, title, note, rs in by_group():
        out += [f"## {title}", "", note, ""]
        for r in rs:
            out.append(f"### {r.id}")
            out += ["", r.text, ""]
            if r.params:
                out += ["| 數字 | 程式裡的名字 | 目前的值 | 單位 |", "|---|---|---|---|"]
                for p in r.params:
                    out.append(f"| {p.label} | `{p.name}` | {_fmt_value(p.value)} | {p.unit} |")
                out.append("")
            out += ["程式位置：" + "、".join(f"`{w}`" for w in r.where), ""]
    return "\n".join(out).rstrip() + "\n"


def to_json() -> str:
    return json.dumps(to_dict(), ensure_ascii=False, indent=2) + "\n"
