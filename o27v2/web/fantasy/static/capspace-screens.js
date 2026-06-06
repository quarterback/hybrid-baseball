/* ============================================================
   SLATE — screens part 1: Hub, Lobby, Entries, Player drawer
   ============================================================ */
const {
  useState,
  useEffect
} = React;

/* ---------- HUB / GAME LIBRARY ---------- */
function HubScreen({
  onNav,
  onOpenFormat,
  onNewRun
}) {
  const S = window.SLATE;
  const games = (S.SLATE_GAMES || []).length;
  const contests = S.CONTESTS || [];
  const prizePool = contests.reduce((s, c) => s + (c.prize || 0), 0);
  const topPrize = contests.reduce((m, c) => Math.max(m, c.top || 0), 0);
  const [w, setW] = useState(null);
  const [busy, setBusy] = useState(false);
  function loadW() {
    fetch('/fantasy/api/wallet').then(r => r.ok ? r.json() : null).then(d => {
      setW(d);
      if (d && d.balance != null) S.WALLET = d.balance;
    }).catch(() => {});
  }
  useEffect(loadW, []);
  const bal = w ? w.balance : S.WALLET;
  const rec = w && w.records || {};
  const t = rec.tier;
  const pct = t && !t.isMax && t.nextGate > t.floor ? Math.max(3, Math.min(100, Math.round((rec.lifetime - t.floor) / (t.nextGate - t.floor) * 100))) : 100;
  function restart() {
    if (busy) return;
    setBusy(true);
    fetch('/fantasy/api/wallet/restart', {
      method: 'POST'
    }).then(r => r.json()).then(j => {
      setBusy(false);
      if (!j.ok) {
        window.alert(j.error || '');
        return;
      }
      if (j.balance != null) S.WALLET = j.balance;
      loadW();
    }).catch(() => setBusy(false));
  }
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(TopBar, {
    title: "Good evening, Player!",
    sub: `${games} games on tonight's slate`,
    right: /*#__PURE__*/React.createElement("button", {
      className: "btn btn--ghost btn--sm hide-mobile",
      style: {
        padding: '9px 12px'
      }
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "bell",
      size: 18
    }))
  }), /*#__PURE__*/React.createElement("div", {
    className: "app__scroll"
  }, /*#__PURE__*/React.createElement("div", {
    className: "page"
  }, /*#__PURE__*/React.createElement("div", {
    className: "hero"
  }, /*#__PURE__*/React.createElement("div", {
    className: "hero__in"
  }, /*#__PURE__*/React.createElement(Tag, {
    kind: "live"
  }, /*#__PURE__*/React.createElement("span", {
    className: "pulse"
  }), " Tonight's slate is live"), /*#__PURE__*/React.createElement("h1", {
    className: "mt-12"
  }, "Tonight's Daily Slate is live."), /*#__PURE__*/React.createElement("p", null, "Build a lineup under the ", S.money(S.CAP), " cap across ", games, " games.", prizePool > 0 ? ` ${S.money(prizePool)} in prizes on the board.` : ''), /*#__PURE__*/React.createElement("div", {
    className: "row wrap"
  }, /*#__PURE__*/React.createElement(Btn, {
    variant: "brand",
    size: "lg",
    onClick: () => onNav('lobby')
  }, "Play the slate ", /*#__PURE__*/React.createElement(Icon, {
    name: "chev",
    size: 18
  })), /*#__PURE__*/React.createElement(Btn, {
    variant: "ghost",
    size: "lg",
    onClick: () => onNav('lobby')
  }, "Browse contests")))), /*#__PURE__*/React.createElement("div", {
    className: "tiles mt-24"
  }, /*#__PURE__*/React.createElement("div", {
    className: "tile"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Tonight"), /*#__PURE__*/React.createElement("div", {
    className: "val"
  }, games), /*#__PURE__*/React.createElement("div", {
    className: "sub"
  }, "games on the slate")), /*#__PURE__*/React.createElement("div", {
    className: "tile"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Contests"), /*#__PURE__*/React.createElement("div", {
    className: "val"
  }, contests.length), /*#__PURE__*/React.createElement("div", {
    className: "sub"
  }, "open to enter")), /*#__PURE__*/React.createElement("div", {
    className: "tile"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Top prize"), /*#__PURE__*/React.createElement("div", {
    className: "val"
  }, topPrize > 0 ? S.money(topPrize) : '—'), /*#__PURE__*/React.createElement("div", {
    className: "sub"
  }, "across all contests")), /*#__PURE__*/React.createElement("div", {
    className: "tile"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Bankroll"), /*#__PURE__*/React.createElement("div", {
    className: "val"
  }, S.money(bal)), /*#__PURE__*/React.createElement("div", {
    className: "sub"
  }, "one wallet, every game"))), w && t && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "section-head mt-24"
  }, /*#__PURE__*/React.createElement("h2", null, "Your career"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn--ghost btn--sm",
    onClick: onNewRun
  }, "New run")), bal < 5000 && /*#__PURE__*/React.createElement("div", {
    className: "card card--pad mb-12",
    style: {
      borderLeft: '4px solid var(--c-amber)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontWeight: 800
    }
  }, "Back from the felt?"), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: '.85rem',
      margin: '4px 0 10px',
      lineHeight: 1.4
    }
  }, "Tapped out \u2014 but your ", /*#__PURE__*/React.createElement("b", null, t.name), " status is permanent. As a ", t.name, ", you restart with ", /*#__PURE__*/React.createElement("b", null, S.money(t.startBankroll)), ". Climb tiers to come back richer."), /*#__PURE__*/React.createElement("button", {
    className: "btn btn--brand btn--sm",
    disabled: busy,
    onClick: restart
  }, "Restart with ", S.money(t.startBankroll))), /*#__PURE__*/React.createElement("div", {
    className: "card card--pad mb-12"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'baseline',
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl",
    style: {
      textTransform: 'uppercase',
      fontSize: '.7rem',
      letterSpacing: '.06em',
      color: 'var(--ink-3)',
      fontWeight: 700
    }
  }, "Status"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: '1.5rem',
      fontWeight: 900
    }
  }, t.name)), /*#__PURE__*/React.createElement("div", {
    style: {
      textAlign: 'right'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl",
    style: {
      textTransform: 'uppercase',
      fontSize: '.7rem',
      letterSpacing: '.06em',
      color: 'var(--ink-3)',
      fontWeight: 700
    }
  }, "Lifetime won"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: '1.5rem',
      fontWeight: 900,
      color: 'var(--live)'
    }
  }, S.money(rec.lifetime || 0)))), /*#__PURE__*/React.createElement("div", {
    style: {
      height: 9,
      background: 'var(--card-2)',
      borderRadius: 6,
      overflow: 'hidden',
      margin: '14px 0 7px'
    }
  }, /*#__PURE__*/React.createElement("i", {
    style: {
      display: 'block',
      height: '100%',
      width: pct + '%',
      background: 'linear-gradient(90deg, var(--c-teal), var(--c-violet))'
    }
  })), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: '.82rem',
      fontWeight: 700
    }
  }, t.isMax ? `Top tier — you're a ${t.name}. First-ballot.` : `${S.money(Math.max(0, t.nextGate - (rec.lifetime || 0)))} to ${t.nextName}`), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: '.78rem',
      marginTop: 6
    }
  }, "Restart stake at ", t.name, ": ", /*#__PURE__*/React.createElement("b", null, S.money(t.startBankroll)), !t.isMax ? ` · ${t.nextName} unlocks ${S.money(t.nextStart)}` : '')), /*#__PURE__*/React.createElement("div", {
    className: "tiles"
  }, /*#__PURE__*/React.createElement("div", {
    className: "tile"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Net P&L"), /*#__PURE__*/React.createElement("div", {
    className: "val",
    style: {
      color: (rec.net || 0) >= 0 ? 'var(--live)' : 'var(--down)'
    }
  }, (rec.net || 0) >= 0 ? '+' : '−', S.money(Math.abs(rec.net || 0))), /*#__PURE__*/React.createElement("div", {
    className: "sub"
  }, "all-time")), /*#__PURE__*/React.createElement("div", {
    className: "tile"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Peak"), /*#__PURE__*/React.createElement("div", {
    className: "val"
  }, S.money(rec.peak_bankroll || 0)), /*#__PURE__*/React.createElement("div", {
    className: "sub"
  }, "highest bankroll")), /*#__PURE__*/React.createElement("div", {
    className: "tile"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Biggest win"), /*#__PURE__*/React.createElement("div", {
    className: "val"
  }, S.money(rec.biggest_win || 0)), /*#__PURE__*/React.createElement("div", {
    className: "sub"
  }, "single payout")), /*#__PURE__*/React.createElement("div", {
    className: "tile"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Best streak"), /*#__PURE__*/React.createElement("div", {
    className: "val"
  }, rec.best_streak || 0), /*#__PURE__*/React.createElement("div", {
    className: "sub"
  }, "Go Streaking")), /*#__PURE__*/React.createElement("div", {
    className: "tile"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Cashes"), /*#__PURE__*/React.createElement("div", {
    className: "val"
  }, rec.cashes || 0, "/", rec.entries || 0), /*#__PURE__*/React.createElement("div", {
    className: "sub"
  }, "entries cashed")))), /*#__PURE__*/React.createElement("div", {
    className: "section-head mt-32"
  }, /*#__PURE__*/React.createElement("h2", null, "Game library"), /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: '.85rem',
      fontWeight: 600,
      whiteSpace: 'nowrap'
    }
  }, "Eight ways to play your league")), /*#__PURE__*/React.createElement("div", {
    className: "fmt-grid"
  }, S.FORMATS.map(f => /*#__PURE__*/React.createElement("a", {
    key: f.id,
    className: 'fmt' + (f.live || f.view ? '' : ' fmt--soon'),
    onClick: () => f.view ? onNav(f.view) : onOpenFormat(f)
  }, /*#__PURE__*/React.createElement("span", {
    className: "fmt__glow",
    style: {
      background: f.color
    }
  }), /*#__PURE__*/React.createElement("span", {
    className: "fmt__icon",
    style: {
      background: f.color
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: f.icon,
    size: 24
  })), f.tag && /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      top: 16,
      right: 16
    }
  }, /*#__PURE__*/React.createElement(Tag, {
    kind: f.tag === 'live' ? 'live' : 'new'
  }, f.tag === 'live' ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", {
    className: "pulse"
  }), " Live") : 'New')), /*#__PURE__*/React.createElement("div", {
    className: "fmt__name"
  }, f.name), /*#__PURE__*/React.createElement("div", {
    className: "fmt__desc"
  }, f.desc), /*#__PURE__*/React.createElement("div", {
    className: "fmt__foot"
  }, f.stat.map((s, i) => /*#__PURE__*/React.createElement("span", {
    key: i,
    className: "fmt__stat"
  }, s)))))))));
}

