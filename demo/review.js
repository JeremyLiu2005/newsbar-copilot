/* Review workspace (人工複查): a priority queue of cells that need a human, the evidence for one cell,
 * and a one-keystroke decision. Used by the 監看台 (db-backed) and the 操作原型 (local draft).
 * Needs CSS tokens from the host page and review.css. */
(function () {
  const ETYPES = ['漏換題', '過度換題', '包框誤判', '角落廣告誤判', '標不可辨認', '捏造', '格式錯誤', '其他'];
  const SIG = {
    'NEW-1': '主 bar 出現新標題，且穩定 7 秒以上', 'NEW-2a': '語音主題改變', 'NEW-2b': '畫面切回主播台', 'NEW-2c': '說話的人換了',
    'NEW-2d': '出現記者名條', 'SAME-6': '有語音卻沒有其他佐證，標題輪播視為同一則', 'SAME-7': '名條不自成一則', 'SAME-8': '3 分鐘內同一標題輪回，併回原則',
    'CAROUSEL': '主 bar 在輪播頭條預告', 'AD-a': '主畫面版面改變（包框）', 'AD-b': '主 bar 底色改變', 'AD-c': '台徽消失', 'AD-d': '黑畫面或靜音',
    'AD-e': '語音是廣告詞', 'AD-f': '聲音指紋命中廣告庫', 'AD-g': '跑馬列消失', 'AD-h': '主 bar 出現電話或「廣告後立即回到」', 'AD-i': '主 bar 輪播頭條預告',
    'AD-j': '畫面時鐘消失', 'AD-9': '版面已恢復但等不到新讀數，逾時回新聞', 'TXT-5': '天氣新聞，記「氣象」'
  };
  const WHY = {
    fb3: '換題當下標題沒讀到（或廣告後沒等到新標題），這一格先用語音決定名稱。請對照畫面上的標題，確認是同一則新聞。',
    fb4: 'AI 寫的五字標兩次沒通過逐字檢查，改用規則直接從標題摘字。請看這幾個字是否說得清楚是哪則新聞。',
    fb1: '換題的佐證偏弱。請看這一則的起點，是不是真的換了新聞，或該併回前一則。',
    fb1c: '換題的佐證偏弱，但本機模型初判這是新的一則。你可以快速確認，不必逐項細查。',
    smp: '系統高信心的格子，隨機抽樣檢查，用來確認沒有漏看的錯誤。'
  };
  const TAGNAME = { fb3: '語音定名', fb4: '規則擷取', fb1: '換題存疑', fb1c: '換題存疑・AI 已初判', smp: '高信心抽樣' };
  const SHORT = { fb3: '語音定名', fb4: '規則擷取', fb1: '換題存疑', fb1c: '換題存疑', smp: '抽樣' };
  const FUNC = new Set('中了再又仍已遭被將要的與和');
  const $ = (r, s) => r.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const clock = sec => { const t = 12 * 3600 + sec, h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = t % 60; return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`; };
  const hm = m => `${m.slice(0, 2)}:${m.slice(2)}`;
  const mmss = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

  function cleared(c) {
    const news = c.items.filter(i => i.type === 'news');
    return c.fb === 'FB-1' && news.length > 0 && news.every(i => i.judge && i.judge.verdict === 'different');
  }
  function kindOf(c) {
    const fb = c.fb || '';
    if (fb.includes('FB-3')) return 'fb3';
    if (fb.includes('FB-4')) return 'fb4';
    if (fb.includes('FB-1')) return cleared(c) ? 'fb1c' : 'fb1';
    return c.conf === 'low' ? 'fb1' : 'smp';
  }
  const PRIO = { fb3: 0, fb4: 1, fb1: 2, fb1c: 3, smp: 4 };

  /** Check a cell's label against the titles on screen: length 4–6 per part, every content character from the title. */
  function labelCheck(c) {
    const titles = c.items.filter(i => i.type === 'news' && i.bar).map(i => i.bar).join('');
    return String(c.v || '').split('/').filter(Boolean).map(p => {
      if (p === '廣告' || p === '氣象') return { p, ok: true, n: 0, bad: [] };
      const chars = [...p], bad = chars.filter(ch => !titles.includes(ch) && !FUNC.has(ch) && !/[0-9A-Za-z]/.test(ch));
      return { p, n: chars.length, bad, ok: chars.length >= 4 && chars.length <= 6 && !bad.length };
    });
  }

  /** Draft store: lives in this browser only. */
  function localStore(key) {
    let rec = {}; const subs = [];
    try { rec = JSON.parse(localStorage.getItem(key) || '{}'); } catch (e) { }
    const flush = () => { try { localStorage.setItem(key, JSON.stringify(rec)); } catch (e) { } subs.forEach(f => f()); };
    return {
      mode: 'local', all: () => rec,
      async save(r) { rec[r.channel + '_' + r.minute] = r; flush(); },
      async remove(id) { delete rec[id]; flush(); },
      subscribe(f) { subs.push(f); }, notify: flush
    };
  }

  function mount(root, opt) {
    const D = opt.D, store = opt.store, NAMES = D.channels;
    const S = { ch: new Set(), kind: new Set(), status: 'todo', sample: opt.sample !== false, idx: 0, id: null, undo: [], t0: null, mode: null, msg: '' };
    const all = D.cells;
    const highs = all.filter(c => c.conf !== 'low' && c.items.some(i => i.type === 'news')).sort((a, b) => a.m.localeCompare(b.m) || a.ch.localeCompare(b.ch));
    const sampled = new Set(highs.filter((_, i) => i % 6 === 0).map(c => c.ch + '_' + c.m));
    const pool = all.filter(c => c.conf === 'low' || sampled.has(c.ch + '_' + c.m));
    const idOf = c => c.ch + '_' + c.m;
    const recOf = c => store.all()[idOf(c)];

    root.innerHTML = `<div class="rv">
      <div class="rv-top"><div class="rv-prog" id="rv-prog"></div>
        <div class="rv-who"><label>審核者 <input type="text" id="rv-who" placeholder="姓名或代號" size="9"></label>
          <button class="ghost" id="rv-json">匯出 JSON</button><button class="ghost" id="rv-csv">匯出 CSV</button></div></div>
      <div class="rv-banner" id="rv-banner" hidden></div>
      <div class="rv-grid">
        <aside class="rv-q"><div class="rv-filters" id="rv-filters"></div><div class="rv-list" id="rv-list" role="listbox" aria-label="待複查佇列"></div></aside>
        <section class="rv-main"><div id="rv-card"></div><p class="rv-toast" id="rv-toast" aria-live="polite"></p>
          <details id="rv-diff"><summary id="rv-diff-s">修正對照</summary><div id="rv-diff-b"></div></details>
          <details><summary>鍵盤快速鍵</summary><div class="help">
            <kbd>Y</kbd><span>這一格對</span><kbd>E</kbd><span>改寫（游標跳到輸入欄）</span><kbd>X</kbd><span>這一格不該填</span>
            <kbd>J / K</kbd><span>下一格／上一格</span><kbd>S</kbd><span>先跳過</span><kbd>Z</kbd><span>復原上一筆</span><kbd>Enter</kbd><span>在輸入欄按下＝儲存</span></div></details>
        </section></div></div>`;
    const who = $(root, '#rv-who');
    try { who.value = localStorage.getItem('nb-reviewer') || ''; } catch (e) { }
    who.addEventListener('change', () => { try { localStorage.setItem('nb-reviewer', who.value.trim()); } catch (e) { } });

    const queue = () => {
      let q = pool.filter(c => {
        const k = kindOf(c), r = recOf(c);
        if (S.ch.size && !S.ch.has(c.ch)) return false;
        if (k === 'smp' && !S.sample) return false;
        if (S.kind.size && !S.kind.has(k)) return false;
        if (S.status === 'todo' && r) return false;
        if (S.status === 'done' && !r) return false;
        return true;
      });
      q.sort((a, b) => PRIO[kindOf(a)] - PRIO[kindOf(b)] || a.m.localeCompare(b.m) || a.ch.localeCompare(b.ch));
      return q;
    };
    const total = () => pool.filter(c => S.sample || kindOf(c) !== 'smp').length;
    const doneN = () => pool.filter(c => (S.sample || kindOf(c) !== 'smp') && recOf(c)).length;

    function top() {
      const n = total(), d = doneN(), recs = Object.values(store.all()), fixes = recs.filter(r => r.verdict === 'fix').length;
      const el = S.t0 ? Math.floor((Date.now() - S.t0) / 1000) : 0, per = d && S.t0 ? Math.round(el / Math.max(d, 1)) : null;
      $(root, '#rv-prog').innerHTML = `<div class="line"><span>已複查 <b>${d}</b> / ${n} 格・改正 ${fixes} 格</span>
        <span class="rv-note" id="rv-time">${S.t0 ? `用時 ${mmss(el)}${per != null ? `・平均 ${per} 秒／格（目標 30 秒）` : ''}` : '開始複查後計時'}</span></div>
        <div class="rv-track"><i style="width:${n ? d / n * 100 : 0}%"></i></div>`;
      const b = $(root, '#rv-banner');
      b.hidden = !store.banner; if (store.banner) b.textContent = store.banner;
    }

    function filters() {
      const chip = (on, a, t) => `<button class="chip${on ? ' on' : ''}" ${a} aria-pressed="${on}">${t}</button>`;
      const cnt = k => pool.filter(c => kindOf(c) === k).length;
      $(root, '#rv-filters').innerHTML = `
        <div class="grp"><span class="gl">狀態</span>${[['todo', '待複查'], ['all', '全部'], ['done', '已複查']].map(([v, t]) => chip(S.status === v, `data-st="${v}"`, t)).join('')}</div>
        <div class="grp"><span class="gl">台別</span>${Object.keys(NAMES).map(c => chip(S.ch.has(c), `data-ch="${c}"`, NAMES[c])).join('')}</div>
        <div class="grp"><span class="gl">類型</span>${['fb3', 'fb4', 'fb1', 'fb1c'].map(k => chip(S.kind.has(k), `data-k="${k}"`, `${TAGNAME[k]} ${cnt(k)}`)).join('')}
          ${chip(S.sample, 'data-smp="1"', `高信心抽樣 ${sampled.size}`)}</div>`;
      root.querySelectorAll('[data-st]').forEach(b => b.onclick = () => { S.status = b.dataset.st; S.idx = 0; S.id = null; render(); });
      root.querySelectorAll('[data-ch]').forEach(b => b.onclick = () => { S.ch.has(b.dataset.ch) ? S.ch.delete(b.dataset.ch) : S.ch.add(b.dataset.ch); S.idx = 0; S.id = null; render(); });
      root.querySelectorAll('[data-k]').forEach(b => b.onclick = () => { S.kind.has(b.dataset.k) ? S.kind.delete(b.dataset.k) : S.kind.add(b.dataset.k); S.idx = 0; S.id = null; render(); });
      root.querySelectorAll('[data-smp]').forEach(b => b.onclick = () => { S.sample = !S.sample; S.idx = 0; S.id = null; render(); });
    }

    function list(q) {
      const el = $(root, '#rv-list');
      if (!q.length) { el.innerHTML = `<div class="rv-empty">${S.status === 'todo' ? '這個條件下沒有待複查的格子了。' : '沒有符合的格子。'}</div>`; return; }
      el.innerHTML = q.map((c, i) => {
        const k = kindOf(c), r = recOf(c);
        return `<div class="qi${c.ch + c.m === S.id ? ' sel' : ''}${r ? ' done' : ''}" data-i="${i}" tabindex="0" role="option" aria-selected="${c.ch + c.m === S.id}">
          <span class="tm">${hm(c.m)}</span><span class="lb">${esc(c.v)}</span>
          ${r ? `<span class="tag ${r.verdict === 'fix' ? 't-fix' : 't-ok'}">${r.verdict === 'fix' ? '已改正' : '已確認'}</span>` : `<span class="tag t-${k}">${SHORT[k]}</span>`}
          <span class="sub">${NAMES[c.ch]}・${TAGNAME[k]}</span></div>`;
      }).join('');
      el.querySelectorAll('.qi').forEach(n => {
        const go = () => { S.idx = +n.dataset.i; S.id = null; render(); };
        n.onclick = go; n.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } };
      });
      const sel = el.querySelector('.sel'); if (sel) sel.scrollIntoView({ block: 'nearest' });
    }

    function itemHtml(c, i) {
      if (i.type === 'ad') {
        return `<div class="rv-item"><div><span class="tag t-fb4">廣告</span> <span class="meta" style="font:13px var(--mono);color:var(--mute)">${clock(i.start)}–${clock(i.end)}</span></div>
          ${i.strip && i.strip.length ? `<div><div class="cap">破口從頭到尾平均截 4 張</div><div class="strip4">${i.strip.map(x => `<figure style="margin:0"><img loading="lazy" src="${x.src}" alt="${NAMES[c.ch]} ${clock(x.sec)}"><div class="cap">${clock(x.sec)}</div></figure>`).join('')}</div></div>` : ''}</div>`;
      }
      const j = i.judge;
      const jt = { different: '新的一則', same: '疑似同一則', noise: '疑似雜訊（不是新聞標題）', first: '各台第一則', unknown: '無法判斷' }[j && j.verdict];
      return `<div class="rv-item">
        <div><span class="tag t-fb1c">新聞</span> <b style="margin-left:6px">${esc(i.label || '')}</b> <span style="font:13px var(--mono);color:var(--mute);margin-left:6px">從 ${clock(i.start)} 起</span></div>
        <div class="rv-ev">
          ${i.full ? `<div><div class="cap">完整畫面・${clock(i.shot ?? i.start ?? 0)}</div><img loading="lazy" src="${i.full}" alt="${NAMES[c.ch]} 完整畫面"></div>` : ''}
          ${i.img ? `<div><div class="cap">主 bar 位置裁切（OCR 讀的區域）</div><img loading="lazy" src="${i.img}" alt="主 bar 裁切"></div>` : ''}
        </div>
        <div><div class="cap">主 bar 讀到的標題（可用滑鼠選取，再按「帶入選取文字」）</div><div class="rv-title" data-title>${esc(i.bar || '（讀不到）')}</div></div>
        ${j && j.prev ? `<div class="cmp"><b>前一則</b><span>${esc(j.prev)}</span><b>這一則</b><span>${esc(i.bar || '')}</span></div>` : ''}
        ${j ? `<div class="judge"><div class="h">AI 初判：${jt || esc(j.verdict)}</div><div>${esc(j.reason || '')}</div><div class="rv-note">本機 7B 模型的參考意見，只用來排序，不會自動改表格。</div></div>` : ''}
        ${i.asr ? `<details><summary>語音辨識節錄</summary><div style="margin-top:6px">${esc(i.asr)}…</div></details>` : ''}
      </div>`;
    }

    function card(c, q) {
      const box = $(root, '#rv-card');
      if (!c) { box.innerHTML = `<div class="rv-card"><b>${S.status === 'todo' ? '這個條件下的格子都複查完了' : '沒有可看的格子'}</b><p class="rv-note">${S.status === 'todo' ? '可以改看「全部」，或匯出修正紀錄。' : '調整左側的篩選條件。'}</p></div>`; return; }
      const k = kindOf(c), r = recOf(c), lc = labelCheck(c), sigs = [...new Set(c.sig || [])];
      const chk = lc.map(x => `<span class="p${x.ok ? '' : ' bad'}">${x.bad.length ? [...x.p].map(ch => x.bad.includes(ch) ? `<u>${esc(ch)}</u>` : esc(ch)).join('') : esc(x.p)}${x.n ? `・${x.n} 字` : ''}${x.n && x.n > 6 ? '，超過 6 字' : x.n && x.n < 4 ? '，少於 4 字' : ''}${x.bad.length ? `，${x.bad.length} 個字不在標題中` : ''}</span>`).join('');
      box.innerHTML = `<div class="rv-card">
        <div class="rv-head"><div><div class="meta">${NAMES[c.ch]}・${hm(c.m)}　第 ${Math.min(S.idx + 1, q.length)} / ${q.length} 格</div><div class="val">${esc(c.v)}</div></div>
          <span class="tag t-${k}">${TAGNAME[k]}</span></div>
        <div class="why">${WHY[k]}</div>
        <div><div class="cap">五字標檢查</div><div class="lblchk">${chk || '—'}</div></div>
        ${c.items.map(i => itemHtml(c, i)).join('')}
        <details><summary>判斷依據（${sigs.length} 項訊號）</summary>${sigs.map(s => `<div style="margin-top:4px"><code style="font:12px var(--mono);background:var(--bg);border:1px solid var(--line);border-radius:4px;padding:0 5px;margin-right:6px">${esc(s)}</code>${esc(SIG[s] || '')}</div>`).join('')}</details>
        <div class="rv-dec">
          <div class="cap">你的判定（目前寫「${esc(c.v) || '空白'}」）</div>
          <div class="big"><button data-act="ok" class="${r && r.verdict === 'ok' ? 'sel' : ''}">對<kbd>Y</kbd></button>
            <button data-act="fix" class="ghost">改寫<kbd>E</kbd></button><button data-act="blank" class="ghost">不該填<kbd>X</kbd></button>
            <button data-act="skip" class="ghost">先跳過<kbd>S</kbd></button></div>
          <div class="rv-fix" id="rv-fix" ${r && r.verdict === 'fix' ? '' : 'hidden'}>
            <div class="r"><input type="text" id="rv-val" value="${esc(r && r.verdict === 'fix' ? r.correct : c.v)}" placeholder="正確寫法" aria-label="正確寫法">
              <button class="ghost" data-act="pick">帶入選取文字</button><button class="ghost" data-act="orig">還原原標</button></div>
            <div class="r"><span class="rv-note">錯誤類型（必選）</span>${ETYPES.map(t => `<button class="chip${r && r.error_type === t ? ' on' : ''}" data-et="${t}" aria-pressed="${!!(r && r.error_type === t)}">${t}</button>`).join('')}</div>
            <div class="r"><input type="text" id="rv-note" placeholder="備註（選填）" value="${esc(r && r.note || '')}" aria-label="備註"><button data-act="save">儲存並看下一格<kbd>Enter</kbd></button></div>
          </div>
          <p class="rv-err" id="rv-err" hidden></p>
          ${r ? `<p class="rv-note">${esc(r.reviewer)}・${esc(String(r.reviewed_at).replace('T', ' ').slice(0, 16))}：${r.verdict === 'fix' ? `改成「${esc(r.correct || '（空白）')}」（${esc(r.error_type)}）` : '確認無誤'}　<button class="ghost" data-act="undo1" style="padding:2px 10px">撤銷這筆</button></p>` : ''}
        </div>
        <div class="rv-nav"><button class="ghost" data-act="prev">← 上一格<kbd>K</kbd></button><button class="ghost" data-act="next">下一格 →<kbd>J</kbd></button></div>
      </div>`;
      let et = r && r.error_type || '';
      const err = m => { const e = $(root, '#rv-err'); e.textContent = m; e.hidden = !m; };
      box.querySelectorAll('[data-et]').forEach(b => b.onclick = () => { et = b.dataset.et; box.querySelectorAll('[data-et]').forEach(x => { const on = x.dataset.et === et; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on); }); });
      const act = {
        ok: () => commit(c, { verdict: 'ok' }),
        fix: () => { $(root, '#rv-fix').hidden = false; $(root, '#rv-val').focus(); $(root, '#rv-val').select(); },
        blank: () => { $(root, '#rv-fix').hidden = false; $(root, '#rv-val').value = ''; if (!et) err('這格不該填：請選一個錯誤類型，再按儲存。'); },
        skip: () => step(1, true), prev: () => step(-1), next: () => step(1),
        pick: () => {
          const s = window.getSelection(), t = (s ? s.toString() : '').trim();
          const ok = s && s.anchorNode && (s.anchorNode.nodeType === 1 ? s.anchorNode : s.anchorNode.parentElement).closest('[data-title]');
          if (!t || !ok) return err('先用滑鼠在上面的標題選一段字。');
          err(''); $(root, '#rv-val').value = t;
        },
        orig: () => { $(root, '#rv-val').value = c.v; },
        save: () => {
          if (!et) return err('請選錯誤類型。');
          commit(c, { verdict: 'fix', correct: $(root, '#rv-val').value.trim(), error_type: et, note: $(root, '#rv-note').value.trim() || null }, err);
        },
        undo1: () => revert(c)
      };
      box.querySelectorAll('[data-act]').forEach(b => b.onclick = () => act[b.dataset.act]());
      const val = $(root, '#rv-val'); if (val) val.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); act.save(); } });
      S.act = act;
    }

    async function commit(c, part, err) {
      const rv = who.value.trim();
      const show = m => { if (err) err(m); else { const e = $(root, '#rv-err'); if (e) { e.textContent = m; e.hidden = false; } } };
      if (!rv) { who.focus(); return show('請先在上方填審核者（會記在修正紀錄裡）。'); }
      const before = recOf(c) || null;
      const rec = { channel: c.ch, minute: c.m, original: c.v, reviewer: rv, reviewed_at: new Date().toISOString(),
        correct: null, error_type: null, note: null, ...part };
      try { await store.save(rec); } catch (e) { return show('儲存失敗：' + (e.message || e.code || e)); }
      if (!S.t0) S.t0 = Date.now();
      S.undo.push({ id: idOf(c), before });
      toast(`已儲存「${NAMES[c.ch]} ${hm(c.m)}」${part.verdict === 'ok' ? '：確認無誤' : '：已改正'}`);
      afterChange(true);
    }
    async function revert(c) {
      const id = idOf(c), r = recOf(c); if (!r) return;
      S.undo.push({ id, before: r }); await store.remove(id); toast('已撤銷'); afterChange(false);
    }
    async function undoLast() {
      const u = S.undo.pop(); if (!u) return toast('沒有可復原的動作');
      if (u.before) await store.save(u.before); else await store.remove(u.id);
      toast('已復原上一筆'); S.id = u.id; afterChange(false, u.id);
    }
    function toast(m) { $(root, '#rv-toast').textContent = m; }
    function step(d, skip) {
      const q = queue(); if (!q.length) return;
      S.idx = Math.max(0, Math.min(q.length - 1, S.idx + d)); S.id = null; render();
      if (skip) toast('已跳過，稍後可從「待複查」再看到它');
    }
    function afterChange(advance, focusId) {
      if (advance && S.status !== 'todo') S.idx += 1;
      S.id = focusId || null;
      if (focusId) { const q = queue(), k = q.findIndex(c => idOf(c) === focusId); if (k >= 0) S.idx = k; }
      render(); if (opt.onChange) opt.onChange(store.all());
    }

    function diff() {
      const rs = Object.values(store.all()).filter(r => r.verdict === 'fix').sort((a, b) => a.minute.localeCompare(b.minute) || a.channel.localeCompare(b.channel));
      $(root, '#rv-diff-s').textContent = `修正對照（${rs.length} 格有改動）`;
      $(root, '#rv-diff-b').innerHTML = rs.length ? `<table><thead><tr><th>時間</th><th>台</th><th>原本</th><th>改成</th><th>錯誤類型</th><th>審核者</th></tr></thead><tbody>${rs.map(r => `<tr><td>${hm(r.minute)}</td><td>${esc(NAMES[r.channel])}</td><td>${esc(r.original)}</td><td><b>${esc(r.correct || '（空白）')}</b></td><td>${esc(r.error_type)}</td><td>${esc(r.reviewer)}</td></tr>`).join('')}</tbody></table>
        <p class="rv-note">正式後台會用這份紀錄產出另存的「修正版」Excel，原本的 Excel 不會被改動。</p>` : '<p class="rv-note">還沒有改動。</p>';
    }

    function render() {
      const q = queue();
      if (S.id) { const k = q.findIndex(c => idOf(c) === S.id); if (k >= 0) S.idx = k; }
      S.idx = Math.max(0, Math.min(S.idx, q.length - 1));
      const c = q[S.idx]; S.id = c ? idOf(c) : null; S.cur = c;
      top(); filters(); list(q); card(c, q); diff();
    }

    function download(name, text, type) {
      const go = async () => {
        const dl = window.claude && window.claude.use ? await window.claude.use('downloads').catch(() => null) : null;
        if (dl) { try { await dl.save({ filename: name, data: text }); return; } catch (e) { return; } }
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click();
      };
      go();
    }
    const rows = () => Object.values(store.all()).sort((a, b) => a.minute.localeCompare(b.minute) || a.channel.localeCompare(b.channel));
    $(root, '#rv-json').onclick = () => rows().length ? download('corrections.json', JSON.stringify(rows(), null, 1), 'application/json') : toast('還沒有複查紀錄可以匯出');
    $(root, '#rv-csv').onclick = () => {
      if (!rows().length) return toast('還沒有複查紀錄可以匯出');
      const q = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
      const head = ['台別', '時間', '原本', '判定', '正確寫法', '錯誤類型', '備註', '審核者', '時間戳'];
      const body = rows().map(r => [NAMES[r.channel], hm(r.minute), r.original, r.verdict === 'fix' ? '改正' : '確認', r.correct, r.error_type, r.note, r.reviewer, r.reviewed_at].map(q).join(','));
      download('corrections.csv', '﻿' + [head.map(q).join(','), ...body].join('\n'), 'text/csv');
    };

    document.addEventListener('keydown', e => {
      if (!root.isConnected || root.offsetParent === null || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target, typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT');
      if (typing) return;
      const k = e.key.toLowerCase(), a = S.act; if (!a) return;
      if (k === 'y') a.ok(); else if (k === 'e') { e.preventDefault(); a.fix(); } else if (k === 'x') a.blank();
      else if (k === 'j' || k === 'n') a.next(); else if (k === 'k' || k === 'p') a.prev(); else if (k === 's') a.skip(); else if (k === 'z') undoLast();
    });
    setInterval(() => { if (S.t0) { const n = $(root, '#rv-time'); if (n) { const d = doneN(), el = Math.floor((Date.now() - S.t0) / 1000); n.textContent = `用時 ${mmss(el)}${d ? `・平均 ${Math.round(el / d)} 秒／格（目標 30 秒）` : ''}`; } } }, 1000);
    store.subscribe(() => { render(); });
    render();
    return { render, go(id) { S.status = 'all'; S.kind.clear(); S.ch.clear(); S.id = id; render(); } };
  }

  window.NBReview = { mount, localStore, kindOf, cleared, ETYPES };
})();
