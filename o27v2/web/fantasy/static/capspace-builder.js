/* ============================================================
   SLATE — screens part 2: Lineup Builder, Live scoring, Player drawer
   ============================================================ */
const {
  useState,
  useEffect
} = React;

/* color for a 20-80 rating */
function ratingColor(v) {
  if (v >= 68) return 'var(--live)';
  if (v >= 58) return 'var(--brand)';
  if (v >= 48) return 'var(--amber)';
  return 'var(--down)';
}

/* ---------- LINEUP BUILDER ---------- */
function BuilderScreen({
  contest,
  roster,
  onAdd,
  onRemove,
  onOpenPlayer,
  onEnter,
  onNav
}) {
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
  function canAfford(p) {
    return p.salary <= rem;
  }
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(TopBar, {
    title: contest ? contest.name : 'Build lineup',
    sub: `Daily Slate · ${S.money(S.CAP)} cap`,
    back: true,
    onBack: () => onNav('lobby')
  }), /*#__PURE__*/React.createElement("div", {
    className: "app__scroll"
  }, /*#__PURE__*/React.createElement("div", {
    className: "page"
  }, /*#__PURE__*/React.createElement("div", {
    className: "cap mb-16"
  }, /*#__PURE__*/React.createElement("div", {
    className: "cap__cell"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Salary left"), /*#__PURE__*/React.createElement("div", {
    className: 'val ' + (over ? 'warn' : 'ok')
  }, over ? '-' : '', S.money(Math.abs(rem)))), /*#__PURE__*/React.createElement("div", {
    className: "cap__cell"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Avg / open slot"), /*#__PURE__*/React.createElement("div", {
    className: "val"
  }, openSlots ? S.money(Math.max(0, perSlot)) : '—')), /*#__PURE__*/React.createElement("div", {
    className: "cap__cell"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Proj points"), /*#__PURE__*/React.createElement("div", {
    className: "val",
    style: {
      color: 'var(--amber)'
    }
  }, projPts.toFixed(1))), /*#__PURE__*/React.createElement("div", {
    className: "cap__bar"
  }, /*#__PURE__*/React.createElement("i", {
    className: over ? 'over' : '',
    style: {
      width: Math.min(100, used / S.CAP * 100) + '%'
    }
  }))), /*#__PURE__*/React.createElement("div", {
    className: "builder"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pool"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pool__bar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "search"
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "search",
    size: 17
  }), /*#__PURE__*/React.createElement("input", {
    placeholder: "Search players\u2026",
    value: q,
    onChange: e => setQ(e.target.value)
  })), /*#__PURE__*/React.createElement("select", {
    className: "chip",
    value: sort,
    onChange: e => setSort(e.target.value),
    style: {
      paddingRight: 24
    }
  }, /*#__PURE__*/React.createElement("option", {
    value: "proj"
  }, "Sort: Proj"), /*#__PURE__*/React.createElement("option", {
    value: "salary"
  }, "Sort: Salary"), /*#__PURE__*/React.createElement("option", {
    value: "value"
  }, "Sort: Value"))), /*#__PURE__*/React.createElement("div", {
    className: "pool__bar",
    style: {
      gap: 6
    }
  }, positions.map(p => /*#__PURE__*/React.createElement(Chip, {
    key: p,
    active: posF === p,
    onClick: () => setPosF(p)
  }, p === 'ALL' ? 'All' : p))), /*#__PURE__*/React.createElement("div", {
    className: "prow",
    style: {
      background: 'var(--card-2)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__sub",
    style: {
      fontWeight: 800
    }
  }, pool.length, " PLAYERS"), /*#__PURE__*/React.createElement("div", {
    className: "colh"
  }, "PROJ"), /*#__PURE__*/React.createElement("div", {
    className: "colh hide-narrow"
  }, "SALARY"), /*#__PURE__*/React.createElement("div", {
    className: "colh hide-narrow"
  }, "VALUE"), /*#__PURE__*/React.createElement("div", {
    className: "colh",
    style: {
      width: 34
    }
  })), pool.map(p => {
    const afford = canAfford(p);
    return /*#__PURE__*/React.createElement("div", {
      key: p.id,
      className: "prow"
    }, /*#__PURE__*/React.createElement("div", {
      className: "prow__id",
      onClick: () => onOpenPlayer(p),
      style: {
        cursor: 'pointer'
      }
    }, /*#__PURE__*/React.createElement(PlayerMark, {
      p: p
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement("div", {
      className: "prow__name"
    }, p.name), /*#__PURE__*/React.createElement("div", {
      className: "prow__sub"
    }, /*#__PURE__*/React.createElement("span", {
      className: "poscap"
    }, p.pos), " \xB7 ", /*#__PURE__*/React.createElement("span", {
      style: {
        color: p.teamColor,
        fontWeight: 700
      }
    }, p.team), " ", p.opp, " \xB7 ", /*#__PURE__*/React.createElement("span", null, p.own, "% own")))), /*#__PURE__*/React.createElement("div", {
      className: "cell-num"
    }, /*#__PURE__*/React.createElement("div", {
      className: "big",
      style: {
        color: 'var(--brand)'
      }
    }, p.proj.toFixed(1)), /*#__PURE__*/React.createElement("div", {
      className: "sm"
    }, "proj")), /*#__PURE__*/React.createElement("div", {
      className: "cell-num hide-narrow"
    }, /*#__PURE__*/React.createElement("div", {
      className: "big"
    }, S.money(p.salary)), /*#__PURE__*/React.createElement(Spark, {
      form: p.form
    })), /*#__PURE__*/React.createElement("div", {
      className: "cell-num hide-narrow"
    }, /*#__PURE__*/React.createElement("div", {
      className: "big val-pos"
    }, p.value.toFixed(1)), /*#__PURE__*/React.createElement("div", {
      className: "sm"
    }, "pt/L")), /*#__PURE__*/React.createElement("button", {
      className: "add-btn",
      disabled: !afford,
      title: afford ? 'Add to lineup' : 'Over cap',
      onClick: () => onAdd(p)
    }, afford ? '+' : /*#__PURE__*/React.createElement(Icon, {
      name: "lock",
      size: 15
    })));
  }), pool.length === 0 && /*#__PURE__*/React.createElement("div", {
    className: "center muted",
    style: {
      padding: 30,
      fontWeight: 600
    }
  }, "No players match.")), /*#__PURE__*/React.createElement("div", {
    className: "roster"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card card--pad"
  }, /*#__PURE__*/React.createElement("div", {
    className: "roster__head"
  }, /*#__PURE__*/React.createElement("h3", null, "Your lineup"), /*#__PURE__*/React.createElement("span", {
    className: "num muted",
    style: {
      fontWeight: 700
    }
  }, filled, "/", S.SLOTS.length)), S.SLOTS.map(slot => {
    const p = roster[slot.key];
    return /*#__PURE__*/React.createElement("div", {
      key: slot.key,
      className: 'slot' + (p ? ' slot--filled' : '')
    }, /*#__PURE__*/React.createElement("span", {
      className: "slot__pos"
    }, slot.label), p ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
      className: "slot__body",
      onClick: () => onOpenPlayer(p),
      style: {
        cursor: 'pointer'
      }
    }, /*#__PURE__*/React.createElement("div", {
      className: "slot__name"
    }, p.name), /*#__PURE__*/React.createElement("div", {
      className: "prow__sub"
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        color: p.teamColor,
        fontWeight: 700
      }
    }, p.team), " ", p.opp, " \xB7 ", /*#__PURE__*/React.createElement("span", {
      className: "num",
      style: {
        color: 'var(--brand)'
      }
    }, p.proj.toFixed(1), " proj"))), /*#__PURE__*/React.createElement("span", {
      className: "slot__sal num"
    }, S.money(p.salary)), /*#__PURE__*/React.createElement("button", {
      className: "slot__x",
      onClick: () => onRemove(slot.key)
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "x",
      size: 16
    }))) : /*#__PURE__*/React.createElement("div", {
      className: "slot__body"
    }, /*#__PURE__*/React.createElement("span", {
      className: "slot__empty"
    }, slot.flex ? 'Stay flex — any hitter' : 'Empty')));
  }), /*#__PURE__*/React.createElement(Btn, {
    variant: filled === S.SLOTS.length && !over ? 'brand' : 'ghost',
    block: true,
    className: "mt-12",
    disabled: filled !== S.SLOTS.length || over,
    onClick: onEnter
  }, over ? 'Over the cap' : filled === S.SLOTS.length ? /*#__PURE__*/React.createElement(React.Fragment, null, "Enter contest \xB7 ", S.money(contest ? contest.fee : 0), " ", /*#__PURE__*/React.createElement(Icon, {
    name: "chev",
    size: 18
  })) : `Fill ${S.SLOTS.length - filled} more`), /*#__PURE__*/React.createElement("button", {
    className: "btn btn--ghost btn--sm btn--block mt-8",
    onClick: () => S.SLOTS.forEach(s => onRemove(s.key))
  }, "Clear lineup")))))));
}