/* ---------- DFS LOBBY ---------- */
function LobbyScreen({
  onNav,
  onEnterContest
}) {
  const S = window.SLATE;
  const [slate, setSlate] = useState('main');
  const slates = [{
    id: 'main',
    label: 'Main · 4 games',
    time: '6:05'
  }, {
    id: 'turbo',
    label: 'Turbo · 2 games',
    time: '7:40'
  }, {
    id: 'late',
    label: 'Late Voyage · 1 game',
    time: '8:10'
  }];
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(TopBar, {
    title: "Daily Slate",
    sub: "Pick a contest, then build",
    back: true,
    onBack: () => onNav('hub'),
    right: /*#__PURE__*/React.createElement("span", {
      className: "simclock hide-mobile"
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "clock",
      size: 15
    }), " Locks ", /*#__PURE__*/React.createElement("span", {
      className: "num"
    }, "6:05"))
  }), /*#__PURE__*/React.createElement("div", {
    className: "app__scroll"
  }, /*#__PURE__*/React.createElement("div", {
    className: "page"
  }, /*#__PURE__*/React.createElement("div", {
    className: "slate-tabs"
  }, slates.map(s => /*#__PURE__*/React.createElement(Chip, {
    key: s.id,
    active: slate === s.id,
    onClick: () => setSlate(s.id)
  }, s.label))), /*#__PURE__*/React.createElement("div", {
    className: "card card--pad mb-16"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow mb-12"
  }, "Tonight's games", S.SIM_DAY ? ` · ${S.SIM_DAY}` : ''), /*#__PURE__*/React.createElement("div", {
    className: "row wrap",
    style: {
      gap: 10
    }
  }, S.SLATE_GAMES.map((g, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "row",
    style: {
      gap: 8,
      padding: '8px 14px',
      background: 'var(--paper-2)',
      borderRadius: 'var(--r)'
    }
  }, /*#__PURE__*/React.createElement("b", {
    className: "num",
    style: {
      color: S.TEAMS[g.away].color
    }
  }, g.away), /*#__PURE__*/React.createElement("span", {
    className: "dim",
    style: {
      fontSize: '.75rem'
    }
  }, "@"), /*#__PURE__*/React.createElement("b", {
    className: "num",
    style: {
      color: S.TEAMS[g.home].color
    }
  }, g.home), /*#__PURE__*/React.createElement("span", {
    className: "dim num",
    style: {
      fontSize: '.74rem',
      marginLeft: 4
    }
  }, g.time))))), /*#__PURE__*/React.createElement("div", {
    className: "section-head"
  }, /*#__PURE__*/React.createElement("h2", null, "Contests"), /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: '.85rem',
      fontWeight: 600,
      whiteSpace: 'nowrap'
    }
  }, S.CONTESTS.length, " open")), /*#__PURE__*/React.createElement("div", {
    className: "col",
    style: {
      gap: 12
    }
  }, S.CONTESTS.map(c => /*#__PURE__*/React.createElement("div", {
    key: c.id,
    className: "contest"
  }, /*#__PURE__*/React.createElement("span", {
    className: "contest__badge",
    style: {
      background: c.color
    }
  }, c.badge), /*#__PURE__*/React.createElement("div", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "contest__name"
  }, c.name), /*#__PURE__*/React.createElement("div", {
    className: "contest__meta"
  }, /*#__PURE__*/React.createElement("span", null, c.kind), /*#__PURE__*/React.createElement("span", null, "Entry ", /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, c.fee === 0 ? 'Free' : S.money(c.fee))), /*#__PURE__*/React.createElement("span", null, "Top prize ", /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, S.money(c.top))), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, c.entries.toLocaleString('en-IN')), "/", c.cap.toLocaleString('en-IN'))), /*#__PURE__*/React.createElement("div", {
    className: "fill"
  }, /*#__PURE__*/React.createElement("i", {
    style: {
      width: Math.min(100, c.entries / c.cap * 100) + '%'
    }
  }))), /*#__PURE__*/React.createElement("div", {
    className: "contest__prize"
  }, /*#__PURE__*/React.createElement("div", {
    className: "amt"
  }, S.money(c.prize)), /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "Prize pool"), /*#__PURE__*/React.createElement(Btn, {
    variant: "brand",
    size: "sm",
    className: "mt-8",
    onClick: () => onEnterContest(c)
  }, "Enter"))))))));
}

/* ---------- MY ENTRIES (real data) ---------- */
function EntriesScreen({
  onNav,
  onOpenContest
}) {
  const S = window.SLATE;
  const [tab, setTab] = useState('live');
  const [entries, setEntries] = useState(null);
  useEffect(() => {
    fetch('/fantasy/api/entries').then(r => r.ok ? r.json() : []).then(setEntries).catch(() => setEntries([]));
  }, []);
  const all = entries || [];
  const bucket = e => e.games_total > 0 && e.games_done >= e.games_total ? 'past' : e.games_done > 0 ? 'live' : 'upcoming';
  const live = all.filter(e => bucket(e) === 'live');
  const upcoming = all.filter(e => bucket(e) === 'upcoming');
  const past = all.filter(e => bucket(e) === 'past');
  const rows = tab === 'past' ? past : tab === 'upcoming' ? upcoming : live;
  const prog = e => e.games_total ? e.games_done / e.games_total : 0;
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(TopBar, {
    title: "My Entries",
    sub: "Track your lineups",
    back: true,
    onBack: () => onNav('hub')
  }), /*#__PURE__*/React.createElement("div", {
    className: "app__scroll"
  }, /*#__PURE__*/React.createElement("div", {
    className: "page page--narrow"
  }, /*#__PURE__*/React.createElement("div", {
    className: "slate-tabs"
  }, /*#__PURE__*/React.createElement(Chip, {
    active: tab === 'live',
    onClick: () => setTab('live')
  }, "Live \xB7 ", live.length), /*#__PURE__*/React.createElement(Chip, {
    active: tab === 'upcoming',
    onClick: () => setTab('upcoming')
  }, "Upcoming \xB7 ", upcoming.length), /*#__PURE__*/React.createElement(Chip, {
    active: tab === 'past',
    onClick: () => setTab('past')
  }, "Past \xB7 ", past.length)), entries === null ? /*#__PURE__*/React.createElement("div", {
    className: "card card--pad center",
    style: {
      padding: '48px 20px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dim",
    style: {
      fontWeight: 600
    }
  }, "Loading your entries\u2026")) : rows.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "card card--pad center",
    style: {
      padding: '48px 20px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dim",
    style: {
      fontWeight: 600
    }
  }, "No ", tab, " entries."), /*#__PURE__*/React.createElement(Btn, {
    variant: "soft",
    className: "mt-16",
    onClick: () => onNav('lobby')
  }, "Find a contest")) : /*#__PURE__*/React.createElement("div", {
    className: "col",
    style: {
      gap: 12
    }
  }, rows.map((e, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "contest",
    onClick: () => onOpenContest && onOpenContest(e.contest_id),
    style: {
      cursor: 'pointer'
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "contest__badge",
    style: {
      background: e.color
    }
  }, e.badge), /*#__PURE__*/React.createElement("div", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "contest__name"
  }, e.contest), /*#__PURE__*/React.createElement("div", {
    className: "contest__meta"
  }, e.rank != null && /*#__PURE__*/React.createElement("span", null, "Rank ", /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, e.rank.toLocaleString('en-IN')), "/", (e.of || 0).toLocaleString('en-IN')), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, e.pts.toFixed(1)), " pts"), /*#__PURE__*/React.createElement("span", {
    style: {
      color: e.live ? 'var(--live)' : 'var(--ink-3)',
      fontWeight: 700
    }
  }, e.live ? 'Live' : 'Final')), /*#__PURE__*/React.createElement("div", {
    className: "fill"
  }, /*#__PURE__*/React.createElement("i", {
    style: {
      width: prog(e) * 100 + '%'
    }
  }))), /*#__PURE__*/React.createElement("div", {
    className: "contest__prize"
  }, /*#__PURE__*/React.createElement("div", {
    className: "amt num"
  }, e.games_done, "/", e.games_total), /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, "games"))))))));
}

