/* ============================================================
   SLATE — screens part 1: Hub, Lobby, Entries, Player drawer
   ============================================================ */
const { useState, useEffect } = React;

/* ---------- HUB / GAME LIBRARY ---------- */
function HubScreen({ onNav, onOpenFormat }) {
  const S = window.SLATE;
  return (
    <>
      <TopBar title="Good evening, Player!" sub="4 games on tonight's slate" right={
        <button className="btn btn--ghost btn--sm hide-mobile" style={{ padding: '9px 12px' }}><Icon name="bell" size={18} /></button>
      } />
      <div className="app__scroll">
        <div className="page">
          {/* hero */}
          <div className="hero">
            <div className="hero__in">
              <Tag kind="live"><span className="pulse" /> Slate locks 6:05</Tag>
              <h1 className="mt-12">Tonight's Daily Slate is live.</h1>
              <p>Build a lineup under the {S.money(S.CAP)} cap across four games. {S.money(50*S.CRORE)} in prizes on the board.</p>
              <div className="row wrap">
                <Btn variant="brand" size="lg" onClick={() => onNav('lobby')}>Enter the Crore Room <Icon name="chev" size={18} /></Btn>
                <Btn variant="ghost" size="lg" onClick={() => onNav('lobby')}>Browse contests</Btn>
              </div>
            </div>
          </div>

          {/* quick tiles */}
          <div className="tiles mt-24">
            <div className="tile"><div className="lbl">Balance</div><div className="val">{S.money(S.WALLET)}</div><div className="sub">Guilder wallet</div></div>
            <div className="tile"><div className="lbl">Live entries</div><div className="val">3</div><div className="sub" style={{ color: 'var(--live)' }}>+{S.money(42*S.LAKH)} winning</div></div>
            <div className="tile"><div className="lbl">Tonight</div><div className="val">4</div><div className="sub">games · 6:05 first lock</div></div>
            <div className="tile"><div className="lbl">Win streak</div><div className="val">5</div><div className="sub">Beat the Voyage</div></div>
          </div>

          {/* the game library */}
          <div className="section-head mt-32">
            <h2>Game library</h2>
            <span className="muted" style={{ fontSize: '.85rem', fontWeight: 600, whiteSpace: 'nowrap' }}>Eight ways to play your league</span>
          </div>
          <div className="fmt-grid">
            {S.FORMATS.map(f => (
              <a key={f.id} className={'fmt' + (f.live ? '' : ' fmt--soon')} onClick={() => onOpenFormat(f)}>
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

Object.assign(window, { HubScreen, LobbyScreen, EntriesScreen, FormatTeaser });
