/* ============================================================
   SLATE — screens part 2: Lineup Builder, Live scoring, Player drawer
   ============================================================ */
const { useState, useEffect } = React;

/* color for a 20-80 rating */
function ratingColor(v) {
  if (v >= 68) return 'var(--live)';
  if (v >= 58) return 'var(--brand)';
  if (v >= 48) return 'var(--amber)';
  return 'var(--down)';
}

/* ---------- LINEUP BUILDER ---------- */
function BuilderScreen({ contest, roster, onAdd, onRemove, onOpenPlayer, onEnter, onNav }) {
  const S = window.SLATE;
  const [q, setQ] = useState('');
  const [posF, setPosF] = useState('ALL');
  const [sort, setSort] = useState('proj');
  const [affordOnly, setAffordOnly] = useState(false);

  const chosenIds = Object.values(roster).filter(Boolean).map(p => p.id);
  const used = Object.values(roster).filter(Boolean).reduce((s, p) => s + p.salary, 0);
  const rem = S.CAP - used;
  const filled = chosenIds.length;
  const openSlots = S.SLOTS.length - filled;
  const projPts = Object.values(roster).filter(Boolean).reduce((s, p) => s + p.proj, 0);
  const perSlot = openSlots > 0 ? rem / openSlots : 0;
  const over = rem < 0;

  // Cheapest salary in the pool — used to reserve money for the slots you still
  // have to fill, so "affordable" means you can still complete a legal lineup.
  const minSal = S.PLAYERS.reduce((m, p) => Math.min(m, p.salary || Infinity), Infinity);
  // After adding this player you'd have (openSlots - 1) slots left, each needing
  // at least the min salary. Affordable = it fits AND leaves enough for the rest.
  function canAfford(p) {
    const reserve = minSal === Infinity ? 0 : minSal * Math.max(0, openSlots - 1);
    return p.salary <= rem - reserve;
  }

  const positions = ['ALL', 'PILOT', 'C', '1B', '2B', '3B', 'SS', 'OF'];
  let pool = S.PLAYERS.filter(p => !chosenIds.includes(p.id));
  if (posF !== 'ALL') pool = pool.filter(p => ((p.posEligible && p.posEligible.length) ? p.posEligible : [p.pos]).includes(posF));
  if (q.trim()) pool = pool.filter(p => p.name.toLowerCase().includes(q.toLowerCase()));
  if (affordOnly) pool = pool.filter(canAfford);
  pool = [...pool].sort((a, b) => sort === 'salary' ? b.salary - a.salary : sort === 'value' ? b.value - a.value : b.proj - a.proj);

  return (
    <>
      <TopBar title={contest ? contest.name : 'Build lineup'} sub={`Daily Slate · ${S.money(S.CAP)} cap`} back onBack={() => onNav('lobby')} />
      <div className="app__scroll">
        <div className="page">
          {/* cap meter */}
          <div className="cap mb-16">
            <div className="cap__cell"><div className="lbl">Salary left</div><div className={'val ' + (over ? 'warn' : 'ok')}>{over ? '-' : ''}{S.money(Math.abs(rem))}</div></div>
            <div className="cap__cell"><div className="lbl">Avg / open slot</div><div className="val">{openSlots ? S.money(Math.max(0, perSlot)) : '—'}</div></div>
            <div className="cap__cell"><div className="lbl">Proj points</div><div className="val" style={{ color: 'var(--amber)' }}>{projPts.toFixed(1)}</div></div>
            <div className="cap__bar"><i className={over ? 'over' : ''} style={{ width: Math.min(100, (used / S.CAP) * 100) + '%' }} /></div>
          </div>

          <div className="builder">
            {/* roster — kept first so it sits up top on mobile (your picks at a glance) */}
            <div className="roster">
              <div className="card card--pad">
                <div className="roster__head">
                  <h3>Your lineup</h3>
                  <span className="num muted" style={{ fontWeight: 700 }}>{filled}/{S.SLOTS.length}</span>
                </div>
                {S.SLOTS.map(slot => {
                  const p = roster[slot.key];
                  return (
                    <div key={slot.key} className={'slot' + (p ? ' slot--filled' : '')}>
                      <span className="slot__pos">{slot.label}</span>
                      {p ? (
                        <>
                          <div className="slot__body" onClick={() => onOpenPlayer(p)} style={{ cursor: 'pointer' }}>
                            <div className="slot__name">{p.name}</div>
                            <div className="prow__sub"><span style={{ color: p.teamColor, fontWeight: 700 }}>{p.team}</span> {p.opp} · <span className="num" style={{ color: 'var(--brand)' }}>{p.proj.toFixed(1)} proj</span></div>
                          </div>
                          <span className="slot__sal num">{S.money(p.salary)}</span>
                          <button className="slot__x" onClick={() => onRemove(slot.key)}><Icon name="x" size={16} /></button>
                        </>
                      ) : (
                        <div className="slot__body"><span className="slot__empty">{slot.flex ? 'Flex — any hitter' : 'Empty'}</span></div>
                      )}
                    </div>
                  );
                })}
                <Btn variant={filled === S.SLOTS.length && !over ? 'brand' : 'ghost'} block className="mt-12"
                  disabled={filled !== S.SLOTS.length || over} onClick={onEnter}>
                  {over ? 'Over the cap' : filled === S.SLOTS.length ? <>Enter contest · {S.money(contest ? contest.fee : 0)} <Icon name="chev" size={18} /></> : `Fill ${S.SLOTS.length - filled} more`}
                </Btn>
                <button className="btn btn--ghost btn--sm btn--block mt-8" onClick={() => S.SLOTS.forEach(s => onRemove(s.key))}>Clear lineup</button>
              </div>
            </div>

            {/* player pool */}
            <div className="pool">
              <div className="pool__bar">
                <div className="search">
                  <Icon name="search" size={17} />
                  <input placeholder="Search players…" value={q} onChange={e => setQ(e.target.value)} />
                </div>
                <select className="chip" value={sort} onChange={e => setSort(e.target.value)} style={{ paddingRight: 24 }}>
                  <option value="proj">Sort: Proj</option>
                  <option value="salary">Sort: Salary</option>
                  <option value="value">Sort: Value</option>
                </select>
              </div>
              <div className="pool__bar" style={{ gap: 6 }}>
                <Chip active={affordOnly} brand={affordOnly} onClick={() => setAffordOnly(v => !v)} title="Only players you can still afford">
                  {affordOnly ? '✓ ' : ''}In budget
                </Chip>
                {positions.map(p => <Chip key={p} active={posF === p} onClick={() => setPosF(p)}>{p === 'ALL' ? 'All' : p}</Chip>)}
              </div>
              <div className="prow prow--build" style={{ background: 'var(--card-2)' }}>
                <div className="prow__sub" style={{ fontWeight: 800 }}>{pool.length} PLAYERS · last-5 form & season</div>
                <div className="colh">PROJ</div>
                <div className="colh">SALARY</div>
                <div className="colh" style={{ width: 34 }}></div>
              </div>
              <PagedList items={pool} perPage={30} resetKey={q + '|' + posF + '|' + affordOnly + '|' + sort}
                empty={<div className="center muted" style={{ padding: 30, fontWeight: 600 }}>{affordOnly ? 'Nothing left in budget — remove a pricey pick or turn off “In budget.”' : 'No players match.'}</div>}
                renderRow={p => {
                  const afford = canAfford(p);
                  return (
                    <div key={p.id} className={'prow prow--build' + (afford ? '' : ' prow--off')}>
                      <div className="prow__id" onClick={() => onOpenPlayer(p)} style={{ cursor: 'pointer' }}>
                        <PlayerMark p={p} />
                        <div style={{ minWidth: 0 }}>
                          <div className="prow__name">{p.name}</div>
                          <div className="prow__sub"><span className="poscap">{posLabel(p)}</span> · <span style={{ color: p.teamColor, fontWeight: 700 }}>{p.team}</span> {p.opp}</div>
                          {p.statline
                            ? <div className="prow__stat">{p.statline}</div>
                            : <div className="prow__stat prow__stat--none">No games yet · {p.own}% rostered</div>}
                        </div>
                      </div>
                      <div className="cell-num"><div className="big" style={{ color: 'var(--brand)' }}>{p.proj.toFixed(1)}</div><Spark form={p.form} /></div>
                      <div className="cell-num"><div className="big">{S.money(p.salary)}</div><div className="sm">{p.value ? p.value.toFixed(1) + ' pt/$' : ''}</div></div>
                      <button className="add-btn" disabled={!afford} title={afford ? 'Add to lineup' : 'Not enough cap left'} onClick={() => onAdd(p)}>
                        {afford ? '+' : <Icon name="lock" size={15} />}
                      </button>
                    </div>
                  );
                }} />
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

/* ordinal: 1 -> 1st, 2 -> 2nd … */
function ordinal(n) {
  const s = ['th', 'st', 'nd', 'rd'], v = n % 100;
  return n.toLocaleString('en-IN') + (s[(v - 20) % 10] || s[v] || s[0]);
}

/* ---------- LIVE SCORING + LEADERBOARD (real data) ---------- */
function LiveScreen({ roster, contestId, onNav }) {
  const S = window.SLATE;
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (contestId == null) { setData(null); return; }
    setLoading(true);
    fetch('/fantasy/api/contest/' + contestId)
      .then(r => (r.ok ? r.json() : null))
      .then(j => { setData(j); setLoading(false); })
      .catch(() => setLoading(false));
  }, [contestId]);

  // no entry yet (e.g. tapped Live directly) — prompt to play
  if (contestId == null) {
    return (
      <>
        <TopBar title="Live" sub="No live entry yet" back onBack={() => onNav('hub')} />
        <div className="app__scroll"><div className="page">
          <div className="card card--pad center" style={{ padding: '48px 20px' }}>
            <div className="dim" style={{ fontWeight: 600 }}>You don't have a live lineup.</div>
            <Btn variant="brand" className="mt-16" onClick={() => onNav('lobby')}>Find a contest <Icon name="chev" size={18} /></Btn>
          </div>
        </div></div>
      </>
    );
  }
  if (loading || !data) {
    return (
      <>
        <TopBar title="Live" sub="Scoring…" back onBack={() => onNav('hub')} />
        <div className="app__scroll"><div className="page">
          <div className="card card--pad center" style={{ padding: '48px 20px' }}>
            <div className="dim" style={{ fontWeight: 600 }}>Scoring the board…</div>
          </div>
        </div></div>
      </>
    );
  }

  const c = data.contest || {};
  const lineup = data.lineup || [];
  const board = data.board || [];
  const gamesDone = data.games_done, gamesTotal = data.games_total;
  const meRow = board.find(b => b.me) || {};
  const winning = meRow.win || 0;
  const topPct = data.percentile != null ? Math.max(0, 100 - data.percentile) : null;

  return (
    <>
      <TopBar title={c.name || 'Live'} sub={`Live · ${gamesDone} of ${gamesTotal} games final`} back onBack={() => onNav('hub')} right={
        <span className="simclock hide-mobile"><span className="dot" /> Live</span>
      } />
      <div className="app__scroll">
        <div className="page">
          {/* live header */}
          <div className="live-head">
            <div className="live-head__rank">
              <div><div className="eyebrow" style={{ color: 'rgba(255,255,255,.6)' }}>Your rank</div>
              <div className="row" style={{ alignItems: 'baseline', gap: 10 }}>
                <span className="pos">{data.your_rank ? ordinal(data.your_rank) : '—'}</span>
                <span className="of">of {(data.field_total || 0).toLocaleString('en-IN')}</span></div></div>
              <div className="live-head__pts">
                <div className="eyebrow" style={{ color: 'rgba(255,255,255,.6)' }}>Live points</div>
                <div className="n">{data.your_points != null ? data.your_points.toFixed(1) : '0.0'}</div>
              </div>
            </div>
            <div className="row" style={{ justifyContent: 'space-between', marginTop: 14, gap: 8 }}>
              <span style={{ color: winning > 0 ? 'var(--live)' : 'rgba(255,255,255,.7)', fontWeight: 700, fontSize: '.85rem', whiteSpace: 'nowrap' }}>
                {topPct != null ? 'Top ' + topPct.toFixed(0) + '%' : '—'}{winning > 0 ? ' · winning ' + S.money(winning) : ''}
              </span>
              <span style={{ color: 'rgba(255,255,255,.6)', fontWeight: 600, fontSize: '.8rem', whiteSpace: 'nowrap' }}>Par {data.par.toFixed(1)} · win line {data.cash_line.toFixed(1)}</span>
            </div>
            <div className="live-bars">
              {Array.from({ length: gamesTotal }).map((_, i) => <i key={i} className={i < gamesDone ? 'done' : ''} />)}
            </div>
          </div>

          <div className="builder mt-16">
            {/* lineup scoring */}
            <div className="card" style={{ overflow: 'hidden' }}>
              <div className="prow" style={{ background: 'var(--card-2)' }}>
                <div className="prow__sub" style={{ fontWeight: 800 }}>YOUR LINEUP</div>
                <div className="colh">PTS</div>
                <div className="colh" style={{ width: 60 }}>STATUS</div>
              </div>
              {lineup.length === 0 && <div className="center muted" style={{ padding: 24, fontWeight: 600 }}>No lineup on this entry.</div>}
              {lineup.map((p, i) => (
                <div key={i} className="score-row">
                  <div className="prow__id">
                    <PlayerMark p={{ init: p.init, teamColor: p.teamColor }} size={38} />
                    <div style={{ minWidth: 0 }}>
                      <div className="prow__name">{p.name}</div>
                      <div className="prow__sub"><span className="poscap">{p.pos}</span> · <span style={{ color: p.teamColor, fontWeight: 700 }}>{p.team}</span> {p.opp}</div>
                    </div>
                  </div>
                  <div className="score-row__pts" style={{ color: p.done ? 'var(--ink)' : 'var(--ink-3)' }}>{p.pts.toFixed(1)}</div>
                  <div style={{ width: 60, textAlign: 'right' }}>
                    {p.done ? <Tag kind="ink">Final</Tag> : <Tag kind="live"><span className="pulse" /> Live</Tag>}
                  </div>
                </div>
              ))}
            </div>

            {/* leaderboard */}
            <div className="card" style={{ overflow: 'hidden' }}>
              <div className="prow" style={{ background: 'var(--card-2)' }}>
                <div className="prow__sub" style={{ fontWeight: 800 }}>LEADERBOARD</div>
                <div className="colh" style={{ width: 60 }}>PTS</div>
              </div>
              {board.map((r, i) => (
                <div key={i} className={'lb-row' + (r.me ? ' lb-row--me' : '')}>
                  <div className={'lb-rank' + (r.rank <= 3 ? ' top' : '')}>{r.rank.toLocaleString('en-IN')}</div>
                  <div className="lb-user">
                    <span className="avatar" style={{ width: 30, height: 30, fontSize: '.8rem' }}>{(r.user[0] || '?').toUpperCase()}</span>
                    <b style={{ fontSize: '.9rem' }}>{r.user}</b>
                    {r.me && <Tag kind="new">You</Tag>}
                  </div>
                  <div className="lb-win hide-narrow">{r.win ? S.money(r.win) : '—'}</div>
                  <div className="lb-pts">{r.pts.toFixed(1)}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

/* ---------- PLAYER DRAWER ---------- */
function PlayerDrawer({ player, open, onClose, onAdd, inLineup }) {
  const S = window.SLATE;
  const [d, setD] = useState(null);
  const [showRatings, setShowRatings] = useState(false);
  const seedId = player && player.id;
  useEffect(() => {
    if (!open || !seedId) return;
    setD(null); setShowRatings(false);
    fetch('/fantasy/api/player/' + String(seedId).replace(/^p/, ''))
      .then(r => (r.ok ? r.json() : null)).then(setD).catch(() => setD(null));
  }, [open, seedId]);

  if (!player) return null;
  const seed = player;
  const isPitcher = d ? d.isPitcher : seed.isPitcher;
  const hasStats = !!(d && d.stats && d.stats.length);
  const viewRatings = showRatings || (d && !hasStats);
  const ratings = (d && d.r) || seed.r || {};
  const showDfs = seed.salary != null;
  const proj = Number(d ? d.proj : (seed.proj || 0));
  const heroColor = seed.teamColor || (S.TEAMS && S.TEAMS[seed.team] && S.TEAMS[seed.team].color) || 'var(--brand)';
  const ratingDefs = isPitcher
    ? [['command', 'Command'], ['stuff', 'Stuff'], ['decay', 'Decay resist'], ['control', 'Control'], ['late', 'Late-arc']]
    : [['contact', 'Contact'], ['power', 'Power'], ['eye', 'Eye'], ['stay', 'Stay'], ['speed', 'Speed'], ['field', 'Field']];

  return (
    <div className={'scrim' + (open ? ' scrim--open' : '')} onClick={onClose}>
      <div className="drawer" onClick={e => e.stopPropagation()}>
        <div className="drawer__hero" style={{ background: heroColor }}>
          <button className="drawer__close" onClick={onClose}><Icon name="x" size={18} /></button>
          <div className="row" style={{ gap: 6 }}>
            <Tag kind="ink" style={{ background: 'rgba(0,0,0,.2)', color: '#fff' }}>{(d && d.pos) || seed.pos}</Tag>
          </div>
          <div className="drawer__name mt-8">{(d && d.name) || seed.name}</div>
          <div className="drawer__meta">{(d && d.teamName) || seed.teamName || seed.team || ''}{seed.opp ? ' · ' + seed.opp : ''}</div>
        </div>
        <div className="drawer__body">
          <div className="tiles" style={{ gridTemplateColumns: 'repeat(' + (showDfs ? 4 : 1) + ',1fr)' }}>
            <div className="tile" style={{ padding: '11px 12px' }}><div className="lbl">Proj</div><div className="val" style={{ fontSize: '1.3rem', color: 'var(--brand)' }}>{proj.toFixed(1)}</div></div>
            {showDfs && <>
              <div className="tile" style={{ padding: '11px 12px' }}><div className="lbl">Salary</div><div className="val" style={{ fontSize: '1.3rem' }}>{S.money(seed.salary)}</div></div>
              <div className="tile" style={{ padding: '11px 12px' }}><div className="lbl">Value</div><div className="val" style={{ fontSize: '1.3rem', color: 'var(--live)' }}>{(seed.value || 0).toFixed(1)}</div></div>
              <div className="tile" style={{ padding: '11px 12px' }}><div className="lbl">Own</div><div className="val" style={{ fontSize: '1.3rem' }}>{seed.own}%</div></div>
            </>}
          </div>

          {!d ? (
            <div className="center muted" style={{ padding: '28px', fontWeight: 600 }}>Loading…</div>
          ) : (
            <>
              <div className="eyebrow mt-24 mb-12" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span>{viewRatings ? 'Ratings · 20–80 scale' : 'Season'}</span>
                {hasStats && <button className="btn btn--ghost btn--sm" onClick={() => setShowRatings(s => !s)}>{viewRatings ? 'Show stats' : 'Show ratings'}</button>}
              </div>
              {viewRatings ? (
                <div className="rating-grid">
                  {ratingDefs.map(([k, lbl]) => (
                    <div key={k} className="rating">
                      <div className="rating__lbl">{lbl}</div>
                      <div className="rating__row"><span className="rating__val" style={{ color: ratingColor(ratings[k]) }}>{ratings[k]}</span></div>
                      <div className="rating__bar"><i style={{ width: ((ratings[k] - 20) / 60) * 100 + '%', background: ratingColor(ratings[k]) }} /></div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="tiles" style={{ gridTemplateColumns: 'repeat(4,1fr)', gap: 8 }}>
                  {d.stats.map(s => (
                    <div key={s.k} className="tile" style={{ padding: '9px 8px' }}><div className="lbl">{s.k}</div><div className="val" style={{ fontSize: '1.05rem' }}>{s.v}</div></div>
                  ))}
                </div>
              )}
              {!hasStats && <div className="muted mt-12" style={{ fontSize: '.82rem', lineHeight: 1.4 }}>No games played yet — talent ratings shown for context. Sim some days to build a real stat line.</div>}

              <div className="eyebrow mt-24 mb-12">Last 5 games</div>
              {d.log && d.log.length ? (
                <div className="card" style={{ overflow: 'hidden' }}>
                  <table className="glog">
                    <thead><tr><th>Game</th><th style={{ textAlign: 'left' }}>Line</th><th>FP</th></tr></thead>
                    <tbody>
                      {d.log.map((g, i) => (
                        <tr key={i}>
                          <td>{g.date} <span className="dim">vs {g.opp}</span></td>
                          <td style={{ textAlign: 'left', fontSize: '.76rem' }} className="muted">{g.line}</td>
                          <td className="fp">{g.fp.toFixed(1)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <div className="card card--pad center muted" style={{ fontWeight: 600 }}>No games played yet.</div>}

              <a className="btn btn--ghost btn--block mt-16" href={d.almanac} target="_blank" rel="noopener" style={{ textDecoration: 'none' }}>View full profile in the almanac →</a>

              {onAdd && showDfs && (
                <Btn variant={inLineup ? 'ghost' : 'brand'} block className="mt-12" onClick={() => { onAdd(seed); onClose(); }}>
                  {inLineup ? 'In your lineup ✓' : <>Add to lineup · {S.money(seed.salary)}</>}
                </Btn>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { BuilderScreen, LiveScreen, PlayerDrawer });