/* ---------- COMING-SOON teaser for non-DFS formats ---------- */
function FormatTeaser({
  fmt,
  onClose,
  onNav
}) {
  if (!fmt) return null;
  return /*#__PURE__*/React.createElement("div", {
    className: 'scrim scrim--open',
    onClick: onClose
  }, /*#__PURE__*/React.createElement("div", {
    className: "drawer",
    onClick: e => e.stopPropagation()
  }, /*#__PURE__*/React.createElement("div", {
    className: "drawer__hero",
    style: {
      background: fmt.color
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "drawer__close",
    onClick: onClose
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "x",
    size: 18
  })), /*#__PURE__*/React.createElement("span", {
    className: "fmt__icon",
    style: {
      background: 'rgba(255,255,255,.22)',
      width: 54,
      height: 54
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: fmt.icon,
    size: 28
  })), /*#__PURE__*/React.createElement("div", {
    className: "drawer__name mt-12"
  }, fmt.name), /*#__PURE__*/React.createElement("div", {
    className: "drawer__meta"
  }, fmt.stat.join(' · '))), /*#__PURE__*/React.createElement("div", {
    className: "drawer__body"
  }, /*#__PURE__*/React.createElement("p", {
    className: "muted",
    style: {
      fontSize: '.95rem',
      lineHeight: 1.55
    }
  }, fmt.desc), /*#__PURE__*/React.createElement("div", {
    className: "card card--pad mt-16"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow mb-12"
  }, "Why it only works here"), /*#__PURE__*/React.createElement("p", {
    className: "muted",
    style: {
      margin: 0,
      fontSize: '.88rem'
    }
  }, fmt.id === 'stay' && 'The stay — a runner re-entering the play after a second-chance — has no MLB analog. Every stay, stay-RBI and graded RAD advancement is already a column in your save, so this is pure scoring config.', fmt.id === 'walkback' && 'In O27 a home run plants the hitter at third as a persistent Walk-Back runner. Power gains a tail of value that depends on who bats behind them — a draft puzzle real baseball can\u2019t pose.', fmt.id === 'pilot' && 'O27 has no bullpen — one continuous 27-out arc. Scoring only arc-3 work makes a finisher format out of a sport that structurally has no closers. Value emerges from fatigue, not role.', fmt.id === 'skipper' && 'Your save persists manager telemetry MLB never generates — declared seconds, shift outs added, joker deployment. So you can draft decisions instead of players.', fmt.id === 'voyage' && 'Top-of-order hitters see 5\u20137 PA a game, so \u201cget a hit\u201d is too easy. The bar rises in a sport-native way: a multi-hit AB or a driven-in stay run.', fmt.id === 'hothand' && 'You already model streak state, heat and work ethic. This format scores streak-weighted production — a momentum-trading game powered by your in-season variance model.', fmt.id === 'joker' && 'Jokers are tactical plate appearances with archetypes. Draft an archetype portfolio and score on joker-deployed outcomes — a small, weird, very O27 side-game.', fmt.id === 'dfs' && 'Daily salary-cap lineups on tonight\u2019s sim games — the flagship.')), fmt.live ? /*#__PURE__*/React.createElement(Btn, {
    variant: "brand",
    block: true,
    className: "mt-16",
    onClick: () => {
      onClose();
      onNav('lobby');
    }
  }, "Play now ", /*#__PURE__*/React.createElement(Icon, {
    name: "chev",
    size: 18
  })) : /*#__PURE__*/React.createElement(Btn, {
    variant: "ink",
    block: true,
    className: "mt-16"
  }, "Notify me when it opens ", /*#__PURE__*/React.createElement(Icon, {
    name: "bell",
    size: 17
  })), /*#__PURE__*/React.createElement("div", {
    className: "center muted mt-12",
    style: {
      fontSize: '.78rem'
    }
  }, "Runs on your active save \xB7 zero extra data"))));
}

/* ---------- GO STREAKING (hit-streak survivor) ---------- */
function StreakScreen({
  onNav,
  onOpenPlayer
}) {
  const S = window.SLATE;
  const [data, setData] = useState(null);
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  function load() {
    fetch('/fantasy/api/streak').then(r => r.ok ? r.json() : null).then(setData).catch(() => setData(null));
  }
  useEffect(load, []);
  function pick(p) {
    if (busy) return;
    setBusy(true);
    fetch('/fantasy/api/streak/pick', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        player_id: p.id
      })
    }).then(r => r.json()).then(j => {
      setBusy(false);
      if (!j.ok) window.alert(j.error || 'Could not make that pick.');
      load();
    }).catch(() => setBusy(false));
  }
  const d = data;
  const pool = d ? d.pool || [] : [];
  const shown = q.trim() ? pool.filter(p => p.name.toLowerCase().includes(q.toLowerCase())) : pool;
  const resColor = r => r === 'hit' ? 'var(--live)' : r === 'miss' ? 'var(--down)' : 'var(--ink-3)';
  const resLabel = r => r === 'hit' ? 'Hit' : r === 'miss' ? 'Miss' : r === 'pending' ? 'Live' : '—';
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(TopBar, {
    title: "Go Streaking",
    sub: "Pick a hit, build a streak",
    back: true,
    onBack: () => onNav('hub')
  }), /*#__PURE__*/React.createElement("div", {
    className: "app__scroll"
  }, /*#__PURE__*/React.createElement("div", {
    className: "page page--narrow"
  }, !d ? /*#__PURE__*/React.createElement("div", {
    className: "card card--pad center",
    style: {
      padding: '48px 20px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dim",
    style: {
      fontWeight: 600
    }
  }, "Loading\u2026")) : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "hero",
    style: {
      background: 'linear-gradient(135deg, var(--c-green), var(--c-teal))'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "hero__in"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow",
    style: {
      color: 'rgba(255,255,255,.75)'
    }
  }, "Current streak"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: '3.4rem',
      fontWeight: 900,
      lineHeight: 1
    }
  }, d.current), /*#__PURE__*/React.createElement("p", {
    style: {
      marginTop: 6
    }
  }, "Best run: ", /*#__PURE__*/React.createElement("b", null, d.best), ". One hit keeps it alive \u2014 a hitless day starts you over."))), /*#__PURE__*/React.createElement("div", {
    className: "section-head mt-24"
  }, /*#__PURE__*/React.createElement("h2", null, "Tonight's pick"), /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: '.85rem',
      fontWeight: 600
    }
  }, d.slate_date || '—')), !d.slate_date ? /*#__PURE__*/React.createElement("div", {
    className: "card card--pad center",
    style: {
      padding: '32px 20px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dim",
    style: {
      fontWeight: 600
    }
  }, "No upcoming slate to pick. Sim forward to keep streaking.")) : d.today_pick ? /*#__PURE__*/React.createElement("div", {
    className: "contest"
  }, /*#__PURE__*/React.createElement("span", {
    className: "contest__badge",
    style: {
      background: 'var(--c-green)'
    }
  }, (d.today_pick.team || '?').slice(0, 2)), /*#__PURE__*/React.createElement("div", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "contest__name"
  }, d.today_pick.name), /*#__PURE__*/React.createElement("div", {
    className: "contest__meta"
  }, /*#__PURE__*/React.createElement("span", null, d.today_pick.team), /*#__PURE__*/React.createElement("span", null, "your pick \u2014 needs a hit"))), /*#__PURE__*/React.createElement("div", {
    className: "contest__prize"
  }, /*#__PURE__*/React.createElement("div", {
    className: "amt",
    style: {
      color: resColor(d.today_pick.result)
    }
  }, resLabel(d.today_pick.result)), /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, d.today_pick.result === 'pending' ? 'in progress' : 'result'))) : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "search mb-12"
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "search",
    size: 17
  }), /*#__PURE__*/React.createElement("input", {
    placeholder: "Search hitters\u2026",
    value: q,
    onChange: e => setQ(e.target.value)
  })), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, shown.length === 0 && /*#__PURE__*/React.createElement("div", {
    className: "center muted",
    style: {
      padding: 24,
      fontWeight: 600
    }
  }, "No hitters on the upcoming slate."), shown.map(p => /*#__PURE__*/React.createElement("div", {
    key: p.id,
    className: "prow"
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__id",
    onClick: () => onOpenPlayer && onOpenPlayer(p),
    style: {
      cursor: 'pointer'
    }
  }, /*#__PURE__*/React.createElement(PlayerMark, {
    p: {
      init: p.init,
      teamColor: p.teamColor
    }
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
  }, p.team), " ", p.opp))), /*#__PURE__*/React.createElement("button", {
    className: "add-btn",
    disabled: busy,
    title: "Pick this hitter",
    onClick: () => pick(p)
  }, "+"))))), d.history && d.history.length > 0 && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "section-head mt-24"
  }, /*#__PURE__*/React.createElement("h2", null, "Recent picks")), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, d.history.map((h, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "lb-row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lb-rank"
  }, (h.slate_date || '').slice(5)), /*#__PURE__*/React.createElement("div", {
    className: "lb-user"
  }, /*#__PURE__*/React.createElement("b", {
    style: {
      fontSize: '.9rem'
    }
  }, h.player), " ", /*#__PURE__*/React.createElement("span", {
    className: "dim"
  }, h.team)), /*#__PURE__*/React.createElement("div", {
    className: "lb-pts",
    style: {
      color: resColor(h.result),
      fontWeight: 800
    }
  }, resLabel(h.result))))))))));
}

