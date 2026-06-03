/* ============================================================
   SLATE — screens part 1: Hub, Lobby, Entries, Player drawer
   ============================================================ */
const { useState, useEffect } = React;

/* ---------- HUB / GAME LIBRARY ---------- */
function HubScreen({ onNav, onOpenFormat }) {
  const S = window.SLATE;
  const games = (S.SLATE_GAMES || []).length;
  const contests = S.CONTESTS || [];
  const prizePool = contests.reduce((s, c) => s + (c.prize || 0), 0);
  const topPrize = contests.reduce((m, c) => Math.max(m, c.top || 0), 0);
  return (
    <>
      <TopBar title="Good evening, Player!" sub={`${games} games on tonight's slate`} right={
        <button className="btn btn--ghost btn--sm hide-mobile" style={{ padding: '9px 12px' }}><Icon name="bell" size={18} /></button>
      } />
      <div className="app__scroll">
        <div className="page">
          {/* hero */}
          <div className="hero">
            <div className="hero__in">
              <Tag kind="live"><span className="pulse" /> Tonight's slate is live</Tag>
              <h1 className="mt-12">Tonight's Daily Slate is live.</h1>
              <p>Build a lineup under the {S.money(S.CAP)} cap across {games} games.{prizePool > 0 ? ` ${S.money(prizePool)} in prizes on the board.` : ''}</p>
              <div className="row wrap">
                <Btn variant="brand" size="lg" onClick={() => onNav('lobby')}>Play the slate <Icon name="chev" size={18} /></Btn>
                <Btn variant="ghost" size="lg" onClick={() => onNav('lobby')}>Browse contests</Btn>
              </div>
            </div>
          </div>

          {/* quick tiles */}
          <div className="tiles mt-24">
            <div className="tile"><div className="lbl">Tonight</div><div className="val">{games}</div><div className="sub">games on the slate</div></div>
            <div className="tile"><div className="lbl">Contests</div><div className="val">{contests.length}</div><div className="sub">open to enter</div></div>
            <div className="tile"><div className="lbl">Top prize</div><div className="val">{topPrize > 0 ? S.money(topPrize) : '—'}</div><div className="sub">across all contests</div></div>
            <div className="tile"><div className="lbl">Bankroll</div><div className="val">{S.money(S.WALLET)}</div><div className="sub">play-money balance</div></div>
          </div>

          {/* the game library */}
          <div className="section-head mt-32">
            <h2>Game library</h2>
            <span className="muted" style={{ fontSize: '.85rem', fontWeight: 600, whiteSpace: 'nowrap' }}>Eight ways to play your league</span>
          </div>
          <div className="fmt-grid">
            {S.FORMATS.map(f => (
              <a key={f.id} className={'fmt' + ((f.live || f.view) ? '' : ' fmt--soon')} onClick={() => f.view ? onNav(f.view) : onOpenFormat(f)}>
                <span className="fmt__glow" style={{ background: f.color }} />
                <span className="fmt__icon" style={{ background: f.color }}><Icon name={f.icon} size={24} /></span>
                {f.tag && <span style={{ position: 'absolute', top: 16, right: 16 }}><Tag kind={f.tag === 'live' ? 'live' : 'new'}>{f.tag === 'live' ? <><span className="pulse" /> Live</> : 'New'}</Tag></span>}
                <div className="fmt__name">{f.name}</div>
                <div className="fmt__desc">{f.desc}</div>
                <div className="fmt__foot">
                  {f.stat.map((s, i) => <span key={i} className="fmt__stat">{s}</span>)}
                </div>
              </a>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

/* ---------- DFS LOBBY ---------- */
function LobbyScreen({ onNav, onEnterContest }) {
  const S = window.SLATE;
  const [slate, setSlate] = useState('main');
  const slates = [
    { id: 'main', label: 'Main · 4 games', time: '6:05' },
    { id: 'turbo', label: 'Turbo · 2 games', time: '7:40' },
    { id: 'late', label: 'Late Voyage · 1 game', time: '8:10' },
  ];
  return (
    <>
      <TopBar title="Daily Slate" sub="Pick a contest, then build" back onBack={() => onNav('hub')} right={
        <span className="simclock hide-mobile"><Icon name="clock" size={15} /> Locks <span className="num">6:05</span></span>
      } />
      <div className="app__scroll">
        <div className="page">
          {/* slate selector */}
          <div className="slate-tabs">
            {slates.map(s => <Chip key={s.id} active={slate === s.id} onClick={() => setSlate(s.id)}>{s.label}</Chip>)}
          </div>

          {/* games strip */}
          <div className="card card--pad mb-16">
            <div className="eyebrow mb-12">Tonight's games · Sim day Jun 16</div>
            <div className="row wrap" style={{ gap: 10 }}>
              {S.SLATE_GAMES.map((g, i) => (
                <div key={i} className="row" style={{ gap: 8, padding: '8px 14px', background: 'var(--paper-2)', borderRadius: 'var(--r)' }}>
                  <b className="num" style={{ color: S.TEAMS[g.away].color }}>{g.away}</b>
                  <span className="dim" style={{ fontSize: '.75rem' }}>@</span>
                  <b className="num" style={{ color: S.TEAMS[g.home].color }}>{g.home}</b>
                  <span className="dim num" style={{ fontSize: '.74rem', marginLeft: 4 }}>{g.time}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="section-head"><h2>Contests</h2><span className="muted" style={{ fontSize: '.85rem', fontWeight: 600, whiteSpace: 'nowrap' }}>{S.CONTESTS.length} open</span></div>
          <div className="col" style={{ gap: 12 }}>
            {S.CONTESTS.map(c => (
              <div key={c.id} className="contest">
                <span className="contest__badge" style={{ background: c.color }}>{c.badge}</span>
                <div style={{ minWidth: 0 }}>
                  <div className="contest__name">{c.name}</div>
                  <div className="contest__meta">
                    <span>{c.kind}</span>
                    <span>Entry <b className="num">{c.fee === 0 ? 'Free' : S.money(c.fee)}</b></span>
                    <span>Top prize <b className="num">{S.money(c.top)}</b></span>
                    <span><b className="num">{c.entries.toLocaleString('en-IN')}</b>/{c.cap.toLocaleString('en-IN')}</span>
                  </div>
                  <div className="fill"><i style={{ width: Math.min(100, (c.entries / c.cap) * 100) + '%' }} /></div>
                </div>
                <div className="contest__prize">
                  <div className="amt">{S.money(c.prize)}</div>
                  <div className="lbl">Prize pool</div>
                  <Btn variant="brand" size="sm" className="mt-8" onClick={() => onEnterContest(c)}>Enter</Btn>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

/* ---------- MY ENTRIES (real data) ---------- */
function EntriesScreen({ onNav, onOpenContest }) {
  const S = window.SLATE;
  const [tab, setTab] = useState('live');
  const [entries, setEntries] = useState(null);

  useEffect(() => {
    fetch('/fantasy/api/entries').then(r => (r.ok ? r.json() : [])).then(setEntries).catch(() => setEntries([]));
  }, []);

  const all = entries || [];
  const bucket = e => (e.games_total > 0 && e.games_done >= e.games_total) ? 'past'
    : (e.games_done > 0 ? 'live' : 'upcoming');
  const live = all.filter(e => bucket(e) === 'live');
  const upcoming = all.filter(e => bucket(e) === 'upcoming');
  const past = all.filter(e => bucket(e) === 'past');
  const rows = tab === 'past' ? past : tab === 'upcoming' ? upcoming : live;
  const prog = e => (e.games_total ? e.games_done / e.games_total : 0);

  return (
    <>
      <TopBar title="My Entries" sub="Track your lineups" back onBack={() => onNav('hub')} />
      <div className="app__scroll">
        <div className="page page--narrow">
          <div className="slate-tabs">
            <Chip active={tab === 'live'} onClick={() => setTab('live')}>Live · {live.length}</Chip>
            <Chip active={tab === 'upcoming'} onClick={() => setTab('upcoming')}>Upcoming · {upcoming.length}</Chip>
            <Chip active={tab === 'past'} onClick={() => setTab('past')}>Past · {past.length}</Chip>
          </div>
          {entries === null ? (
            <div className="card card--pad center" style={{ padding: '48px 20px' }}><div className="dim" style={{ fontWeight: 600 }}>Loading your entries…</div></div>
          ) : rows.length === 0 ? (
            <div className="card card--pad center" style={{ padding: '48px 20px' }}>
              <div className="dim" style={{ fontWeight: 600 }}>No {tab} entries.</div>
              <Btn variant="soft" className="mt-16" onClick={() => onNav('lobby')}>Find a contest</Btn>
            </div>
          ) : (
            <div className="col" style={{ gap: 12 }}>
              {rows.map((e, i) => (
                <div key={i} className="contest" onClick={() => onOpenContest && onOpenContest(e.contest_id)} style={{ cursor: 'pointer' }}>
                  <span className="contest__badge" style={{ background: e.color }}>{e.badge}</span>
                  <div style={{ minWidth: 0 }}>
                    <div className="contest__name">{e.contest}</div>
                    <div className="contest__meta">
                      {e.rank != null && <span>Rank <b className="num">{e.rank.toLocaleString('en-IN')}</b>/{(e.of || 0).toLocaleString('en-IN')}</span>}
                      <span><b className="num">{e.pts.toFixed(1)}</b> pts</span>
                      <span style={{ color: e.live ? 'var(--live)' : 'var(--ink-3)', fontWeight: 700 }}>{e.live ? 'Live' : 'Final'}</span>
                    </div>
                    <div className="fill"><i style={{ width: (prog(e) * 100) + '%' }} /></div>
                  </div>
                  <div className="contest__prize">
                    <div className="amt num">{e.games_done}/{e.games_total}</div>
                    <div className="lbl">games</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/* ---------- COMING-SOON teaser for non-DFS formats ---------- */
function FormatTeaser({ fmt, onClose, onNav }) {
  if (!fmt) return null;
  return (
    <div className={'scrim scrim--open'} onClick={onClose}>
      <div className="drawer" onClick={e => e.stopPropagation()}>
        <div className="drawer__hero" style={{ background: fmt.color }}>
          <button className="drawer__close" onClick={onClose}><Icon name="x" size={18} /></button>
          <span className="fmt__icon" style={{ background: 'rgba(255,255,255,.22)', width: 54, height: 54 }}><Icon name={fmt.icon} size={28} /></span>
          <div className="drawer__name mt-12">{fmt.name}</div>
          <div className="drawer__meta">{fmt.stat.join(' · ')}</div>
        </div>
        <div className="drawer__body">
          <p className="muted" style={{ fontSize: '.95rem', lineHeight: 1.55 }}>{fmt.desc}</p>
          <div className="card card--pad mt-16">
            <div className="eyebrow mb-12">Why it only works here</div>
            <p className="muted" style={{ margin: 0, fontSize: '.88rem' }}>
              {fmt.id === 'stay' && 'The stay — a runner re-entering the play after a second-chance — has no MLB analog. Every stay, stay-RBI and graded RAD advancement is already a column in your save, so this is pure scoring config.'}
              {fmt.id === 'walkback' && 'In O27 a home run plants the hitter at third as a persistent Walk-Back runner. Power gains a tail of value that depends on who bats behind them — a draft puzzle real baseball can\u2019t pose.'}
              {fmt.id === 'pilot' && 'O27 has no bullpen — one continuous 27-out arc. Scoring only arc-3 work makes a finisher format out of a sport that structurally has no closers. Value emerges from fatigue, not role.'}
              {fmt.id === 'skipper' && 'Your save persists manager telemetry MLB never generates — declared seconds, shift outs added, joker deployment. So you can draft decisions instead of players.'}
              {fmt.id === 'voyage' && 'Top-of-order hitters see 5\u20137 PA a game, so \u201cget a hit\u201d is too easy. The bar rises in a sport-native way: a multi-hit AB or a driven-in stay run.'}
              {fmt.id === 'hothand' && 'You already model streak state, heat and work ethic. This format scores streak-weighted production — a momentum-trading game powered by your in-season variance model.'}
              {fmt.id === 'joker' && 'Jokers are tactical plate appearances with archetypes. Draft an archetype portfolio and score on joker-deployed outcomes — a small, weird, very O27 side-game.'}
              {fmt.id === 'dfs' && 'Daily salary-cap lineups on tonight\u2019s sim games — the flagship.'}
            </p>
          </div>
          {fmt.live
            ? <Btn variant="brand" block className="mt-16" onClick={() => { onClose(); onNav('lobby'); }}>Play now <Icon name="chev" size={18} /></Btn>
            : <Btn variant="ink" block className="mt-16">Notify me when it opens <Icon name="bell" size={17} /></Btn>}
          <div className="center muted mt-12" style={{ fontSize: '.78rem' }}>Runs on your active save · zero extra data</div>
        </div>
      </div>
    </div>
  );
}

/* ---------- GO STREAKING (hit-streak survivor) ---------- */
function StreakScreen({ onNav }) {
  const S = window.SLATE;
  const [data, setData] = useState(null);
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);

  function load() {
    fetch('/fantasy/api/streak').then(r => (r.ok ? r.json() : null)).then(setData).catch(() => setData(null));
  }
  useEffect(load, []);

  function pick(p) {
    if (busy) return;
    setBusy(true);
    fetch('/fantasy/api/streak/pick', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ player_id: p.id }) })
      .then(r => r.json()).then(j => { setBusy(false); if (!j.ok) window.alert(j.error || 'Could not make that pick.'); load(); })
      .catch(() => setBusy(false));
  }

  const d = data;
  const pool = d ? (d.pool || []) : [];
  const shown = q.trim() ? pool.filter(p => p.name.toLowerCase().includes(q.toLowerCase())) : pool;
  const resColor = r => r === 'hit' ? 'var(--live)' : r === 'miss' ? 'var(--down)' : 'var(--ink-3)';
  const resLabel = r => r === 'hit' ? 'Hit' : r === 'miss' ? 'Miss' : r === 'pending' ? 'Live' : '—';

  return (
    <>
      <TopBar title="Go Streaking" sub="Pick a hit, build a streak" back onBack={() => onNav('hub')} />
      <div className="app__scroll">
        <div className="page page--narrow">
          {!d ? (
            <div className="card card--pad center" style={{ padding: '48px 20px' }}><div className="dim" style={{ fontWeight: 600 }}>Loading…</div></div>
          ) : (
            <>
              {/* streak hero */}
              <div className="hero" style={{ background: 'linear-gradient(135deg, var(--c-green), var(--c-teal))' }}>
                <div className="hero__in">
                  <div className="eyebrow" style={{ color: 'rgba(255,255,255,.75)' }}>Current streak</div>
                  <div style={{ fontSize: '3.4rem', fontWeight: 900, lineHeight: 1 }}>{d.current}</div>
                  <p style={{ marginTop: 6 }}>Best run: <b>{d.best}</b>. One hit keeps it alive — a hitless day starts you over.</p>
                </div>
              </div>

              {/* tonight's pick */}
              <div className="section-head mt-24"><h2>Tonight's pick</h2><span className="muted" style={{ fontSize: '.85rem', fontWeight: 600 }}>{d.slate_date || '—'}</span></div>
              {!d.slate_date ? (
                <div className="card card--pad center" style={{ padding: '32px 20px' }}><div className="dim" style={{ fontWeight: 600 }}>No upcoming slate to pick. Sim forward to keep streaking.</div></div>
              ) : d.today_pick ? (
                <div className="contest">
                  <span className="contest__badge" style={{ background: 'var(--c-green)' }}>{(d.today_pick.team || '?').slice(0, 2)}</span>
                  <div style={{ minWidth: 0 }}>
                    <div className="contest__name">{d.today_pick.name}</div>
                    <div className="contest__meta"><span>{d.today_pick.team}</span><span>your pick — needs a hit</span></div>
                  </div>
                  <div className="contest__prize">
                    <div className="amt" style={{ color: resColor(d.today_pick.result) }}>{resLabel(d.today_pick.result)}</div>
                    <div className="lbl">{d.today_pick.result === 'pending' ? 'in progress' : 'result'}</div>
                  </div>
                </div>
              ) : (
                <>
                  <div className="search mb-12"><Icon name="search" size={17} /><input placeholder="Search hitters…" value={q} onChange={e => setQ(e.target.value)} /></div>
                  <div className="card" style={{ overflow: 'hidden' }}>
                    {shown.length === 0 && <div className="center muted" style={{ padding: 24, fontWeight: 600 }}>No hitters on the upcoming slate.</div>}
                    {shown.map(p => (
                      <div key={p.id} className="prow">
                        <div className="prow__id">
                          <PlayerMark p={{ init: p.init, teamColor: p.teamColor }} />
                          <div style={{ minWidth: 0 }}>
                            <div className="prow__name">{p.name}</div>
                            <div className="prow__sub"><span className="poscap">{p.pos}</span> · <span style={{ color: p.teamColor, fontWeight: 700 }}>{p.team}</span> {p.opp}</div>
                          </div>
                        </div>
                        <button className="add-btn" disabled={busy} title="Pick this hitter" onClick={() => pick(p)}>+</button>
                      </div>
                    ))}
                  </div>
                </>
              )}

              {/* history */}
              {d.history && d.history.length > 0 && (
                <>
                  <div className="section-head mt-24"><h2>Recent picks</h2></div>
                  <div className="card" style={{ overflow: 'hidden' }}>
                    {d.history.map((h, i) => (
                      <div key={i} className="lb-row">
                        <div className="lb-rank">{(h.slate_date || '').slice(5)}</div>
                        <div className="lb-user"><b style={{ fontSize: '.9rem' }}>{h.player}</b> <span className="dim">{h.team}</span></div>
                        <div className="lb-pts" style={{ color: resColor(h.result), fontWeight: 800 }}>{resLabel(h.result)}</div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

/* ---------- SLUGGERS (Walk-Back home-run game) ---------- */
function SluggersScreen({ onNav }) {
  const [data, setData] = useState(null);
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);

  function load() {
    fetch('/fantasy/api/sluggers').then(r => (r.ok ? r.json() : null)).then(setData).catch(() => setData(null));
  }
  useEffect(load, []);

  function act(path, p) {
    if (busy) return;
    setBusy(true);
    fetch('/fantasy/api/sluggers/' + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ player_id: p.id }) })
      .then(r => r.json()).then(j => { setBusy(false); if (!j.ok) window.alert(j.error || 'Could not do that.'); load(); })
      .catch(() => setBusy(false));
  }

  const d = data;
  const ys = d && d.your_slate;
  const picks = ys ? ys.picks : [];
  const pool = d ? (d.pool || []) : [];
  const shown = q.trim() ? pool.filter(p => p.name.toLowerCase().includes(q.toLowerCase())) : pool;
  const slotsLeft = d ? (d.max - (d.picked || 0)) : 0;

  return (
    <>
      <TopBar title="Sluggers" sub="Bank the bombs" back onBack={() => onNav('hub')} />
      <div className="app__scroll">
        <div className="page page--narrow">
          {!d ? (
            <div className="card card--pad center" style={{ padding: '48px 20px' }}><div className="dim" style={{ fontWeight: 600 }}>Loading…</div></div>
          ) : (
            <>
              {/* season hero */}
              <div className="hero" style={{ background: 'linear-gradient(135deg, var(--c-violet), var(--c-coral))' }}>
                <div className="hero__in">
                  <div className="eyebrow" style={{ color: 'rgba(255,255,255,.75)' }}>Season slugger points</div>
                  <div style={{ fontSize: '3.2rem', fontWeight: 900, lineHeight: 1 }}>{d.season}</div>
                  <p style={{ marginTop: 6 }}>HR <b>×4</b> · Walk-Back run <b>×4</b> · RBI <b>×1</b> — the homer plus the runs it brings home.</p>
                </div>
              </div>

              {/* tonight's sluggers */}
              <div className="section-head mt-24"><h2>Tonight's sluggers</h2><span className="muted" style={{ fontSize: '.85rem', fontWeight: 600 }}>{d.slate_date || '—'}</span></div>
              {picks.length > 0 && (
                <div className="card mb-12" style={{ overflow: 'hidden' }}>
                  {picks.map((p, i) => (
                    <div key={i} className="prow">
                      <div className="prow__id">
                        <span className="contest__badge" style={{ background: 'var(--c-violet)' }}>{(p.team || '?').slice(0, 2)}</span>
                        <div style={{ minWidth: 0 }}>
                          <div className="prow__name">{p.name}</div>
                          <div className="prow__sub"><span style={{ fontWeight: 700 }}>{p.team}</span> · {p.pts == null ? 'in progress' : `${p.pts} pts`}</div>
                        </div>
                      </div>
                      {ys && ys.settled ? <div className="lb-pts" style={{ fontWeight: 800 }}>{p.pts}</div>
                        : <button className="add-btn" disabled={busy} title="Drop" onClick={() => act('remove', p)} style={{ background: 'var(--down)' }}>−</button>}
                    </div>
                  ))}
                  {ys && (ys.fieldAvg != null) && (
                    <div className="lb-row" style={{ borderTop: '1px solid var(--line)' }}>
                      <div className="lb-user dim" style={{ fontWeight: 600 }}>Your {ys.score} · field avg {ys.fieldAvg} · ceiling {ys.ceiling}</div>
                    </div>
                  )}
                </div>
              )}

              {/* picker */}
              {!d.slate_date ? (
                <div className="card card--pad center" style={{ padding: '32px 20px' }}><div className="dim" style={{ fontWeight: 600 }}>No upcoming slate. Sim forward to keep slugging.</div></div>
              ) : slotsLeft > 0 ? (
                <>
                  <div className="muted mb-12" style={{ fontSize: '.85rem', fontWeight: 600 }}>{slotsLeft} slot{slotsLeft > 1 ? 's' : ''} left · sorted by power</div>
                  <div className="search mb-12"><Icon name="search" size={17} /><input placeholder="Search hitters…" value={q} onChange={e => setQ(e.target.value)} /></div>
                  <div className="card" style={{ overflow: 'hidden' }}>
                    {shown.length === 0 && <div className="center muted" style={{ padding: 24, fontWeight: 600 }}>No hitters on the upcoming slate.</div>}
                    {shown.map(p => (
                      <div key={p.id} className="prow">
                        <div className="prow__id">
                          <PlayerMark p={{ init: p.init, teamColor: p.teamColor }} />
                          <div style={{ minWidth: 0 }}>
                            <div className="prow__name">{p.name}</div>
                            <div className="prow__sub"><span className="poscap">{p.pos}</span> · <span style={{ color: p.teamColor, fontWeight: 700 }}>{p.team}</span> {p.opp} · <span title="power">PWR {p.power}</span></div>
                          </div>
                        </div>
                        <button className="add-btn" disabled={busy} title="Add slugger" onClick={() => act('pick', p)}>+</button>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <div className="card card--pad center" style={{ padding: '20px' }}><div className="dim" style={{ fontWeight: 600 }}>Lineup full — {d.max} sluggers locked for tonight.</div></div>
              )}

              {/* history */}
              {d.history && d.history.length > 0 && (
                <>
                  <div className="section-head mt-24"><h2>Past slates</h2></div>
                  <div className="card" style={{ overflow: 'hidden' }}>
                    {d.history.map((h, i) => (
                      <div key={i} className="lb-row">
                        <div className="lb-rank">{(h.slate_date || '').slice(5)}</div>
                        <div className="lb-user"><b style={{ fontSize: '.9rem' }}>{h.score} pts</b> <span className="dim">vs field {h.fieldAvg != null ? h.fieldAvg : '—'}</span></div>
                        <div className="lb-pts" style={{ color: (h.fieldAvg != null && h.score >= h.fieldAvg) ? 'var(--live)' : 'var(--ink-3)', fontWeight: 800 }}>{h.fieldAvg != null && h.score >= h.fieldAvg ? 'beat' : ''}</div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

/* ---------- PILOTS (pitching game) ---------- */
function PilotsScreen({ onNav }) {
  const [data, setData] = useState(null);
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);

  function load() {
    fetch('/fantasy/api/pilots').then(r => (r.ok ? r.json() : null)).then(setData).catch(() => setData(null));
  }
  useEffect(load, []);

  function act(path, p) {
    if (busy) return;
    setBusy(true);
    fetch('/fantasy/api/pilots/' + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ player_id: p.id }) })
      .then(r => r.json()).then(j => { setBusy(false); if (!j.ok) window.alert(j.error || 'Could not do that.'); load(); })
      .catch(() => setBusy(false));
  }

  const d = data;
  const ys = d && d.your_slate;
  const picks = ys ? ys.picks : [];
  const pool = d ? (d.pool || []) : [];
  const shown = q.trim() ? pool.filter(p => p.name.toLowerCase().includes(q.toLowerCase())) : pool;
  const slotsLeft = d ? (d.max - (d.picked || 0)) : 0;

  return (
    <>
      <TopBar title="Pilots" sub="Work the mound" back onBack={() => onNav('hub')} />
      <div className="app__scroll">
        <div className="page page--narrow">
          {!d ? (
            <div className="card card--pad center" style={{ padding: '48px 20px' }}><div className="dim" style={{ fontWeight: 600 }}>Loading…</div></div>
          ) : (
            <>
              <div className="hero" style={{ background: 'linear-gradient(135deg, var(--c-blue), var(--c-teal))' }}>
                <div className="hero__in">
                  <div className="eyebrow" style={{ color: 'rgba(255,255,255,.75)' }}>Season pilot points</div>
                  <div style={{ fontSize: '3.2rem', fontWeight: 900, lineHeight: 1 }}>{d.season}</div>
                  <p style={{ marginTop: 6 }}>K <b>×3</b> · Out <b>×1</b> · ER <b>−2</b> · Quality Start <b>+6</b> · Quality Finish <b>+6</b>.</p>
                </div>
              </div>

              <div className="section-head mt-24"><h2>Tonight's pilots</h2><span className="muted" style={{ fontSize: '.85rem', fontWeight: 600 }}>{d.slate_date || '—'}</span></div>
              {picks.length > 0 && (
                <div className="card mb-12" style={{ overflow: 'hidden' }}>
                  {picks.map((p, i) => (
                    <div key={i} className="prow">
                      <div className="prow__id">
                        <span className="contest__badge" style={{ background: 'var(--c-blue)' }}>{(p.team || '?').slice(0, 2)}</span>
                        <div style={{ minWidth: 0 }}>
                          <div className="prow__name">{p.name}</div>
                          <div className="prow__sub"><span style={{ fontWeight: 700 }}>{p.team}</span> · {p.pts == null ? 'in progress' : `${p.pts} pts`}</div>
                        </div>
                      </div>
                      {ys && ys.settled ? <div className="lb-pts" style={{ fontWeight: 800 }}>{p.pts}</div>
                        : <button className="add-btn" disabled={busy} title="Drop" onClick={() => act('remove', p)} style={{ background: 'var(--down)' }}>−</button>}
                    </div>
                  ))}
                  {ys && (ys.fieldAvg != null) && (
                    <div className="lb-row" style={{ borderTop: '1px solid var(--line)' }}>
                      <div className="lb-user dim" style={{ fontWeight: 600 }}>Your {ys.score} · field avg {ys.fieldAvg} · ceiling {ys.ceiling}</div>
                    </div>
                  )}
                </div>
              )}

              {!d.slate_date ? (
                <div className="card card--pad center" style={{ padding: '32px 20px' }}><div className="dim" style={{ fontWeight: 600 }}>No upcoming slate. Sim forward to keep pitching.</div></div>
              ) : slotsLeft > 0 ? (
                <>
                  <div className="muted mb-12" style={{ fontSize: '.85rem', fontWeight: 600 }}>{slotsLeft} slot{slotsLeft > 1 ? 's' : ''} left · sorted by projection</div>
                  <div className="search mb-12"><Icon name="search" size={17} /><input placeholder="Search pilots…" value={q} onChange={e => setQ(e.target.value)} /></div>
                  <div className="card" style={{ overflow: 'hidden' }}>
                    {shown.length === 0 && <div className="center muted" style={{ padding: 24, fontWeight: 600 }}>No pilots on the upcoming slate.</div>}
                    {shown.map(p => (
                      <div key={p.id} className="prow">
                        <div className="prow__id">
                          <PlayerMark p={{ init: p.init, teamColor: p.teamColor }} />
                          <div style={{ minWidth: 0 }}>
                            <div className="prow__name">{p.name}</div>
                            <div className="prow__sub"><span className="poscap">{p.pos}</span> · <span style={{ color: p.teamColor, fontWeight: 700 }}>{p.team}</span> {p.opp} · <span title="projection">proj {p.proj}</span></div>
                          </div>
                        </div>
                        <button className="add-btn" disabled={busy} title="Add pilot" onClick={() => act('pick', p)}>+</button>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <div className="card card--pad center" style={{ padding: '20px' }}><div className="dim" style={{ fontWeight: 600 }}>Staff full — {d.max} pilots locked for tonight.</div></div>
              )}

              {d.history && d.history.length > 0 && (
                <>
                  <div className="section-head mt-24"><h2>Past slates</h2></div>
                  <div className="card" style={{ overflow: 'hidden' }}>
                    {d.history.map((h, i) => (
                      <div key={i} className="lb-row">
                        <div className="lb-rank">{(h.slate_date || '').slice(5)}</div>
                        <div className="lb-user"><b style={{ fontSize: '.9rem' }}>{h.score} pts</b> <span className="dim">vs field {h.fieldAvg != null ? h.fieldAvg : '—'}</span></div>
                        <div className="lb-pts" style={{ color: (h.fieldAvg != null && h.score >= h.fieldAvg) ? 'var(--live)' : 'var(--ink-3)', fontWeight: 800 }}>{h.fieldAvg != null && h.score >= h.fieldAvg ? 'beat' : ''}</div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

/* ---------- CATEGORY LEAGUES (Roto engine) ---------- */
function CategoriesScreen({ onNav }) {
  const [fmt, setFmt] = useState('std5x5');
  const [data, setData] = useState(null);
  const [pool, setPool] = useState(null);
  const [sel, setSel] = useState([]);
  const [editing, setEditing] = useState(false);
  const [side, setSide] = useState('h');
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);

  function loadPool(f) {
    fetch('/fantasy/api/categories/pool?format=' + f).then(r => r.ok ? r.json() : { hitters: [], pitchers: [] }).then(setPool).catch(() => setPool({ hitters: [], pitchers: [] }));
  }
  function loadState(f) {
    fetch('/fantasy/api/categories?format=' + f).then(r => r.ok ? r.json() : null).then(d => {
      setData(d);
      const complete = d && d.standings;
      setEditing(!complete);
      if (!complete) loadPool(f);
    }).catch(() => setData(null));
  }
  useEffect(() => { setSel([]); setQ(''); loadState(fmt); }, [fmt]);

  const slots = data ? data.slots : { h: 0, p: 0 };
  const nH = sel.filter(s => s.pos !== 'P').length;
  const nP = sel.filter(s => s.pos === 'P').length;
  const full = nH === slots.h && nP === slots.p;
  const selIds = new Set(sel.map(s => s.id));
  const curFmt = (data && data.formats || []).find(f => f.key === fmt) || {};

  function add(item) {
    if (selIds.has(item.id)) return;
    const isP = item.pos === 'P';
    if (isP && nP >= slots.p) return;
    if (!isP && nH >= slots.h) return;
    setSel([...sel, item]);
  }
  function lock() {
    if (!full || busy) return;
    setBusy(true);
    fetch('/fantasy/api/categories/draft', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ format: fmt, player_ids: sel.map(s => s.id) }) })
      .then(r => r.json()).then(j => { setBusy(false); if (!j.ok) { window.alert(j.error || 'Draft failed.'); return; } setEditing(false); loadState(fmt); })
      .catch(() => setBusy(false));
  }
  function reDraft() { setSel((data.roster || []).map(r => ({ ...r, pos: r.pos === 'P' ? 'P' : 'H' }))); loadPool(fmt); setEditing(true); }

  const st = data && data.standings;
  const onlyOneSide = slots.h === 0 || slots.p === 0;
  const showSide = onlyOneSide ? (slots.p === 0 ? 'h' : 'p') : side;
  const list = pool ? (showSide === 'p' ? pool.pitchers : pool.hitters) : [];
  const shown = q.trim() ? list.filter(p => p.name.toLowerCase().includes(q.toLowerCase())) : list;
  const rankColor = (r, field) => r === 1 ? 'var(--live)' : r <= Math.ceil(field / 3) ? 'var(--c-teal)' : r >= field - Math.ceil(field / 3) ? 'var(--down)' : 'var(--ink-2)';

  return (
    <>
      <TopBar title="Category Leagues" sub="Season-long Roto" back onBack={() => onNav('hub')} />
      <div className="app__scroll">
        <div className="page page--narrow">
          {/* format tabs */}
          <div className="slate-tabs mb-12" style={{ overflowX: 'auto', flexWrap: 'nowrap' }}>
            {(data && data.formats || []).map(f => (
              <Chip key={f.key} active={f.key === fmt} onClick={() => setFmt(f.key)}>{f.name}</Chip>
            ))}
          </div>
          {!data ? (
            <div className="card card--pad center" style={{ padding: '48px 20px' }}><div className="dim" style={{ fontWeight: 600 }}>Loading…</div></div>
          ) : (
            <>
              <p className="muted mb-12" style={{ fontSize: '.86rem', lineHeight: 1.45 }}>{curFmt.blurb}</p>

              {st && !editing ? (
                /* ---- standings ---- */
                <>
                  <div className="hero" style={{ background: st.dq ? 'linear-gradient(135deg,var(--down),var(--c-amber))' : 'linear-gradient(135deg, var(--c-teal), var(--c-blue))' }}>
                    <div className="hero__in">
                      <div className="eyebrow" style={{ color: 'rgba(255,255,255,.75)' }}>Roto points · rank</div>
                      <div style={{ fontSize: '3rem', fontWeight: 900, lineHeight: 1 }}>{st.roto} <span style={{ fontSize: '1.3rem', opacity: .8 }}>/ {st.max_points}</span></div>
                      <p style={{ marginTop: 6 }}><b>#{st.rank}</b> of {st.field}{st.dq ? ' · DQ — below the AB/out floor, roster players who play!' : ''}</p>
                    </div>
                  </div>
                  <div className="section-head mt-24"><h2>Categories</h2><span className="muted" style={{ fontSize: '.8rem', fontWeight: 600 }}>value · rank · pts</span></div>
                  <div className="card" style={{ overflow: 'hidden' }}>
                    {st.categories.map(c => (
                      <div key={c.key} className="lb-row">
                        <div className="lb-rank" style={{ fontWeight: 800 }}>{c.label}</div>
                        <div className="lb-user"><b style={{ fontSize: '.95rem' }}>{c.value}</b></div>
                        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                          <span className="pill" style={{ background: rankColor(c.rank, st.field), color: '#fff', fontWeight: 800, padding: '2px 8px', borderRadius: 8, fontSize: '.78rem' }}>#{c.rank}</span>
                          <span className="lb-pts" style={{ fontWeight: 800, minWidth: 34, textAlign: 'right' }}>{c.points}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="section-head mt-24"><h2>Your roster</h2><button className="btn btn--ghost btn--sm" onClick={reDraft}>Re-draft</button></div>
                  <div className="card" style={{ overflow: 'hidden' }}>
                    {(data.roster || []).map(p => (
                      <div key={p.id} className="prow">
                        <div className="prow__id">
                          <span className="contest__badge" style={{ background: p.pos === 'P' ? 'var(--c-blue)' : 'var(--c-violet)' }}>{(p.team || '?').slice(0, 2)}</span>
                          <div style={{ minWidth: 0 }}><div className="prow__name">{p.name}</div><div className="prow__sub">{p.pos === 'P' ? 'Pitcher' : 'Hitter'} · {p.team}</div></div>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                /* ---- draft ---- */
                <>
                  <div className="card card--pad mb-12" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div style={{ fontWeight: 700, fontSize: '.9rem' }}>
                      {slots.h > 0 && <span style={{ color: nH === slots.h ? 'var(--live)' : 'var(--ink-2)' }}>Hitters {nH}/{slots.h}</span>}
                      {slots.h > 0 && slots.p > 0 && <span className="dim"> · </span>}
                      {slots.p > 0 && <span style={{ color: nP === slots.p ? 'var(--live)' : 'var(--ink-2)' }}>Pitchers {nP}/{slots.p}</span>}
                    </div>
                    <button className="btn btn--brand btn--sm" disabled={!full || busy} onClick={lock}>Lock roster</button>
                  </div>
                  {sel.length > 0 && (
                    <div className="chips mb-12" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {sel.map(s => (
                        <button key={s.id} className="chip" onClick={() => setSel(sel.filter(x => x.id !== s.id))} style={{ background: 'var(--surface-2)', border: '1px solid var(--line)', borderRadius: 14, padding: '4px 10px', fontSize: '.8rem', fontWeight: 700 }}>
                          {s.name} <span className="dim">{s.pos}</span> ×
                        </button>
                      ))}
                    </div>
                  )}
                  {!onlyOneSide && (
                    <div className="slate-tabs mb-12">
                      <Chip active={showSide === 'h'} onClick={() => setSide('h')}>Hitters</Chip>
                      <Chip active={showSide === 'p'} onClick={() => setSide('p')}>Pitchers</Chip>
                    </div>
                  )}
                  <div className="search mb-12"><Icon name="search" size={17} /><input placeholder={'Search ' + (showSide === 'p' ? 'pitchers' : 'hitters') + '…'} value={q} onChange={e => setQ(e.target.value)} /></div>
                  {curFmt.invert && <div className="muted mb-12" style={{ fontSize: '.8rem', fontWeight: 600, color: 'var(--c-amber)' }}>Anti-league: worst production wins — but you must clear the playing-time floor.</div>}
                  <div className="card" style={{ overflow: 'hidden' }}>
                    {!pool && <div className="center muted" style={{ padding: 24, fontWeight: 600 }}>Loading pool…</div>}
                    {pool && shown.length === 0 && <div className="center muted" style={{ padding: 24, fontWeight: 600 }}>No players found.</div>}
                    {shown.slice(0, 120).map(p => (
                      <div key={p.id} className="prow">
                        <div className="prow__id">
                          <span className="contest__badge" style={{ background: p.pos === 'P' ? 'var(--c-blue)' : 'var(--c-violet)' }}>{(p.team || '?').slice(0, 2)}</span>
                          <div style={{ minWidth: 0 }}>
                            <div className="prow__name">{p.name}</div>
                            <div className="prow__sub" style={{ fontSize: '.74rem' }}><b>{p.pos}</b> · {p.line}</div>
                          </div>
                        </div>
                        <button className="add-btn" disabled={selIds.has(p.id)} title="Draft" onClick={() => add(p)}>{selIds.has(p.id) ? '✓' : '+'}</button>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

/* ---------- SPORTSBOOK ---------- */
function SportsbookScreen({ onNav }) {
  const [data, setData] = useState(null);
  const [slip, setSlip] = useState(null);   // {game_id, market, side, odds, line, label}
  const [stake, setStake] = useState(25);
  const [busy, setBusy] = useState(false);

  function load() {
    fetch('/fantasy/api/sportsbook').then(r => r.ok ? r.json() : null).then(setData).catch(() => setData(null));
  }
  useEffect(load, []);

  const od = n => (n > 0 ? '+' + n : '' + n);
  const dec = o => (o > 0 ? 1 + o / 100 : 1 + 100 / Math.abs(o));

  function pick(g, market, side, odds, line, label) {
    setSlip({ game_id: g.game_id, market, side, odds, line, label });
  }
  function place() {
    if (!slip || busy) return;
    setBusy(true);
    fetch('/fantasy/api/sportsbook/bet', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ game_id: slip.game_id, market: slip.market, side: slip.side, stake }) })
      .then(r => r.json()).then(j => { setBusy(false); if (!j.ok) { window.alert(j.error || 'Bet rejected.'); return; } setSlip(null); load(); })
      .catch(() => setBusy(false));
  }

  const d = data;
  const sel = (g, market, side) => slip && slip.game_id === g.game_id && slip.market === market && slip.side === side;
  const oddsBtn = (g, market, side, odds, line, label) => (
    <button className={'odds-btn' + (sel(g, market, side) ? ' is-on' : '')}
      onClick={() => pick(g, market, side, odds, line, label)}
      style={{ flex: 1, padding: '8px 6px', borderRadius: 10, border: '1px solid var(--line-2)', fontWeight: 700, fontSize: '.78rem', lineHeight: 1.3, background: sel(g, market, side) ? 'var(--brand-soft)' : 'var(--card)', color: sel(g, market, side) ? 'var(--brand-ink)' : 'var(--ink)' }}>
      <div style={{ fontSize: '.72rem', opacity: .7 }}>{label}</div>
      <div>{od(odds)}</div>
    </button>
  );
  const statusColor = s => s === 'won' ? 'var(--live)' : s === 'lost' ? 'var(--down)' : 'var(--ink-3)';

  return (
    <>
      <TopBar title="Sportsbook" sub="Beat the house" back onBack={() => onNav('hub')} />
      <div className="app__scroll">
        <div className="page page--narrow">
          {!d ? (
            <div className="card card--pad center" style={{ padding: '48px 20px' }}><div className="dim" style={{ fontWeight: 600 }}>Loading…</div></div>
          ) : (
            <>
              <div className="hero" style={{ background: 'linear-gradient(135deg, var(--c-amber), var(--c-coral))' }}>
                <div className="hero__in">
                  <div className="eyebrow" style={{ color: 'rgba(255,255,255,.75)' }}>Bankroll · units</div>
                  <div style={{ fontSize: '3rem', fontWeight: 900, lineHeight: 1 }}>{d.bankroll}</div>
                  <p style={{ marginTop: 6 }}>{d.record.w}–{d.record.l}{d.record.p ? `–${d.record.p}` : ''} · net <b>{d.record.net > 0 ? '+' : ''}{d.record.net}</b>{d.at_risk ? ` · ${d.at_risk} at risk` : ''}</p>
                </div>
              </div>

              <div className="section-head mt-24"><h2>Tonight's board</h2><span className="muted" style={{ fontSize: '.85rem', fontWeight: 600 }}>{d.slate_date || '—'}</span></div>
              {d.games.length === 0 && <div className="card card--pad center" style={{ padding: '24px' }}><div className="dim" style={{ fontWeight: 600 }}>No games open for betting.</div></div>}
              {d.games.map(g => (
                <div key={g.game_id} className="card card--pad mb-12">
                  <div style={{ fontWeight: 800, marginBottom: 8 }}>{g.away} <span className="dim">@</span> {g.home}</div>
                  <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
                    {oddsBtn(g, 'ml', 'away', g.ml_away, null, g.away + ' ML')}
                    {oddsBtn(g, 'ml', 'home', g.ml_home, null, g.home + ' ML')}
                  </div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    {oddsBtn(g, 'total', 'over', g.over_odds, g.total, 'Over ' + g.total)}
                    {oddsBtn(g, 'total', 'under', g.under_odds, g.total, 'Under ' + g.total)}
                  </div>
                </div>
              ))}

              {d.open.length > 0 && (
                <>
                  <div className="section-head mt-24"><h2>Open bets</h2></div>
                  <div className="card" style={{ overflow: 'hidden' }}>
                    {d.open.map(b => (
                      <div key={b.id} className="lb-row">
                        <div className="lb-user"><b style={{ fontSize: '.9rem' }}>{b.desc}</b> <span className="dim">{od(b.odds)} · {b.matchup}</span></div>
                        <div className="lb-pts" style={{ fontWeight: 700 }}>{b.stake}u</div>
                      </div>
                    ))}
                  </div>
                </>
              )}

              {d.settled.length > 0 && (
                <>
                  <div className="section-head mt-24"><h2>Settled</h2></div>
                  <div className="card" style={{ overflow: 'hidden' }}>
                    {d.settled.map(b => (
                      <div key={b.id} className="lb-row">
                        <div className="lb-user"><b style={{ fontSize: '.9rem' }}>{b.desc}</b> <span className="dim">{b.matchup} {b.score ? `(${b.score})` : ''}</span></div>
                        <div className="lb-pts" style={{ color: statusColor(b.status), fontWeight: 800 }}>
                          {b.status === 'won' ? `+${(b.payout - b.stake).toFixed(0)}` : b.status === 'lost' ? `−${b.stake}` : 'push'}
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>

      {/* bet slip */}
      {slip && (
        <div className="betslip" style={{ position: 'sticky', bottom: 0, left: 0, right: 0, background: 'var(--card)', borderTop: '1px solid var(--line-2)', padding: '14px 18px', boxShadow: '0 -6px 18px rgba(0,0,0,.12)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <div style={{ fontWeight: 800 }}>{slip.label} <span className="dim">{od(slip.odds)}</span></div>
            <button className="btn btn--ghost btn--sm" onClick={() => setSlip(null)}>Cancel</button>
          </div>
          <div className="slate-tabs mb-12">
            {[10, 25, 50, 100].map(v => <Chip key={v} active={stake === v} onClick={() => setStake(v)}>{v}u</Chip>)}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="dim" style={{ fontWeight: 600, fontSize: '.85rem' }}>Risk {stake}u → win {(stake * (dec(slip.odds) - 1)).toFixed(0)}u</div>
            <button className="btn btn--brand" disabled={busy || stake > (d ? d.bankroll : 0)} onClick={place}>Place {stake}u</button>
          </div>
        </div>
      )}
    </>
  );
}

/* ---------- BEST BALL ---------- */
function BestBallScreen({ onNav }) {
  const [data, setData] = useState(null);
  const [pool, setPool] = useState(null);
  const [sel, setSel] = useState([]);
  const [editing, setEditing] = useState(false);
  const [side, setSide] = useState('h');
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);

  function loadPool() {
    fetch('/fantasy/api/bestball/pool').then(r => r.ok ? r.json() : { hitters: [], pitchers: [] }).then(setPool).catch(() => setPool({ hitters: [], pitchers: [] }));
  }
  function load() {
    fetch('/fantasy/api/bestball').then(r => r.ok ? r.json() : null).then(d => {
      setData(d);
      const complete = d && d.standings;
      setEditing(!complete);
      if (!complete) loadPool();
    }).catch(() => setData(null));
  }
  useEffect(load, []);

  const slots = data ? data.slots : { h: 0, p: 0 };
  const nH = sel.filter(s => s.pos !== 'P').length;
  const nP = sel.filter(s => s.pos === 'P').length;
  const full = nH === slots.h && nP === slots.p;
  const req = (data && data.require) || {};
  const haveByPos = {};
  sel.forEach(s => { if (s.pos !== 'P') haveByPos[s.pos] = (haveByPos[s.pos] || 0) + 1; });
  const posOk = Object.entries(req).every(([p, n]) => (haveByPos[p] || 0) >= n);
  const canLock = full && posOk;
  const selIds = new Set(sel.map(s => s.id));
  const list = pool ? (side === 'p' ? pool.pitchers : pool.hitters) : [];
  const shown = q.trim() ? list.filter(p => p.name.toLowerCase().includes(q.toLowerCase())) : list;
  const st = data && data.standings;

  function add(item) {
    if (selIds.has(item.id)) return;
    const isP = item.pos === 'P';
    if (isP && nP >= slots.p) return;
    if (!isP && nH >= slots.h) return;
    setSel([...sel, item]);
  }
  function lock() {
    if (!full || busy) return;
    setBusy(true);
    fetch('/fantasy/api/bestball/draft', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ player_ids: sel.map(s => s.id) }) })
      .then(r => r.json()).then(j => { setBusy(false); if (!j.ok) { window.alert(j.error || 'Draft failed.'); return; } setEditing(false); load(); })
      .catch(() => setBusy(false));
  }
  function reDraft() { setSel((data.roster || []).map(r => ({ ...r, pos: r.pos === 'P' ? 'P' : 'H' }))); loadPool(); setEditing(true); }

  return (
    <>
      <TopBar title="Best Ball" sub="Draft once, never touch it" back onBack={() => onNav('hub')} />
      <div className="app__scroll">
        <div className="page page--narrow">
          {!data ? (
            <div className="card card--pad center" style={{ padding: '48px 20px' }}><div className="dim" style={{ fontWeight: 600 }}>Loading…</div></div>
          ) : st && !editing ? (
            /* ---- standings ---- */
            <>
              <div className="hero" style={{ background: 'linear-gradient(135deg, var(--c-lime), var(--c-teal))' }}>
                <div className="hero__in">
                  <div className="eyebrow" style={{ color: 'rgba(255,255,255,.75)' }}>Season points · rank</div>
                  <div style={{ fontSize: '3rem', fontWeight: 900, lineHeight: 1 }}>{st.score}</div>
                  <p style={{ marginTop: 6 }}><b>#{st.rank}</b> of {st.field} · {st.pct}th pct · field avg {st.field_avg}, best {st.field_best}</p>
                </div>
              </div>
              <p className="muted mt-12 mb-12" style={{ fontSize: '.84rem', lineHeight: 1.45 }}>Auto-lineup: {st.lineup}. No management — your draft is the whole game.</p>
              <div className="section-head mt-12"><h2>Your roster</h2><button className="btn btn--ghost btn--sm" onClick={reDraft}>Re-draft</button></div>
              <div className="card" style={{ overflow: 'hidden' }}>
                {(data.roster || []).map(p => (
                  <div key={p.id} className="prow">
                    <div className="prow__id">
                      <span className="contest__badge" style={{ background: p.pos === 'P' ? 'var(--c-blue)' : 'var(--c-violet)' }}>{(p.team || '?').slice(0, 2)}</span>
                      <div style={{ minWidth: 0 }}><div className="prow__name">{p.name}</div><div className="prow__sub">{p.pos === 'P' ? 'Pitcher' : 'Hitter'} · {p.team}</div></div>
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            /* ---- draft ---- */
            <>
              <p className="muted mb-12" style={{ fontSize: '.86rem', lineHeight: 1.45 }}>Draft {slots.h} hitters and {slots.p} pitchers covering every slot. Each slate your best in-position lineup — C, 1B, 2B, 3B, SS, OF, OF + best 2 pitchers — auto-scores, so draft depth at a spot and the hot bat there starts itself.</p>
              <div className="card card--pad mb-12" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ fontWeight: 700, fontSize: '.9rem' }}>
                  <span style={{ color: nH === slots.h ? 'var(--live)' : 'var(--ink-2)' }}>Hitters {nH}/{slots.h}</span>
                  <span className="dim"> · </span>
                  <span style={{ color: nP === slots.p ? 'var(--live)' : 'var(--ink-2)' }}>Pitchers {nP}/{slots.p}</span>
                </div>
                <button className="btn btn--brand btn--sm" disabled={!canLock || busy} onClick={lock}>Lock roster</button>
              </div>
              <div className="chips mb-12" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {Object.entries(req).map(([p, n]) => {
                  const ok = (haveByPos[p] || 0) >= n;
                  return <span key={p} style={{ fontSize: '.76rem', fontWeight: 800, padding: '3px 9px', borderRadius: 12, background: ok ? 'var(--brand-soft)' : 'var(--card-2)', color: ok ? 'var(--brand-ink)' : 'var(--ink-3)', border: '1px solid var(--line)' }}>{ok ? '✓ ' : ''}{p}{n > 1 ? ` ×${n}` : ''}</span>;
                })}
              </div>
              {sel.length > 0 && (
                <div className="chips mb-12" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {sel.map(s => (
                    <button key={s.id} className="chip" onClick={() => setSel(sel.filter(x => x.id !== s.id))} style={{ background: 'var(--surface-2)', border: '1px solid var(--line)', borderRadius: 14, padding: '4px 10px', fontSize: '.8rem', fontWeight: 700 }}>
                      {s.name} <span className="dim">{s.pos}</span> ×
                    </button>
                  ))}
                </div>
              )}
              <div className="slate-tabs mb-12">
                <Chip active={side === 'h'} onClick={() => setSide('h')}>Hitters</Chip>
                <Chip active={side === 'p'} onClick={() => setSide('p')}>Pitchers</Chip>
              </div>
              <div className="search mb-12"><Icon name="search" size={17} /><input placeholder={'Search ' + (side === 'p' ? 'pitchers' : 'hitters') + '…'} value={q} onChange={e => setQ(e.target.value)} /></div>
              <div className="card" style={{ overflow: 'hidden' }}>
                {!pool && <div className="center muted" style={{ padding: 24, fontWeight: 600 }}>Loading pool…</div>}
                {pool && shown.length === 0 && <div className="center muted" style={{ padding: 24, fontWeight: 600 }}>No players found.</div>}
                {shown.slice(0, 120).map(p => (
                  <div key={p.id} className="prow">
                    <div className="prow__id">
                      <span className="contest__badge" style={{ background: p.pos === 'P' ? 'var(--c-blue)' : 'var(--c-violet)' }}>{(p.team || '?').slice(0, 2)}</span>
                      <div style={{ minWidth: 0 }}>
                        <div className="prow__name">{p.name}</div>
                        <div className="prow__sub" style={{ fontSize: '.74rem' }}><b>{p.pos}</b> · {p.line}</div>
                      </div>
                    </div>
                    <button className="add-btn" disabled={selIds.has(p.id)} title="Draft" onClick={() => add(p)}>{selIds.has(p.id) ? '✓' : '+'}</button>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}

Object.assign(window, { HubScreen, LobbyScreen, EntriesScreen, FormatTeaser, StreakScreen, SluggersScreen, PilotsScreen, CategoriesScreen, SportsbookScreen, BestBallScreen });
