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

  const chosenIds = Object.values(roster).filter(Boolean).map(p => p.id);
  const used = Object.values(roster).filter(Boolean).reduce((s, p) => s + p.salary, 0);
  const rem = S.CAP - used;
  const filled = chosenIds.length;
  const openSlots = S.SLOTS.length - filled;
  const projPts = Object.values(roster).filter(Boolean).reduce((s, p) => s + p.proj, 0);
  const perSlot = openSlots > 0 ? rem / openSlots : 0;
  const over = rem < 0;

  const positions = ['ALL', 'PILOT', 'C', '1B', '2B', '3B', 'SS', 'OF'];
  let pool = S.PLAYERS.filter(p => !chosenIds.includes(p.id));
  if (posF !== 'ALL') pool = pool.filter(p => p.pos === posF);
  if (q.trim()) pool = pool.filter(p => p.name.toLowerCase().includes(q.toLowerCase()));
  pool = [...pool].sort((a, b) => sort === 'salary' ? b.salary - a.salary : sort === 'value' ? b.value - a.value : b.proj - a.proj);

  function canAfford(p) { return p.salary <= rem; }

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
                {positions.map(p => <Chip key={p} active={posF === p} onClick={() => setPosF(p)}>{p === 'ALL' ? 'All' : p}</Chip>)}
              </div>
              <div className="prow" style={{ background: 'var(--card-2)' }}>
                <div className="prow__sub" style={{ fontWeight: 800 }}>{pool.length} PLAYERS</div>
                <div className="colh">PROJ</div>
                <div className="colh hide-narrow">SALARY</div>
                <div className="colh hide-narrow">VALUE</div>
                <div className="colh" style={{ width: 34 }}></div>
              </div>
              {pool.map(p => {
                const afford = canAfford(p);
                return (
                  <div key={p.id} className="prow">
                    <div className="prow__id" onClick={() => onOpenPlayer(p)} style={{ cursor: 'pointer' }}>
                      <PlayerMark p={p} />
                      <div style={{ minWidth: 0 }}>
                        <div className="prow__name">{p.name}</div>
                        <div className="prow__sub"><span className="poscap">{p.pos}</span> · <span style={{ color: p.teamColor, fontWeight: 700 }}>{p.team}</span> {p.opp} · <span>{p.own}% own</span></div>
                      </div>
                    </div>
                    <div className="cell-num"><div className="big" style={{ color: 'var(--brand)' }}>{p.proj.toFixed(1)}</div><div className="sm">proj</div></div>
                    <div className="cell-num hide-narrow"><div className="big">{S.money(p.salary)}</div><Spark form={p.form} /></div>
                    <div className="cell-num hide-narrow"><div className="big val-pos">{p.value.toFixed(1)}</div><div className="sm">pt/L</div></div>
                    <button className="add-btn" disabled={!afford} title={afford ? 'Add to lineup' : 'Over cap'} onClick={() => onAdd(p)}>
                      {afford ? '+' : <Icon name="lock" size={15} />}
                    </button>
                  </div>
                );
              })}
              {pool.length === 0 && <div className="center muted" style={{ padding: 30, fontWeight: 600 }}>No players match.</div>}
            </div>

            {/* roster */}
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
                        <div className="slot__body"><span className="slot__empty">{slot.flex ? 'Stay flex — any hitter' : 'Empty'}</span></div>
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
              <span style={{ color: 'rgba(255,255,255,.6)', fontWeight: 600, fontSize: '.8rem', whiteSpace: 'nowrap' }}>Par {data.par.toFixed(1)} · cash {data.cash_line.toFixed(1)}</span>
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
  if (!player) return null;
  const p = player;
  const ratingDefs = p.isPitcher
    ? [['command', 'Command'], ['stuff', 'Stuff'], ['decay', 'Decay resist'], ['control', 'Control'], ['late', 'Late-arc']]
    : [['contact', 'Contact'], ['power', 'Power'], ['eye', 'Eye'], ['stay', 'Stay'], ['speed', 'Speed'], ['field', 'Field']];
  return (
    <div className={'scrim' + (open ? ' scrim--open' : '')} onClick={onClose}>
      <div className="drawer" onClick={e => e.stopPropagation()}>
        <div className="drawer__hero" style={{ background: p.teamColor }}>
          <button className="drawer__close" onClick={onClose}><Icon name="x" size={18} /></button>
          <div className="row" style={{ gap: 6 }}>
            <Tag kind="ink" style={{ background: 'rgba(0,0,0,.2)', color: '#fff' }}>{p.pos}</Tag>
          </div>
          <div className="drawer__name mt-8">{p.name}</div>
          <div className="drawer__meta">{p.teamName} · {p.opp}</div>
        </div>
        <div className="drawer__body">
          <div className="tiles" style={{ gridTemplateColumns: 'repeat(4,1fr)' }}>
            <div className="tile" style={{ padding: '11px 12px' }}><div className="lbl">Proj</div><div className="val" style={{ fontSize: '1.3rem', color: 'var(--brand)' }}>{p.proj.toFixed(1)}</div></div>
            <div className="tile" style={{ padding: '11px 12px' }}><div className="lbl">Salary</div><div className="val" style={{ fontSize: '1.3rem' }}>{S.money(p.salary)}</div></div>
            <div className="tile" style={{ padding: '11px 12px' }}><div className="lbl">Value</div><div className="val" style={{ fontSize: '1.3rem', color: 'var(--live)' }}>{p.value.toFixed(1)}</div></div>
            <div className="tile" style={{ padding: '11px 12px' }}><div className="lbl">Own</div><div className="val" style={{ fontSize: '1.3rem' }}>{p.own}%</div></div>
          </div>

          <div className="eyebrow mt-24 mb-12">Ratings · 20–80 scale</div>
          <div className="rating-grid">
            {ratingDefs.map(([k, lbl]) => (
              <div key={k} className="rating">
                <div className="rating__lbl">{lbl}</div>
                <div className="rating__row"><span className="rating__val" style={{ color: ratingColor(p.r[k]) }}>{p.r[k]}</span></div>
                <div className="rating__bar"><i style={{ width: ((p.r[k] - 20) / 60) * 100 + '%', background: ratingColor(p.r[k]) }} /></div>
              </div>
            ))}
          </div>

          <div className="eyebrow mt-24 mb-12">Last 5 games</div>
          <div className="card" style={{ overflow: 'hidden' }}>
            <table className="glog">
              <thead><tr><th>Game</th><th style={{ textAlign: 'left' }}>Line</th><th>FP</th></tr></thead>
              <tbody>
                {p.log.map((g, i) => (
                  <tr key={i}>
                    <td>{g.date} <span className="dim">vs {g.opp}</span></td>
                    <td style={{ textAlign: 'left', fontSize: '.76rem' }} className="muted">{g.line}</td>
                    <td className="fp">{g.fp.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {onAdd && (
            <Btn variant={inLineup ? 'ghost' : 'brand'} block className="mt-16" onClick={() => { onAdd(p); onClose(); }}>
              {inLineup ? 'In your lineup ✓' : <>Add to lineup · {S.money(p.salary)}</>}
            </Btn>
          )}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { BuilderScreen, LiveScreen, PlayerDrawer });