/* ---------- SLUGGERS (Walk-Back home-run game) ---------- */
function SluggersScreen({
  onNav,
  onOpenPlayer
}) {
  const S = window.SLATE;
  const [data, setData] = useState(null);
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  function load() {
    fetch('/fantasy/api/sluggers').then(r => r.ok ? r.json() : null).then(setData).catch(() => setData(null));
  }
  useEffect(load, []);
  function act(path, p) {
    if (busy) return;
    setBusy(true);
    fetch('/fantasy/api/sluggers/' + path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        player_id: p.id
      })
    }).then(r => r.json()).then(j => {
      setBusy(false);
      if (!j.ok) window.alert(j.error || 'Could not do that.');
      load();
    }).catch(() => setBusy(false));
  }
  const d = data;
  const ys = d && d.your_slate;
  const picks = ys ? ys.picks : [];
  const pool = d ? d.pool || [] : [];
  const shown = q.trim() ? pool.filter(p => p.name.toLowerCase().includes(q.toLowerCase())) : pool;
  const slotsLeft = d ? d.max - (d.picked || 0) : 0;
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(TopBar, {
    title: "Sluggers",
    sub: "Bank the bombs",
    back: true,
    onBack: () => onNav('hub')
  }), /*#__PURE__*/React.createElement("div", {
    className: "app__scroll"
  }, /*#__PURE__*/React.createElement("div", {
    className: "page page--narrow"
  }, !d ? /*#__PURE__*/React.createElement("div", {
    className: "card card--pad center",
    style: {
      padding: '48px 20px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dim",
    style: {
      fontWeight: 600
    }
  }, "Loading\u2026")) : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "hero",
    style: {
      background: 'linear-gradient(135deg, var(--c-violet), var(--c-coral))'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "hero__in"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow",
    style: {
      color: 'rgba(255,255,255,.75)'
    }
  }, "Season slugger points"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: '3.2rem',
      fontWeight: 900,
      lineHeight: 1
    }
  }, d.season), /*#__PURE__*/React.createElement("p", {
    style: {
      marginTop: 6
    }
  }, "HR ", /*#__PURE__*/React.createElement("b", null, "\xD74"), " \xB7 Walk-Back run ", /*#__PURE__*/React.createElement("b", null, "\xD74"), " \xB7 RBI ", /*#__PURE__*/React.createElement("b", null, "\xD71"), " \u2014 the homer plus the runs it brings home."), d.buyIn ? /*#__PURE__*/React.createElement("p", {
    style: {
      marginTop: 4,
      fontSize: '.84rem',
      opacity: .9
    }
  }, "Buy-in ", S.money(d.buyIn), "/slate \xB7 beat the field to cash", d.entered ? ' · entered ✓' : '') : null)), /*#__PURE__*/React.createElement("div", {
    className: "section-head mt-24"
  }, /*#__PURE__*/React.createElement("h2", null, "Tonight's sluggers"), /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: '.85rem',
      fontWeight: 600
    }
  }, d.slate_date || '—')), picks.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "card mb-12",
    style: {
      overflow: 'hidden'
    }
  }, picks.map((p, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "prow"
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__id",
    onClick: () => onOpenPlayer && onOpenPlayer(p),
    style: {
      cursor: 'pointer'
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "contest__badge",
    style: {
      background: 'var(--c-violet)'
    }
  }, (p.team || '?').slice(0, 2)), /*#__PURE__*/React.createElement("div", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__name"
  }, p.name), /*#__PURE__*/React.createElement("div", {
    className: "prow__sub"
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontWeight: 700
    }
  }, p.team), " \xB7 ", p.pts == null ? 'in progress' : `${p.pts} pts`))), ys && ys.settled ? /*#__PURE__*/React.createElement("div", {
    className: "lb-pts",
    style: {
      fontWeight: 800
    }
  }, p.pts) : /*#__PURE__*/React.createElement("button", {
    className: "add-btn",
    disabled: busy,
    title: "Drop",
    onClick: () => act('remove', p),
    style: {
      background: 'var(--down)'
    }
  }, "\u2212"))), ys && ys.fieldAvg != null && /*#__PURE__*/React.createElement("div", {
    className: "lb-row",
    style: {
      borderTop: '1px solid var(--line)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "lb-user dim",
    style: {
      fontWeight: 600
    }
  }, "Your ", ys.score, " \xB7 field avg ", ys.fieldAvg, " \xB7 ceiling ", ys.ceiling))), !d.slate_date ? /*#__PURE__*/React.createElement("div", {
    className: "card card--pad center",
    style: {
      padding: '32px 20px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dim",
    style: {
      fontWeight: 600
    }
  }, "No upcoming slate. Sim forward to keep slugging.")) : slotsLeft > 0 ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "muted mb-12",
    style: {
      fontSize: '.85rem',
      fontWeight: 600
    }
  }, slotsLeft, " slot", slotsLeft > 1 ? 's' : '', " left \xB7 sorted by season HR"), /*#__PURE__*/React.createElement("div", {
    className: "search mb-12"
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "search",
    size: 17
  }), /*#__PURE__*/React.createElement("input", {
    placeholder: "Search hitters\u2026",
    value: q,
    onChange: e => setQ(e.target.value)
  })), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, shown.length === 0 && /*#__PURE__*/React.createElement("div", {
    className: "center muted",
    style: {
      padding: 24,
      fontWeight: 600
    }
  }, "No hitters on the upcoming slate."), shown.map(p => /*#__PURE__*/React.createElement("div", {
    key: p.id,
    className: "prow"
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__id",
    onClick: () => onOpenPlayer && onOpenPlayer(p),
    style: {
      cursor: 'pointer'
    }
  }, /*#__PURE__*/React.createElement(PlayerMark, {
    p: {
      init: p.init,
      teamColor: p.teamColor
    }
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
  }, p.team), " ", p.opp, " \xB7 ", /*#__PURE__*/React.createElement("span", {
    title: "season home runs"
  }, p.hr, " HR")))), /*#__PURE__*/React.createElement("button", {
    className: "add-btn",
    disabled: busy,
    title: "Add slugger",
    onClick: () => act('pick', p)
  }, "+"))))) : /*#__PURE__*/React.createElement("div", {
    className: "card card--pad center",
    style: {
      padding: '20px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dim",
    style: {
      fontWeight: 600
    }
  }, "Lineup full \u2014 ", d.max, " sluggers locked for tonight.")), d.history && d.history.length > 0 && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "section-head mt-24"
  }, /*#__PURE__*/React.createElement("h2", null, "Past slates")), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, d.history.map((h, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "lb-row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lb-rank"
  }, (h.slate_date || '').slice(5)), /*#__PURE__*/React.createElement("div", {
    className: "lb-user"
  }, /*#__PURE__*/React.createElement("b", {
    style: {
      fontSize: '.9rem'
    }
  }, h.score, " pts"), " ", /*#__PURE__*/React.createElement("span", {
    className: "dim"
  }, "vs field ", h.fieldAvg != null ? h.fieldAvg : '—')), /*#__PURE__*/React.createElement("div", {
    className: "lb-pts",
    style: {
      color: (h.payout || 0) - (d.buyIn || 0) > 0 ? 'var(--live)' : (h.payout || 0) - (d.buyIn || 0) < 0 ? 'var(--down)' : 'var(--ink-3)',
      fontWeight: 800
    }
  }, (h.payout || 0) - (d.buyIn || 0) > 0 ? `+${S.money((h.payout || 0) - (d.buyIn || 0))}` : (h.payout || 0) - (d.buyIn || 0) < 0 ? `−${S.money((d.buyIn || 0) - (h.payout || 0))}` : 'push')))))))));
}