/* ordinal: 1 -> 1st, 2 -> 2nd … */
function ordinal(n) {
  const s = ['th', 'st', 'nd', 'rd'],
    v = n % 100;
  return n.toLocaleString('en-IN') + (s[(v - 20) % 10] || s[v] || s[0]);
}

/* ---------- LIVE SCORING + LEADERBOARD (real data) ---------- */
function LiveScreen({
  roster,
  contestId,
  onNav
}) {
  const S = window.SLATE;
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (contestId == null) {
      setData(null);
      return;
    }
    setLoading(true);
    fetch('/fantasy/api/contest/' + contestId).then(r => r.ok ? r.json() : null).then(j => {
      setData(j);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [contestId]);

  // no entry yet (e.g. tapped Live directly) — prompt to play
  if (contestId == null) {
    return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(TopBar, {
      title: "Live",
      sub: "No live entry yet",
      back: true,
      onBack: () => onNav('hub')
    }), /*#__PURE__*/React.createElement("div", {
      className: "app__scroll"
    }, /*#__PURE__*/React.createElement("div", {
      className: "page"
    }, /*#__PURE__*/React.createElement("div", {
      className: "card card--pad center",
      style: {
        padding: '48px 20px'
      }
    }, /*#__PURE__*/React.createElement("div", {
      className: "dim",
      style: {
        fontWeight: 600
      }
    }, "You don't have a live lineup."), /*#__PURE__*/React.createElement(Btn, {
      variant: "brand",
      className: "mt-16",
      onClick: () => onNav('lobby')
    }, "Find a contest ", /*#__PURE__*/React.createElement(Icon, {
      name: "chev",
      size: 18
    }))))));
  }
  if (loading || !data) {
    return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(TopBar, {
      title: "Live",
      sub: "Scoring\u2026",
      back: true,
      onBack: () => onNav('hub')
    }), /*#__PURE__*/React.createElement("div", {
      className: "app__scroll"
    }, /*#__PURE__*/React.createElement("div", {
      className: "page"
    }, /*#__PURE__*/React.createElement("div", {
      className: "card card--pad center",
      style: {
        padding: '48px 20px'
      }
    }, /*#__PURE__*/React.createElement("div", {
      className: "dim",
      style: {
        fontWeight: 600
      }
    }, "Scoring the board\u2026")))));
  }
  const c = data.contest || {};
  const lineup = data.lineup || [];
  const board = data.board || [];
  const gamesDone = data.games_done,
    gamesTotal = data.games_total;
  const meRow = board.find(b => b.me) || {};
  const winning = meRow.win || 0;
  const topPct = data.percentile != null ? Math.max(0, 100 - data.percentile) : null;
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(TopBar, {
    title: c.name || 'Live',
    sub: `Live · ${gamesDone} of ${gamesTotal} games final`,
    back: true,
    onBack: () => onNav('hub'),
    right: /*#__PURE__*/React.createElement("span", {
      className: "simclock hide-mobile"
    }, /*#__PURE__*/React.createElement("span", {
      className: "dot"
    }), " Live")
  }), /*#__PURE__*/React.createElement("div", {
    className: "app__scroll"
  }, /*#__PURE__*/React.createElement("div", {
    className: "page"
  }, /*#__PURE__*/React.createElement("div", {
    className: "live-head"
  }, /*#__PURE__*/React.createElement("div", {
    className: "live-head__rank"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow",
    style: {
      color: 'rgba(255,255,255,.6)'
    }
  }, "Your rank"), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      alignItems: 'baseline',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "pos"
  }, data.your_rank ? ordinal(data.your_rank) : '—'), /*#__PURE__*/React.createElement("span", {
    className: "of"
  }, "of ", (data.field_total || 0).toLocaleString('en-IN')))), /*#__PURE__*/React.createElement("div", {
    className: "live-head__pts"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow",
    style: {
      color: 'rgba(255,255,255,.6)'
    }
  }, "Live points"), /*#__PURE__*/React.createElement("div", {
    className: "n"
  }, data.your_points != null ? data.your_points.toFixed(1) : '0.0'))), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      justifyContent: 'space-between',
      marginTop: 14,
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: winning > 0 ? 'var(--live)' : 'rgba(255,255,255,.7)',
      fontWeight: 700,
      fontSize: '.85rem',
      whiteSpace: 'nowrap'
    }
  }, topPct != null ? 'Top ' + topPct.toFixed(0) + '%' : '—', winning > 0 ? ' · winning ' + S.money(winning) : ''), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'rgba(255,255,255,.6)',
      fontWeight: 600,
      fontSize: '.8rem',
      whiteSpace: 'nowrap'
    }
  }, "Par ", data.par.toFixed(1), " \xB7 cash ", data.cash_line.toFixed(1))), /*#__PURE__*/React.createElement("div", {
    className: "live-bars"
  }, Array.from({
    length: gamesTotal
  }).map((_, i) => /*#__PURE__*/React.createElement("i", {
    key: i,
    className: i < gamesDone ? 'done' : ''
  })))), /*#__PURE__*/React.createElement("div", {
    className: "builder mt-16"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow",
    style: {
      background: 'var(--card-2)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__sub",
    style: {
      fontWeight: 800
    }
  }, "YOUR LINEUP"), /*#__PURE__*/React.createElement("div", {
    className: "colh"
  }, "PTS"), /*#__PURE__*/React.createElement("div", {
    className: "colh",
    style: {
      width: 60
    }
  }, "STATUS")), lineup.length === 0 && /*#__PURE__*/React.createElement("div", {
    className: "center muted",
    style: {
      padding: 24,
      fontWeight: 600
    }
  }, "No lineup on this entry."), lineup.map((p, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "score-row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__id"
  }, /*#__PURE__*/React.createElement(PlayerMark, {
    p: {
      init: p.init,
      teamColor: p.teamColor
    },
    size: 38
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__name"
  }, p.name), /*#__PURE__*/React.createElement("div", {
    className: "prow__sub"
  }, /*#__PURE__*/React.createElement("span", {
    className: "poscap"
  }, p.pos), " \xB7 ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: p.teamColor,
      fontWeight: 700
    }
  }, p.team), " ", p.opp))), /*#__PURE__*/React.createElement("div", {
    className: "score-row__pts",
    style: {
      color: p.done ? 'var(--ink)' : 'var(--ink-3)'
    }
  }, p.pts.toFixed(1)), /*#__PURE__*/React.createElement("div", {
    style: {
      width: 60,
      textAlign: 'right'
    }
  }, p.done ? /*#__PURE__*/React.createElement(Tag, {
    kind: "ink"
  }, "Final") : /*#__PURE__*/React.createElement(Tag, {
    kind: "live"
  }, /*#__PURE__*/React.createElement("span", {
    className: "pulse"
  }), " Live"))))), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow",
    style: {
      background: 'var(--card-2)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__sub",
    style: {
      fontWeight: 800
    }
  }, "LEADERBOARD"), /*#__PURE__*/React.createElement("div", {
    className: "colh",
    style: {
      width: 60
    }
  }, "PTS")), board.map((r, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: 'lb-row' + (r.me ? ' lb-row--me' : '')
  }, /*#__PURE__*/React.createElement("div", {
    className: 'lb-rank' + (r.rank <= 3 ? ' top' : '')
  }, r.rank.toLocaleString('en-IN')), /*#__PURE__*/React.createElement("div", {
    className: "lb-user"
  }, /*#__PURE__*/React.createElement("span", {
    className: "avatar",
    style: {
      width: 30,
      height: 30,
      fontSize: '.8rem'
    }
  }, (r.user[0] || '?').toUpperCase()), /*#__PURE__*/React.createElement("b", {
    style: {
      fontSize: '.9rem'
    }
  }, r.user), r.me && /*#__PURE__*/React.createElement(Tag, {
    kind: "new"
  }, "You")), /*#__PURE__*/React.createElement("div", {
    className: "lb-win hide-narrow"
  }, r.win ? S.money(r.win) : '—'), /*#__PURE__*/React.createElement("div", {
    className: "lb-pts"
  }, r.pts.toFixed(1)))))))));
}