/* ---------- PILOTS (pitching game) ---------- */
function PilotsScreen({
  onNav,
  onOpenPlayer
}) {
  const S = window.SLATE;
  const [data, setData] = useState(null);
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  function load() {
    fetch('/fantasy/api/pilots').then(r => r.ok ? r.json() : null).then(setData).catch(() => setData(null));
  }
  useEffect(load, []);
  function act(path, p) {
    if (busy) return;
    setBusy(true);
    fetch('/fantasy/api/pilots/' + path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        player_id: p.id
      })
    }).then(r => r.json()).then(j => {
      setBusy(false);
      if (!j.ok) window.alert(j.error || 'Could not do that.');
      load();
    }).catch(() => setBusy(false));
  }
  const d = data;
  const ys = d && d.your_slate;
  const picks = ys ? ys.picks : [];
  const pool = d ? d.pool || [] : [];
  const shown = q.trim() ? pool.filter(p => p.name.toLowerCase().includes(q.toLowerCase())) : pool;
  const slotsLeft = d ? d.max - (d.picked || 0) : 0;
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(TopBar, {
    title: "Pilots",
    sub: "Work the mound",
    back: true,
    onBack: () => onNav('hub')
  }), /*#__PURE__*/React.createElement("div", {
    className: "app__scroll"
  }, /*#__PURE__*/React.createElement("div", {
    className: "page page--narrow"
  }, !d ? /*#__PURE__*/React.createElement("div", {
    className: "card card--pad center",
    style: {
      padding: '48px 20px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dim",
    style: {
      fontWeight: 600
    }
  }, "Loading\u2026")) : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "hero",
    style: {
      background: 'linear-gradient(135deg, var(--c-blue), var(--c-teal))'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "hero__in"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow",
    style: {
      color: 'rgba(255,255,255,.75)'
    }
  }, "Season pilot points"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: '3.2rem',
      fontWeight: 900,
      lineHeight: 1
    }
  }, d.season), /*#__PURE__*/React.createElement("p", {
    style: {
      marginTop: 6
    }
  }, "K ", /*#__PURE__*/React.createElement("b", null, "\xD73"), " \xB7 Out ", /*#__PURE__*/React.createElement("b", null, "\xD71"), " \xB7 ER ", /*#__PURE__*/React.createElement("b", null, "\u22122"), " \xB7 Quality Start ", /*#__PURE__*/React.createElement("b", null, "+6"), " \xB7 Quality Finish ", /*#__PURE__*/React.createElement("b", null, "+6"), "."), d.buyIn ? /*#__PURE__*/React.createElement("p", {
    style: {
      marginTop: 4,
      fontSize: '.84rem',
      opacity: .9
    }
  }, "Buy-in ", S.money(d.buyIn), "/slate \xB7 beat the field to cash", d.entered ? ' · entered ✓' : '') : null)), /*#__PURE__*/React.createElement("div", {
    className: "section-head mt-24"
  }, /*#__PURE__*/React.createElement("h2", null, "Tonight's pilots"), /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: '.85rem',
      fontWeight: 600
    }
  }, d.slate_date || '—')), picks.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "card mb-12",
    style: {
      overflow: 'hidden'
    }
  }, picks.map((p, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "prow"
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__id",
    onClick: () => onOpenPlayer && onOpenPlayer(p),
    style: {
      cursor: 'pointer'
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "contest__badge",
    style: {
      background: 'var(--c-blue)'
    }
  }, (p.team || '?').slice(0, 2)), /*#__PURE__*/React.createElement("div", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__name"
  }, p.name), /*#__PURE__*/React.createElement("div", {
    className: "prow__sub"
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontWeight: 700
    }
  }, p.team), " \xB7 ", p.pts == null ? 'in progress' : `${p.pts} pts`))), ys && ys.settled ? /*#__PURE__*/React.createElement("div", {
    className: "lb-pts",
    style: {
      fontWeight: 800
    }
  }, p.pts) : /*#__PURE__*/React.createElement("button", {
    className: "add-btn",
    disabled: busy,
    title: "Drop",
    onClick: () => act('remove', p),
    style: {
      background: 'var(--down)'
    }
  }, "\u2212"))), ys && ys.fieldAvg != null && /*#__PURE__*/React.createElement("div", {
    className: "lb-row",
    style: {
      borderTop: '1px solid var(--line)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "lb-user dim",
    style: {
      fontWeight: 600
    }
  }, "Your ", ys.score, " \xB7 field avg ", ys.fieldAvg, " \xB7 ceiling ", ys.ceiling))), !d.slate_date ? /*#__PURE__*/React.createElement("div", {
    className: "card card--pad center",
    style: {
      padding: '32px 20px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dim",
    style: {
      fontWeight: 600
    }
  }, "No upcoming slate. Sim forward to keep pitching.")) : slotsLeft > 0 ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "muted mb-12",
    style: {
      fontSize: '.85rem',
      fontWeight: 600
    }
  }, slotsLeft, " slot", slotsLeft > 1 ? 's' : '', " left \xB7 sorted by projection"), /*#__PURE__*/React.createElement("div", {
    className: "search mb-12"
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "search",
    size: 17
  }), /*#__PURE__*/React.createElement("input", {
    placeholder: "Search pilots\u2026",
    value: q,
    onChange: e => setQ(e.target.value)
  })), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, shown.length === 0 && /*#__PURE__*/React.createElement("div", {
    className: "center muted",
    style: {
      padding: 24,
      fontWeight: 600
    }
  }, "No pilots on the upcoming slate."), shown.map(p => /*#__PURE__*/React.createElement("div", {
    key: p.id,
    className: "prow"
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__id",
    onClick: () => onOpenPlayer && onOpenPlayer(p),
    style: {
      cursor: 'pointer'
    }
  }, /*#__PURE__*/React.createElement(PlayerMark, {
    p: {
      init: p.init,
      teamColor: p.teamColor
    }
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
  }, p.team), " ", p.opp, " \xB7 ", /*#__PURE__*/React.createElement("span", {
    title: "projection"
  }, "proj ", p.proj)))), /*#__PURE__*/React.createElement("button", {
    className: "add-btn",
    disabled: busy,
    title: "Add pilot",
    onClick: () => act('pick', p)
  }, "+"))))) : /*#__PURE__*/React.createElement("div", {
    className: "card card--pad center",
    style: {
      padding: '20px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dim",
    style: {
      fontWeight: 600
    }
  }, "Staff full \u2014 ", d.max, " pilots locked for tonight.")), d.history && d.history.length > 0 && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "section-head mt-24"
  }, /*#__PURE__*/React.createElement("h2", null, "Past slates")), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, d.history.map((h, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "lb-row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lb-rank"
  }, (h.slate_date || '').slice(5)), /*#__PURE__*/React.createElement("div", {
    className: "lb-user"
  }, /*#__PURE__*/React.createElement("b", {
    style: {
      fontSize: '.9rem'
    }
  }, h.score, " pts"), " ", /*#__PURE__*/React.createElement("span", {
    className: "dim"
  }, "vs field ", h.fieldAvg != null ? h.fieldAvg : '—')), /*#__PURE__*/React.createElement("div", {
    className: "lb-pts",
    style: {
      color: (h.payout || 0) - (d.buyIn || 0) > 0 ? 'var(--live)' : (h.payout || 0) - (d.buyIn || 0) < 0 ? 'var(--down)' : 'var(--ink-3)',
      fontWeight: 800
    }
  }, (h.payout || 0) - (d.buyIn || 0) > 0 ? `+${S.money((h.payout || 0) - (d.buyIn || 0))}` : (h.payout || 0) - (d.buyIn || 0) < 0 ? `−${S.money((d.buyIn || 0) - (h.payout || 0))}` : 'push')))))))));
}

/* ---------- CATEGORY LEAGUES (Roto engine) ---------- */
function CategoriesScreen({
  onNav,
  onOpenPlayer
}) {
  const S = window.SLATE;
  const [fmt, setFmt] = useState('std5x5');
  const [data, setData] = useState(null);
  const [pool, setPool] = useState(null);
  const [sel, setSel] = useState([]);
  const [editing, setEditing] = useState(false);
  const [side, setSide] = useState('h');
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  function loadPool(f) {
    fetch('/fantasy/api/categories/pool?format=' + f).then(r => r.ok ? r.json() : {
      hitters: [],
      pitchers: []
    }).then(setPool).catch(() => setPool({
      hitters: [],
      pitchers: []
    }));
  }
  function loadState(f) {
    fetch('/fantasy/api/categories?format=' + f).then(r => r.ok ? r.json() : null).then(d => {
      setData(d);
      const complete = d && d.standings;
      setEditing(!complete);
      if (!complete) loadPool(f);
    }).catch(() => setData(null));
  }
  useEffect(() => {
    setSel([]);
    setQ('');
    loadState(fmt);
  }, [fmt]);
  const slots = data ? data.slots : {
    h: 0,
    p: 0
  };
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
    fetch('/fantasy/api/categories/draft', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        format: fmt,
        player_ids: sel.map(s => s.id)
      })
    }).then(r => r.json()).then(j => {
      setBusy(false);
      if (!j.ok) {
        window.alert(j.error || 'Draft failed.');
        return;
      }
      setEditing(false);
      loadState(fmt);
    }).catch(() => setBusy(false));
  }
  function reDraft() {
    setSel((data.roster || []).map(r => ({
      ...r,
      pos: r.pos === 'P' ? 'P' : 'H'
    })));
    loadPool(fmt);
    setEditing(true);
  }
  const st = data && data.standings;
  const onlyOneSide = slots.h === 0 || slots.p === 0;
  const showSide = onlyOneSide ? slots.p === 0 ? 'h' : 'p' : side;
  const list = pool ? showSide === 'p' ? pool.pitchers : pool.hitters : [];
  const shown = q.trim() ? list.filter(p => p.name.toLowerCase().includes(q.toLowerCase())) : list;
  const rankColor = (r, field) => r === 1 ? 'var(--live)' : r <= Math.ceil(field / 3) ? 'var(--c-teal)' : r >= field - Math.ceil(field / 3) ? 'var(--down)' : 'var(--ink-2)';
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(TopBar, {
    title: "Category Leagues",
    sub: "Season-long Roto",
    back: true,
    onBack: () => onNav('hub')
  }), /*#__PURE__*/React.createElement("div", {
    className: "app__scroll"
  }, /*#__PURE__*/React.createElement("div", {
    className: "page page--narrow"
  }, /*#__PURE__*/React.createElement("div", {
    className: "slate-tabs mb-12",
    style: {
      overflowX: 'auto',
      flexWrap: 'nowrap'
    }
  }, (data && data.formats || []).map(f => /*#__PURE__*/React.createElement(Chip, {
    key: f.key,
    active: f.key === fmt,
    onClick: () => setFmt(f.key)
  }, f.name))), !data ? /*#__PURE__*/React.createElement("div", {
    className: "card card--pad center",
    style: {
      padding: '48px 20px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dim",
    style: {
      fontWeight: 600
    }
  }, "Loading\u2026")) : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("p", {
    className: "muted mb-12",
    style: {
      fontSize: '.86rem',
      lineHeight: 1.45
    }
  }, curFmt.blurb), data.buyIn ? /*#__PURE__*/React.createElement("p", {
    className: "muted mb-12",
    style: {
      fontSize: '.82rem',
      fontWeight: 600
    }
  }, "Season buy-in ", S.money(data.buyIn), " \xB7 pays at season's end by final rank", data.entered ? ' · entered ✓' : '', data.payout > 0 ? ` · won ${S.money(data.payout)}` : '') : null, st && !editing ?
  /*#__PURE__*/
  /* ---- standings ---- */
  React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "hero",
    style: {
      background: st.dq ? 'linear-gradient(135deg,var(--down),var(--c-amber))' : 'linear-gradient(135deg, var(--c-teal), var(--c-blue))'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "hero__in"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow",
    style: {
      color: 'rgba(255,255,255,.75)'
    }
  }, "Roto points \xB7 rank"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: '3rem',
      fontWeight: 900,
      lineHeight: 1
    }
  }, st.roto, " ", /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: '1.3rem',
      opacity: .8
    }
  }, "/ ", st.max_points)), /*#__PURE__*/React.createElement("p", {
    style: {
      marginTop: 6
    }
  }, /*#__PURE__*/React.createElement("b", null, "#", st.rank), " of ", st.field, st.dq ? ' · DQ — below the AB/out floor, roster players who play!' : ''))), /*#__PURE__*/React.createElement("div", {
    className: "section-head mt-24"
  }, /*#__PURE__*/React.createElement("h2", null, "Categories"), /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: '.8rem',
      fontWeight: 600
    }
  }, "value \xB7 rank \xB7 pts")), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, st.categories.map(c => /*#__PURE__*/React.createElement("div", {
    key: c.key,
    className: "lb-row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lb-rank",
    style: {
      fontWeight: 800
    }
  }, c.label), /*#__PURE__*/React.createElement("div", {
    className: "lb-user"
  }, /*#__PURE__*/React.createElement("b", {
    style: {
      fontSize: '.95rem'
    }
  }, c.value)), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 10,
      alignItems: 'center'
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "pill",
    style: {
      background: rankColor(c.rank, st.field),
      color: '#fff',
      fontWeight: 800,
      padding: '2px 8px',
      borderRadius: 8,
      fontSize: '.78rem'
    }
  }, "#", c.rank), /*#__PURE__*/React.createElement("span", {
    className: "lb-pts",
    style: {
      fontWeight: 800,
      minWidth: 34,
      textAlign: 'right'
    }
  }, c.points))))), /*#__PURE__*/React.createElement("div", {
    className: "section-head mt-24"
  }, /*#__PURE__*/React.createElement("h2", null, "Your roster"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn--ghost btn--sm",
    onClick: reDraft
  }, "Re-draft")), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, (data.roster || []).map(p => /*#__PURE__*/React.createElement("div", {
    key: p.id,
    className: "prow"
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__id",
    onClick: () => onOpenPlayer && onOpenPlayer(p),
    style: {
      cursor: 'pointer'
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "contest__badge",
    style: {
      background: p.pos === 'P' ? 'var(--c-blue)' : 'var(--c-violet)'
    }
  }, (p.team || '?').slice(0, 2)), /*#__PURE__*/React.createElement("div", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__name"
  }, p.name), /*#__PURE__*/React.createElement("div", {
    className: "prow__sub"
  }, p.pos === 'P' ? 'Pitcher' : 'Hitter', " \xB7 ", p.team))))))) :
  /*#__PURE__*/
  /* ---- draft ---- */
  React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "card card--pad mb-12",
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontWeight: 700,
      fontSize: '.9rem'
    }
  }, slots.h > 0 && /*#__PURE__*/React.createElement("span", {
    style: {
      color: nH === slots.h ? 'var(--live)' : 'var(--ink-2)'
    }
  }, "Hitters ", nH, "/", slots.h), slots.h > 0 && slots.p > 0 && /*#__PURE__*/React.createElement("span", {
    className: "dim"
  }, " \xB7 "), slots.p > 0 && /*#__PURE__*/React.createElement("span", {
    style: {
      color: nP === slots.p ? 'var(--live)' : 'var(--ink-2)'
    }
  }, "Pitchers ", nP, "/", slots.p)), /*#__PURE__*/React.createElement("button", {
    className: "btn btn--brand btn--sm",
    disabled: !full || busy,
    onClick: lock
  }, "Lock roster")), sel.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "chips mb-12",
    style: {
      display: 'flex',
      flexWrap: 'wrap',
      gap: 6
    }
  }, sel.map(s => /*#__PURE__*/React.createElement("button", {
    key: s.id,
    className: "chip",
    onClick: () => setSel(sel.filter(x => x.id !== s.id)),
    style: {
      background: 'var(--surface-2)',
      border: '1px solid var(--line)',
      borderRadius: 14,
      padding: '4px 10px',
      fontSize: '.8rem',
      fontWeight: 700
    }
  }, s.name, " ", /*#__PURE__*/React.createElement("span", {
    className: "dim"
  }, s.pos), " \xD7"))), !onlyOneSide && /*#__PURE__*/React.createElement("div", {
    className: "slate-tabs mb-12"
  }, /*#__PURE__*/React.createElement(Chip, {
    active: showSide === 'h',
    onClick: () => setSide('h')
  }, "Hitters"), /*#__PURE__*/React.createElement(Chip, {
    active: showSide === 'p',
    onClick: () => setSide('p')
  }, "Pitchers")), /*#__PURE__*/React.createElement("div", {
    className: "search mb-12"
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "search",
    size: 17
  }), /*#__PURE__*/React.createElement("input", {
    placeholder: 'Search ' + (showSide === 'p' ? 'pitchers' : 'hitters') + '…',
    value: q,
    onChange: e => setQ(e.target.value)
  })), curFmt.invert && /*#__PURE__*/React.createElement("div", {
    className: "muted mb-12",
    style: {
      fontSize: '.8rem',
      fontWeight: 600,
      color: 'var(--c-amber)'
    }
  }, "Anti-league: worst production wins \u2014 but you must clear the playing-time floor."), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, !pool && /*#__PURE__*/React.createElement("div", {
    className: "center muted",
    style: {
      padding: 24,
      fontWeight: 600
    }
  }, "Loading pool\u2026"), pool && shown.length === 0 && /*#__PURE__*/React.createElement("div", {
    className: "center muted",
    style: {
      padding: 24,
      fontWeight: 600
    }
  }, "No players found."), shown.slice(0, 120).map(p => /*#__PURE__*/React.createElement("div", {
    key: p.id,
    className: "prow"
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__id",
    onClick: () => onOpenPlayer && onOpenPlayer(p),
    style: {
      cursor: 'pointer'
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "contest__badge",
    style: {
      background: p.pos === 'P' ? 'var(--c-blue)' : 'var(--c-violet)'
    }
  }, (p.team || '?').slice(0, 2)), /*#__PURE__*/React.createElement("div", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__name"
  }, p.name), /*#__PURE__*/React.createElement("div", {
    className: "prow__sub",
    style: {
      fontSize: '.74rem'
    }
  }, /*#__PURE__*/React.createElement("b", null, p.pos), " \xB7 ", p.line))), /*#__PURE__*/React.createElement("button", {
    className: "add-btn",
    disabled: selIds.has(p.id),
    title: "Draft",
    onClick: () => add(p)
  }, selIds.has(p.id) ? '✓' : '+')))))))));
}