/* ---------- PLAYER DRAWER ---------- */
function PlayerDrawer({
  player,
  open,
  onClose,
  onAdd,
  inLineup
}) {
  const S = window.SLATE;
  const [d, setD] = useState(null);
  const [showRatings, setShowRatings] = useState(false);
  const seedId = player && player.id;
  useEffect(() => {
    if (!open || !seedId) return;
    setD(null);
    setShowRatings(false);
    fetch('/fantasy/api/player/' + String(seedId).replace(/^p/, '')).then(r => r.ok ? r.json() : null).then(setD).catch(() => setD(null));
  }, [open, seedId]);
  if (!player) return null;
  const seed = player;
  const isPitcher = d ? d.isPitcher : seed.isPitcher;
  const hasStats = !!(d && d.stats && d.stats.length);
  const viewRatings = showRatings || d && !hasStats;
  const ratings = d && d.r || seed.r || {};
  const showDfs = seed.salary != null;
  const proj = Number(d ? d.proj : seed.proj || 0);
  const heroColor = seed.teamColor || S.TEAMS && S.TEAMS[seed.team] && S.TEAMS[seed.team].color || 'var(--brand)';
  const ratingDefs = isPitcher ? [['command', 'Command'], ['stuff', 'Stuff'], ['decay', 'Decay resist'], ['control', 'Control'], ['late', 'Late-arc']] : [['contact', 'Contact'], ['power', 'Power'], ['eye', 'Eye'], ['stay', 'Stay'], ['speed', 'Speed'], ['field', 'Field']];
  return /*#__PURE__*/React.createElement("div", {
    className: 'scrim' + (open ? ' scrim--open' : ''),
    onClick: onClose
  }, /*#__PURE__*/React.createElement("div", {
    className: "drawer",
    onClick: e => e.stopPropagation()
  }, /*#__PURE__*/React.createElement("div", {
    className: "drawer__hero",
    style: {
      background: heroColor
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "drawer__close",
    onClick: onClose
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "x",
    size: 18
  })), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      gap: 6
    }
  }, /*#__PURE__*/React.createElement(Tag, {
    kind: "ink",
    style: {
      background: 'rgba(0,0,0,.2)',
      color: '#fff'
    }
  }, d && d.pos || seed.pos)), /*#__PURE__*/React.createElement("div", {
    className: "drawer__name mt-8"
  }, d && d.name || seed.name), /*#__PURE__*/React.createElement("div", {
    className: "drawer__meta"
  }, d && d.teamName || seed.teamName || seed.team || '', seed.opp ? ' · ' + seed.opp : '')), /*#__PURE__*/React.createElement("div", {
    className: "drawer__body"
  }, /*#__PURE__*/React.createElement("div", {
    className: "tiles",
    style: {
      gridTemplateColumns: 'repeat(' + (showDfs ? 4 : 1) + ',1fr)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "tile",
    style: {
      padding: '11px 12px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Proj"), /*#__PURE__*/React.createElement("div", {
    className: "val",
    style: {
      fontSize: '1.3rem',
      color: 'var(--brand)'
    }
  }, proj.toFixed(1))), showDfs && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "tile",
    style: {
      padding: '11px 12px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Salary"), /*#__PURE__*/React.createElement("div", {
    className: "val",
    style: {
      fontSize: '1.3rem'
    }
  }, S.money(seed.salary))), /*#__PURE__*/React.createElement("div", {
    className: "tile",
    style: {
      padding: '11px 12px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Value"), /*#__PURE__*/React.createElement("div", {
    className: "val",
    style: {
      fontSize: '1.3rem',
      color: 'var(--live)'
    }
  }, (seed.value || 0).toFixed(1))), /*#__PURE__*/React.createElement("div", {
    className: "tile",
    style: {
      padding: '11px 12px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Own"), /*#__PURE__*/React.createElement("div", {
    className: "val",
    style: {
      fontSize: '1.3rem'
    }
  }, seed.own, "%")))), !d ? /*#__PURE__*/React.createElement("div", {
    className: "center muted",
    style: {
      padding: '28px',
      fontWeight: 600
    }
  }, "Loading\u2026") : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow mt-24 mb-12",
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center'
    }
  }, /*#__PURE__*/React.createElement("span", null, viewRatings ? 'Ratings · 20–80 scale' : 'Season'), hasStats && /*#__PURE__*/React.createElement("button", {
    className: "btn btn--ghost btn--sm",
    onClick: () => setShowRatings(s => !s)
  }, viewRatings ? 'Show stats' : 'Show ratings')), viewRatings ? /*#__PURE__*/React.createElement("div", {
    className: "rating-grid"
  }, ratingDefs.map(([k, lbl]) => /*#__PURE__*/React.createElement("div", {
    key: k,
    className: "rating"
  }, /*#__PURE__*/React.createElement("div", {
    className: "rating__lbl"
  }, lbl), /*#__PURE__*/React.createElement("div", {
    className: "rating__row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "rating__val",
    style: {
      color: ratingColor(ratings[k])
    }
  }, ratings[k])), /*#__PURE__*/React.createElement("div", {
    className: "rating__bar"
  }, /*#__PURE__*/React.createElement("i", {
    style: {
      width: (ratings[k] - 20) / 60 * 100 + '%',
      background: ratingColor(ratings[k])
    }
  }))))) : /*#__PURE__*/React.createElement("div", {
    className: "tiles",
    style: {
      gridTemplateColumns: 'repeat(4,1fr)',
      gap: 8
    }
  }, d.stats.map(s => /*#__PURE__*/React.createElement("div", {
    key: s.k,
    className: "tile",
    style: {
      padding: '9px 8px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, s.k), /*#__PURE__*/React.createElement("div", {
    className: "val",
    style: {
      fontSize: '1.05rem'
    }
  }, s.v)))), !hasStats && /*#__PURE__*/React.createElement("div", {
    className: "muted mt-12",
    style: {
      fontSize: '.82rem',
      lineHeight: 1.4
    }
  }, "No games played yet \u2014 talent ratings shown for context. Sim some days to build a real stat line."), /*#__PURE__*/React.createElement("div", {
    className: "eyebrow mt-24 mb-12"
  }, "Last 5 games"), d.log && d.log.length ? /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, /*#__PURE__*/React.createElement("table", {
    className: "glog"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Game"), /*#__PURE__*/React.createElement("th", {
    style: {
      textAlign: 'left'
    }
  }, "Line"), /*#__PURE__*/React.createElement("th", null, "FP"))), /*#__PURE__*/React.createElement("tbody", null, d.log.map((g, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, g.date, " ", /*#__PURE__*/React.createElement("span", {
    className: "dim"
  }, "vs ", g.opp)), /*#__PURE__*/React.createElement("td", {
    style: {
      textAlign: 'left',
      fontSize: '.76rem'
    },
    className: "muted"
  }, g.line), /*#__PURE__*/React.createElement("td", {
    className: "fp"
  }, g.fp.toFixed(1))))))) : /*#__PURE__*/React.createElement("div", {
    className: "card card--pad center muted",
    style: {
      fontWeight: 600
    }
  }, "No games played yet."), /*#__PURE__*/React.createElement("a", {
    className: "btn btn--ghost btn--block mt-16",
    href: d.almanac,
    target: "_blank",
    rel: "noopener",
    style: {
      textDecoration: 'none'
    }
  }, "View full profile in the almanac \u2192"), onAdd && showDfs && /*#__PURE__*/React.createElement(Btn, {
    variant: inLineup ? 'ghost' : 'brand',
    block: true,
    className: "mt-12",
    onClick: () => {
      onAdd(seed);
      onClose();
    }
  }, inLineup ? 'In your lineup ✓' : /*#__PURE__*/React.createElement(React.Fragment, null, "Add to lineup \xB7 ", S.money(seed.salary)))))));
}
Object.assign(window, {
  BuilderScreen,
  LiveScreen,
  PlayerDrawer
});