/* ---------- SPORTSBOOK ---------- */
function SportsbookScreen({
  onNav
}) {
  const S = window.SLATE;
  const [data, setData] = useState(null);
  const [slip, setSlip] = useState(null); // {game_id, market, side, odds, line, label}
  const [stake, setStake] = useState(5000);
  const [busy, setBusy] = useState(false);
  function load() {
    fetch('/fantasy/api/sportsbook').then(r => r.ok ? r.json() : null).then(setData).catch(() => setData(null));
  }
  useEffect(load, []);
  const od = n => n > 0 ? '+' + n : '' + n;
  const dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / Math.abs(o);
  function pick(g, market, side, odds, line, label) {
    setSlip({
      game_id: g.game_id,
      market,
      side,
      odds,
      line,
      label
    });
  }
  function place() {
    if (!slip || busy) return;
    setBusy(true);
    fetch('/fantasy/api/sportsbook/bet', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        game_id: slip.game_id,
        market: slip.market,
        side: slip.side,
        stake
      })
    }).then(r => r.json()).then(j => {
      setBusy(false);
      if (!j.ok) {
        window.alert(j.error || 'Bet rejected.');
        return;
      }
      if (j.bankroll != null) S.WALLET = j.bankroll;
      setSlip(null);
      load();
    }).catch(() => setBusy(false));
  }
  const d = data;
  const sel = (g, market, side) => slip && slip.game_id === g.game_id && slip.market === market && slip.side === side;
  const oddsBtn = (g, market, side, odds, line, label) => /*#__PURE__*/React.createElement("button", {
    className: 'odds-btn' + (sel(g, market, side) ? ' is-on' : ''),
    onClick: () => pick(g, market, side, odds, line, label),
    style: {
      flex: 1,
      padding: '8px 6px',
      borderRadius: 10,
      border: '1px solid var(--line-2)',
      fontWeight: 700,
      fontSize: '.78rem',
      lineHeight: 1.3,
      background: sel(g, market, side) ? 'var(--brand-soft)' : 'var(--card)',
      color: sel(g, market, side) ? 'var(--brand-ink)' : 'var(--ink)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: '.72rem',
      opacity: .7
    }
  }, label), /*#__PURE__*/React.createElement("div", null, od(odds)));
  const statusColor = s => s === 'won' ? 'var(--live)' : s === 'lost' ? 'var(--down)' : 'var(--ink-3)';
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(TopBar, {
    title: "Sportsbook",
    sub: "Beat the house",
    back: true,
    onBack: () => onNav('hub')
  }), /*#__PURE__*/React.createElement("div", {
    className: "app__scroll"
  }, /*#__PURE__*/React.createElement("div", {
    className: "page page--narrow"
  }, !d ? /*#__PURE__*/React.createElement("div", {
    className: "card card--pad center",
    style: {
      padding: '48px 20px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dim",
    style: {
      fontWeight: 600
    }
  }, "Loading\u2026")) : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "hero",
    style: {
      background: 'linear-gradient(135deg, var(--c-amber), var(--c-coral))'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "hero__in"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow",
    style: {
      color: 'rgba(255,255,255,.75)'
    }
  }, "Wallet"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: '3rem',
      fontWeight: 900,
      lineHeight: 1
    }
  }, S.money(d.bankroll)), /*#__PURE__*/React.createElement("p", {
    style: {
      marginTop: 6
    }
  }, d.record.w, "\u2013", d.record.l, d.record.p ? `–${d.record.p}` : '', " \xB7 net ", /*#__PURE__*/React.createElement("b", null, d.record.net > 0 ? '+' : '−', S.money(Math.abs(d.record.net))), d.at_risk ? ` · ${S.money(d.at_risk)} at risk` : ''))), /*#__PURE__*/React.createElement("div", {
    className: "section-head mt-24"
  }, /*#__PURE__*/React.createElement("h2", null, "Tonight's board"), /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: '.85rem',
      fontWeight: 600
    }
  }, d.slate_date || '—')), d.games.length === 0 && /*#__PURE__*/React.createElement("div", {
    className: "card card--pad center",
    style: {
      padding: '24px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dim",
    style: {
      fontWeight: 600
    }
  }, "No games open for betting.")), d.games.map(g => /*#__PURE__*/React.createElement("div", {
    key: g.game_id,
    className: "card card--pad mb-12"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontWeight: 800,
      marginBottom: 8
    }
  }, g.away, " ", /*#__PURE__*/React.createElement("span", {
    className: "dim"
  }, "@"), " ", g.home), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 6,
      marginBottom: 6
    }
  }, oddsBtn(g, 'ml', 'away', g.ml_away, null, g.away + ' ML'), oddsBtn(g, 'ml', 'home', g.ml_home, null, g.home + ' ML')), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 6
    }
  }, oddsBtn(g, 'total', 'over', g.over_odds, g.total, 'Over ' + g.total), oddsBtn(g, 'total', 'under', g.under_odds, g.total, 'Under ' + g.total)))), d.open.length > 0 && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "section-head mt-24"
  }, /*#__PURE__*/React.createElement("h2", null, "Open bets")), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, d.open.map(b => /*#__PURE__*/React.createElement("div", {
    key: b.id,
    className: "lb-row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lb-user"
  }, /*#__PURE__*/React.createElement("b", {
    style: {
      fontSize: '.9rem'
    }
  }, b.desc), " ", /*#__PURE__*/React.createElement("span", {
    className: "dim"
  }, od(b.odds), " \xB7 ", b.matchup)), /*#__PURE__*/React.createElement("div", {
    className: "lb-pts",
    style: {
      fontWeight: 700
    }
  }, S.money(b.stake)))))), d.settled.length > 0 && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "section-head mt-24"
  }, /*#__PURE__*/React.createElement("h2", null, "Settled")), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, d.settled.map(b => /*#__PURE__*/React.createElement("div", {
    key: b.id,
    className: "lb-row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lb-user"
  }, /*#__PURE__*/React.createElement("b", {
    style: {
      fontSize: '.9rem'
    }
  }, b.desc), " ", /*#__PURE__*/React.createElement("span", {
    className: "dim"
  }, b.matchup, " ", b.score ? `(${b.score})` : '')), /*#__PURE__*/React.createElement("div", {
    className: "lb-pts",
    style: {
      color: statusColor(b.status),
      fontWeight: 800
    }
  }, b.status === 'won' ? `+${S.money(b.payout - b.stake)}` : b.status === 'lost' ? `−${S.money(b.stake)}` : 'push')))))))), slip && /*#__PURE__*/React.createElement("div", {
    className: "betslip",
    style: {
      position: 'sticky',
      bottom: 0,
      left: 0,
      right: 0,
      background: 'var(--card)',
      borderTop: '1px solid var(--line-2)',
      padding: '14px 18px',
      boxShadow: '0 -6px 18px rgba(0,0,0,.12)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      marginBottom: 8
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontWeight: 800
    }
  }, slip.label, " ", /*#__PURE__*/React.createElement("span", {
    className: "dim"
  }, od(slip.odds))), /*#__PURE__*/React.createElement("button", {
    className: "btn btn--ghost btn--sm",
    onClick: () => setSlip(null)
  }, "Cancel")), /*#__PURE__*/React.createElement("div", {
    className: "slate-tabs mb-12"
  }, [1000, 5000, 10000, 25000].map(v => /*#__PURE__*/React.createElement(Chip, {
    key: v,
    active: stake === v,
    onClick: () => setStake(v)
  }, S.money(v)))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dim",
    style: {
      fontWeight: 600,
      fontSize: '.85rem'
    }
  }, "Risk ", S.money(stake), " \u2192 win ", S.money(stake * (dec(slip.odds) - 1))), /*#__PURE__*/React.createElement("button", {
    className: "btn btn--brand",
    disabled: busy || stake > (d ? d.bankroll : 0),
    onClick: place
  }, "Place ", S.money(stake)))));
}

/* ---------- BEST BALL ---------- */
function BestBallScreen({
  onNav,
  onOpenPlayer
}) {
  const S = window.SLATE;
  const [data, setData] = useState(null);
  const [pool, setPool] = useState(null);
  const [sel, setSel] = useState([]);
  const [editing, setEditing] = useState(false);
  const [side, setSide] = useState('h');
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  function loadPool() {
    fetch('/fantasy/api/bestball/pool').then(r => r.ok ? r.json() : {
      hitters: [],
      pitchers: []
    }).then(setPool).catch(() => setPool({
      hitters: [],
      pitchers: []
    }));
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
  const slots = data ? data.slots : {
    h: 0,
    p: 0
  };
  const nH = sel.filter(s => s.pos !== 'P').length;
  const nP = sel.filter(s => s.pos === 'P').length;
  const full = nH === slots.h && nP === slots.p;
  const req = data && data.require || {};
  const haveByPos = {};
  sel.forEach(s => {
    if (s.pos !== 'P') haveByPos[s.pos] = (haveByPos[s.pos] || 0) + 1;
  });
  const posOk = Object.entries(req).every(([p, n]) => (haveByPos[p] || 0) >= n);
  const canLock = full && posOk;
  const selIds = new Set(sel.map(s => s.id));
  const list = pool ? side === 'p' ? pool.pitchers : pool.hitters : [];
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
    fetch('/fantasy/api/bestball/draft', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        player_ids: sel.map(s => s.id)
      })
    }).then(r => r.json()).then(j => {
      setBusy(false);
      if (!j.ok) {
        window.alert(j.error || 'Draft failed.');
        return;
      }
      setEditing(false);
      load();
    }).catch(() => setBusy(false));
  }
  function reDraft() {
    setSel((data.roster || []).map(r => ({
      ...r,
      pos: r.pos === 'P' ? 'P' : 'H'
    })));
    loadPool();
    setEditing(true);
  }
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(TopBar, {
    title: "Best Ball",
    sub: "Draft once, never touch it",
    back: true,
    onBack: () => onNav('hub')
  }), /*#__PURE__*/React.createElement("div", {
    className: "app__scroll"
  }, /*#__PURE__*/React.createElement("div", {
    className: "page page--narrow"
  }, !data ? /*#__PURE__*/React.createElement("div", {
    className: "card card--pad center",
    style: {
      padding: '48px 20px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dim",
    style: {
      fontWeight: 600
    }
  }, "Loading\u2026")) : st && !editing ?
  /*#__PURE__*/
  /* ---- standings ---- */
  React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "hero",
    style: {
      background: 'linear-gradient(135deg, var(--c-lime), var(--c-teal))'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "hero__in"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow",
    style: {
      color: 'rgba(255,255,255,.75)'
    }
  }, "Season points \xB7 rank"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: '3rem',
      fontWeight: 900,
      lineHeight: 1
    }
  }, st.score), /*#__PURE__*/React.createElement("p", {
    style: {
      marginTop: 6
    }
  }, /*#__PURE__*/React.createElement("b", null, "#", st.rank), " of ", st.field, " \xB7 ", st.pct, "th pct \xB7 field avg ", st.field_avg, ", best ", st.field_best))), /*#__PURE__*/React.createElement("p", {
    className: "muted mt-12 mb-12",
    style: {
      fontSize: '.84rem',
      lineHeight: 1.45
    }
  }, "Auto-lineup: ", st.lineup, ". No management \u2014 your draft is the whole game."), /*#__PURE__*/React.createElement("div", {
    className: "section-head mt-12"
  }, /*#__PURE__*/React.createElement("h2", null, "Your roster"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn--ghost btn--sm",
    onClick: reDraft
  }, "Re-draft")), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, (data.roster || []).map(p => /*#__PURE__*/React.createElement("div", {
    key: p.id,
    className: "prow"
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__id",
    onClick: () => onOpenPlayer && onOpenPlayer(p),
    style: {
      cursor: 'pointer'
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "contest__badge",
    style: {
      background: p.pos === 'P' ? 'var(--c-blue)' : 'var(--c-violet)'
    }
  }, (p.team || '?').slice(0, 2)), /*#__PURE__*/React.createElement("div", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__name"
  }, p.name), /*#__PURE__*/React.createElement("div", {
    className: "prow__sub"
  }, p.pos === 'P' ? 'Pitcher' : 'Hitter', " \xB7 ", p.team))))))) :
  /*#__PURE__*/
  /* ---- draft ---- */
  React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("p", {
    className: "muted mb-12",
    style: {
      fontSize: '.86rem',
      lineHeight: 1.45
    }
  }, "Draft ", slots.h, " hitters and ", slots.p, " pitchers covering every slot. Each slate your best in-position lineup \u2014 C, 1B, 2B, 3B, SS, OF, OF + best 2 pitchers \u2014 auto-scores, so draft depth at a spot and the hot bat there starts itself."), data.buyIn ? /*#__PURE__*/React.createElement("p", {
    className: "muted mb-12",
    style: {
      fontSize: '.82rem',
      fontWeight: 600
    }
  }, "Season buy-in ", S.money(data.buyIn), " \xB7 pays at season's end by final rank", data.entered ? ' · entered ✓' : '') : null, /*#__PURE__*/React.createElement("div", {
    className: "card card--pad mb-12",
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontWeight: 700,
      fontSize: '.9rem'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: nH === slots.h ? 'var(--live)' : 'var(--ink-2)'
    }
  }, "Hitters ", nH, "/", slots.h), /*#__PURE__*/React.createElement("span", {
    className: "dim"
  }, " \xB7 "), /*#__PURE__*/React.createElement("span", {
    style: {
      color: nP === slots.p ? 'var(--live)' : 'var(--ink-2)'
    }
  }, "Pitchers ", nP, "/", slots.p)), /*#__PURE__*/React.createElement("button", {
    className: "btn btn--brand btn--sm",
    disabled: !canLock || busy,
    onClick: lock
  }, "Lock roster")), /*#__PURE__*/React.createElement("div", {
    className: "chips mb-12",
    style: {
      display: 'flex',
      flexWrap: 'wrap',
      gap: 6
    }
  }, Object.entries(req).map(([p, n]) => {
    const ok = (haveByPos[p] || 0) >= n;
    return /*#__PURE__*/React.createElement("span", {
      key: p,
      style: {
        fontSize: '.76rem',
        fontWeight: 800,
        padding: '3px 9px',
        borderRadius: 12,
        background: ok ? 'var(--brand-soft)' : 'var(--card-2)',
        color: ok ? 'var(--brand-ink)' : 'var(--ink-3)',
        border: '1px solid var(--line)'
      }
    }, ok ? '✓ ' : '', p, n > 1 ? ` ×${n}` : '');
  })), sel.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "chips mb-12",
    style: {
      display: 'flex',
      flexWrap: 'wrap',
      gap: 6
    }
  }, sel.map(s => /*#__PURE__*/React.createElement("button", {
    key: s.id,
    className: "chip",
    onClick: () => setSel(sel.filter(x => x.id !== s.id)),
    style: {
      background: 'var(--surface-2)',
      border: '1px solid var(--line)',
      borderRadius: 14,
      padding: '4px 10px',
      fontSize: '.8rem',
      fontWeight: 700
    }
  }, s.name, " ", /*#__PURE__*/React.createElement("span", {
    className: "dim"
  }, s.pos), " \xD7"))), /*#__PURE__*/React.createElement("div", {
    className: "slate-tabs mb-12"
  }, /*#__PURE__*/React.createElement(Chip, {
    active: side === 'h',
    onClick: () => setSide('h')
  }, "Hitters"), /*#__PURE__*/React.createElement(Chip, {
    active: side === 'p',
    onClick: () => setSide('p')
  }, "Pitchers")), /*#__PURE__*/React.createElement("div", {
    className: "search mb-12"
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "search",
    size: 17
  }), /*#__PURE__*/React.createElement("input", {
    placeholder: 'Search ' + (side === 'p' ? 'pitchers' : 'hitters') + '…',
    value: q,
    onChange: e => setQ(e.target.value)
  })), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      overflow: 'hidden'
    }
  }, !pool && /*#__PURE__*/React.createElement("div", {
    className: "center muted",
    style: {
      padding: 24,
      fontWeight: 600
    }
  }, "Loading pool\u2026"), pool && shown.length === 0 && /*#__PURE__*/React.createElement("div", {
    className: "center muted",
    style: {
      padding: 24,
      fontWeight: 600
    }
  }, "No players found."), shown.slice(0, 120).map(p => /*#__PURE__*/React.createElement("div", {
    key: p.id,
    className: "prow"
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__id",
    onClick: () => onOpenPlayer && onOpenPlayer(p),
    style: {
      cursor: 'pointer'
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "contest__badge",
    style: {
      background: p.pos === 'P' ? 'var(--c-blue)' : 'var(--c-violet)'
    }
  }, (p.team || '?').slice(0, 2)), /*#__PURE__*/React.createElement("div", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "prow__name"
  }, p.name), /*#__PURE__*/React.createElement("div", {
    className: "prow__sub",
    style: {
      fontSize: '.74rem'
    }
  }, /*#__PURE__*/React.createElement("b", null, p.pos), " \xB7 ", p.line))), /*#__PURE__*/React.createElement("button", {
    className: "add-btn",
    disabled: selIds.has(p.id),
    title: "Draft",
    onClick: () => add(p)
  }, selIds.has(p.id) ? '✓' : '+'))))))));
}

/* ---------- ONBOARDING — pick your player ---------- */
function OnboardingScreen({
  personas,
  reset,
  onDone
}) {
  const S = window.SLATE;
  const [busy, setBusy] = useState(false);
  function pick(key) {
    if (busy) return;
    setBusy(true);
    fetch('/fantasy/api/onboard', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        persona: key,
        reset: !!reset
      })
    }).then(r => r.json()).then(j => {
      setBusy(false);
      if (!j.ok) {
        window.alert(j.error || 'Could not start.');
        return;
      }
      if (j.balance != null) S.WALLET = j.balance;
      onDone && onDone();
    }).catch(() => setBusy(false));
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "app__scroll"
  }, /*#__PURE__*/React.createElement("div", {
    className: "page page--narrow"
  }, /*#__PURE__*/React.createElement("div", {
    className: "hero",
    style: {
      background: 'linear-gradient(135deg, var(--c-violet), var(--c-coral))'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "hero__in"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow",
    style: {
      color: 'rgba(255,255,255,.75)'
    }
  }, reset ? 'New run' : 'Welcome to CapSpace'), /*#__PURE__*/React.createElement("h1", {
    className: "mt-12"
  }, "Pick your player."), /*#__PURE__*/React.createElement("p", null, "Your starting bankroll \u2014 and your whole personality. Lifetime winnings carry your status; it never resets, even when you bust. Choose your poison."))), /*#__PURE__*/React.createElement("div", {
    className: "fmt-grid mt-24"
  }, (personas || []).map(p => /*#__PURE__*/React.createElement("a", {
    key: p.key,
    className: "fmt",
    onClick: () => pick(p.key),
    style: {
      cursor: 'pointer'
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "fmt__glow",
    style: {
      background: 'var(--c-coral)'
    }
  }), /*#__PURE__*/React.createElement("div", {
    className: "fmt__name"
  }, p.name), /*#__PURE__*/React.createElement("div", {
    className: "fmt__desc"
  }, p.blurb), /*#__PURE__*/React.createElement("div", {
    className: "fmt__foot"
  }, /*#__PURE__*/React.createElement("span", {
    className: "fmt__stat"
  }, "Starting bankroll ", S.money(p.start * 100)))))), reset && /*#__PURE__*/React.createElement("p", {
    className: "center muted mt-16",
    style: {
      fontWeight: 600,
      fontSize: '.82rem'
    }
  }, "A new run wipes your current bankroll, bets, entries and records.")));
}
Object.assign(window, {
  HubScreen,
  LobbyScreen,
  EntriesScreen,
  FormatTeaser,
  StreakScreen,
  SluggersScreen,
  PilotsScreen,
  CategoriesScreen,
  SportsbookScreen,
  BestBallScreen,
  OnboardingScreen
});
