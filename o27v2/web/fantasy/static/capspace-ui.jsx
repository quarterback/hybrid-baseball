/* ============================================================
   SLATE — UI primitives + responsive app shell
   Exposes components on window for the screen + root files.
   ============================================================ */
const { useState, useEffect, useRef } = React;

/* shared currency context (mode + setter), put on window for cross-file use */
const CurrencyCtx = React.createContext({ mode: 'usd', setMode: () => {} });
window.CurrencyCtx = CurrencyCtx;

/* ---- icon set (simple stroke icons) ---------------------------------- */
const PATHS = {
  home:    'M3 11.5 12 4l9 7.5M5 10v9h5v-6h4v6h5v-9',
  play:    'M12 3 21 12 12 21 3 12z',         // diamond
  ticket:  'M4 7h16v3a2 2 0 0 0 0 4v3H4v-3a2 2 0 0 0 0-4z M9 7v10',
  live:    'M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0 M6.3 6.3a8 8 0 0 0 0 11.4 M17.7 6.3a8 8 0 0 1 0 11.4',
  user:    'M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0 M4 21c0-4 4-6 8-6s8 2 8 6',
  search:  'M11 11m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0 M20 20l-4-4',
  plus:    'M12 5v14M5 12h14',
  x:       'M6 6l12 12M18 6 6 18',
  chev:    'M9 6l6 6-6 6',
  trophy:  'M7 4h10v4a5 5 0 0 1-10 0z M5 5H3v2a3 3 0 0 0 3 3 M19 5h2v2a3 3 0 0 1-3 3 M9 14h6 M10 14v4 M14 14v4 M8 20h8',
  clock:   'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0 M12 7v5l3 2',
  filter:  'M3 5h18l-7 8v5l-4 2v-7z',
  lock:    'M6 11h12v9H6z M9 11V8a3 3 0 0 1 6 0v3',
  bolt:    'M13 3 4 14h6l-1 7 9-11h-6z',
  diamond: 'M12 3 21 12 12 21 3 12z M12 8 16 12 12 16 8 12z',
  rings:   'M9 12m-5 0a5 5 0 1 0 10 0a5 5 0 1 0-10 0 M15 12m-5 0a5 5 0 1 0 10 0a5 5 0 1 0-10 0',
  anchor:  'M12 7m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0 M12 9v11 M6 13a6 6 0 0 0 12 0 M4 13h4 M16 13h4',
  flag:    'M6 21V4 M6 4h11l-2 4 2 4H6',
  wave:    'M3 9c2 0 2 2 4.5 2S10 9 12 9s2 2 4.5 2S19 9 21 9 M3 15c2 0 2 2 4.5 2S10 15 12 15s2 2 4.5 2S19 15 21 15',
  flame:   'M12 3c1 4 5 5 5 9a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3 1 0 1-2-1-8z',
  spark:   'M12 3v6 M12 15v6 M3 12h6 M15 12h6 M6 6l3 3 M15 15l3 3 M18 6l-3 3 M9 15l-3 3',
  star:    'M12 4l2.4 5 5.6.6-4 4 1 5.4-5-2.8-5 2.8 1-5.4-4-4 5.6-.6z',
  info:    'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0 M12 8h.01 M11 12h1v4h1',
  back:    'M15 6l-6 6 6 6',
  wallet:  'M3 7h15v10H3z M3 7l12-3v3 M17 11h4v3h-4z',
  bell:    'M6 16V10a6 6 0 0 1 12 0v6l2 2H4z M10 20a2 2 0 0 0 4 0',
  coin:    'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0 M12 12m-4.5 0a4.5 4.5 0 1 0 9 0a4.5 4.5 0 1 0-9 0',
};
function Icon({ name, size = 22, fill = false, stroke = 2, style }) {
  const d = PATHS[name] || '';
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none"
      stroke="currentColor" strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" style={style}>
      {d.split(' M').map((seg, i) => <path key={i} d={(i ? 'M' : '') + seg} />)}
    </svg>
  );
}

/* ---- CapSpace astronaut mascot + Web 2.0 beta seal ------------------- */
function SpaceMascot({ size = 30 }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true">
      <defs>
        <radialGradient id="csHelmet" cx="36%" cy="28%" r="82%">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="68%" stopColor="#eef1f9" />
          <stop offset="100%" stopColor="#cdd5e8" />
        </radialGradient>
        <linearGradient id="csVisor" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#374466" />
          <stop offset="55%" stopColor="#1b2540" />
          <stop offset="100%" stopColor="#0c1322" />
        </linearGradient>
        <radialGradient id="csAnt" cx="42%" cy="38%" r="65%">
          <stop offset="0%" stopColor="#ffe7a6" />
          <stop offset="55%" stopColor="#ffb020" />
          <stop offset="100%" stopColor="#e8910c" />
        </radialGradient>
      </defs>
      {/* antenna */}
      <line x1="32" y1="14" x2="32" y2="7" stroke="#c4cbde" strokeWidth="2.4" strokeLinecap="round" />
      <circle cx="32" cy="5.4" r="3.4" fill="url(#csAnt)" />
      {/* side comm pods */}
      <rect x="5" y="29" width="8" height="13" rx="3.6" fill="#e4e8f3" stroke="#c0c8db" strokeWidth="1" />
      <rect x="51" y="29" width="8" height="13" rx="3.6" fill="#e4e8f3" stroke="#c0c8db" strokeWidth="1" />
      {/* helmet sphere */}
      <circle cx="32" cy="35" r="21.5" fill="url(#csHelmet)" stroke="#c0c8db" strokeWidth="1" />
      {/* visor */}
      <rect x="15.5" y="24" width="33" height="22.5" rx="11.25" fill="url(#csVisor)" stroke="#0a1120" strokeWidth="1" />
      <ellipse cx="26" cy="30" rx="10" ry="5.2" fill="#ffffff" opacity=".16" transform="rotate(-18 26 30)" />
      {/* friendly eyes + smile */}
      <circle cx="26.5" cy="35.6" r="3.1" fill="#c2e2ff" />
      <circle cx="37.5" cy="35.6" r="3.1" fill="#c2e2ff" />
      <circle cx="25.6" cy="34.6" r="1.1" fill="#ffffff" />
      <circle cx="36.6" cy="34.6" r="1.1" fill="#ffffff" />
      <path d="M27 41 Q32 44.6 37 41" fill="none" stroke="#c2e2ff" strokeWidth="2" strokeLinecap="round" />
      {/* glossy helmet highlight */}
      <ellipse cx="24.5" cy="20" rx="11" ry="5" fill="#ffffff" opacity=".55" />
    </svg>
  );
}

function BetaSeal({ size = 30, label = 'BETA' }) {
  const n = 12, cx = 50, cy = 50, ro = 49, ri = 40, pts = [];
  for (let i = 0; i < n * 2; i++) {
    const ang = (Math.PI / n) * i - Math.PI / 2;
    const r = i % 2 === 0 ? ro : ri;
    pts.push((cx + r * Math.cos(ang)).toFixed(1) + ',' + (cy + r * Math.sin(ang)).toFixed(1));
  }
  return (
    <svg className="beta-seal" viewBox="0 0 100 100" width={size} height={size} aria-label="Beta">
      <defs>
        <linearGradient id="csBeta" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ffd277" />
          <stop offset="55%" stopColor="#ffb020" />
          <stop offset="100%" stopColor="#e8910c" />
        </linearGradient>
      </defs>
      <polygon points={pts.join(' ')} fill="url(#csBeta)" stroke="#fff" strokeWidth="3.5" strokeLinejoin="round" />
      <ellipse cx="42" cy="32" rx="23" ry="11" fill="#fff" opacity=".32" />
      <text x="50" y="51" textAnchor="middle" dominantBaseline="central" fontFamily="Outfit, sans-serif"
        fontWeight="800" fontSize="25" fill="#fff" style={{ letterSpacing: '-.5px' }}>{label}</text>
    </svg>
  );
}

/* ---- tiny atoms ------------------------------------------------------- */
function Btn({ variant = '', size = '', block, children, className = '', ...rest }) {
  const cls = ['btn', variant && 'btn--' + variant, size && 'btn--' + size, block && 'btn--block', className]
    .filter(Boolean).join(' ');
  return <button className={cls} {...rest}>{children}</button>;
}
function Tag({ kind, children }) { return <span className={'tag tag--' + kind}>{children}</span>; }
function Chip({ active, brand, children, ...rest }) {
  return <button className={['chip', active && 'chip--active', brand && 'chip--brand'].filter(Boolean).join(' ')} {...rest}>{children}</button>;
}

function PlayerMark({ p, size = 40 }) {
  return (
    <span className="pmark" style={{ width: size, height: size, background: p.teamColor }}>{p.init}</span>
  );
}

/* sparkline of recent fantasy points */
function Spark({ form, color = 'var(--brand)' }) {
  const max = Math.max(...form, 1);
  return (
    <span className="spark">
      {form.map((v, i) => (
        <i key={i} style={{ height: Math.max(3, (v / max) * 26), background: i === form.length - 1 ? color : 'var(--line-2)' }} />
      ))}
    </span>
  );
}

/* ---- currency selector (global, matches engine modes) ---------------- */
function CurrencySelector({ compact }) {
  const S = window.SLATE;
  const { mode, setMode } = React.useContext(CurrencyCtx);
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('pointerdown', onDoc);
    return () => document.removeEventListener('pointerdown', onDoc);
  }, [open]);
  const cur = S.CURRENCIES.find(c => c.mode === mode) || S.CURRENCIES[0];
  return (
    <div className="cursel" ref={ref}>
      <button className="cursel__btn" onClick={() => setOpen(o => !o)} aria-haspopup="listbox" aria-expanded={open}
        title="Display currency">
        <span className="cursel__sym">{cur.symbol}</span>
        {!compact && <span className="cursel__name">{cur.code}</span>}
        <Icon name="chev" size={14} style={{ transform: open ? 'rotate(90deg)' : 'rotate(90deg)', opacity: .6 }} />
      </button>
      {open && (
        <div className="cursel__menu" role="listbox">
          <div className="cursel__head">Display currency</div>
          {S.CURRENCIES.map(c => (
            <button key={c.mode} role="option" aria-selected={c.mode === mode}
              className={'cursel__opt' + (c.mode === mode ? ' is-on' : '')}
              onClick={() => { setMode(c.mode); setOpen(false); }}>
              <span className="cursel__optsym">{c.symbol}</span>
              <span className="cursel__optname">{c.name}<small>{c.sub}</small></span>
              {c.mode === mode && <Icon name="chev" size={15} style={{ color: 'var(--brand)' }} />}
            </button>
          ))}
          <div className="cursel__foot">Syncs with your O27 league preference</div>
        </div>
      )}
    </div>
  );
}

/* ---- app shell -------------------------------------------------------- */
function AppShell({ view, onNav, walletOpen, children, onEnter }) {
  const S = window.SLATE;
  const nav = [
    { id: 'hub',     label: 'Home',      icon: 'home' },
    { id: 'lobby',   label: 'Lobby',     icon: 'play' },
    { id: 'entries', label: 'My Entries',icon: 'ticket' },
    { id: 'live',    label: 'Live',      icon: 'live', dot: true },
  ];
  return (
    <div className="app">
      {/* desktop sidebar */}
      <aside className="sidebar">
        <div className="brand" onClick={() => onNav('hub')} style={{ cursor: 'pointer' }}>
          <span className="brand__mark brand__mark--web2"><SpaceMascot size={30} /></span>
          <div>
            <div className="brand__word"><span className="cs-cap">Cap</span><span className="cs-space">Space</span></div>
            <div className="brand__sub">O27 Fantasy <span className="brand__beta"><BetaSeal size={26} /></span></div>
          </div>
        </div>
        {nav.map(n => (
          <a key={n.id} className={'navitem' + (view === n.id ? ' navitem--active' : '')} onClick={() => onNav(n.id)}>
            <Icon name={n.icon} size={21} />
            {n.label}
            {n.dot && view !== n.id && <span className="badge-dot" />}
          </a>
        ))}
        <div className="sidebar__foot">
          <div className="wallet">
            <div className="wallet__label">Your balance</div>
            <div className="wallet__amt">{window.SLATE.money(window.SLATE.WALLET)}</div>
            <div className="wallet__row">
              <Btn variant="brand" size="sm">Deposit</Btn>
              <Btn variant="ghost" size="sm" style={{ color: '#fff', borderColor: 'rgba(255,255,255,.25)' }}>History</Btn>
            </div>
          </div>
        </div>
      </aside>

      {/* main column */}
      <div className="app__main">
        {children}
      </div>

      {/* mobile bottom tabs */}
      <nav className="tabbar">
        <a className={'tab' + (view === 'hub' ? ' tab--active' : '')} onClick={() => onNav('hub')}>
          <Icon name="home" size={23} /> Home
        </a>
        <a className={'tab' + (view === 'lobby' ? ' tab--active' : '')} onClick={() => onNav('lobby')}>
          <Icon name="play" size={23} /> Lobby
        </a>
        <a className="tab tab--fab" onClick={() => onEnter ? onEnter() : onNav('lobby')}>
          <span className="tab__fab"><Icon name="plus" size={26} stroke={2.6} /></span>
        </a>
        <a className={'tab' + (view === 'live' ? ' tab--active' : '')} onClick={() => onNav('live')}>
          <Icon name="live" size={23} /> Live
        </a>
        <a className={'tab' + (view === 'entries' ? ' tab--active' : '')} onClick={() => onNav('entries')}>
          <Icon name="ticket" size={23} /> Entries
        </a>
      </nav>
    </div>
  );
}

/* top bar with sim clock + avatar */
function TopBar({ title, sub, back, onBack, right }) {
  return (
    <header className="topbar">
      {back && (
        <button className="btn btn--ghost btn--sm" onClick={onBack} style={{ padding: '8px 12px' }} aria-label="Back">
          <Icon name="back" size={18} />
        </button>
      )}
      <div className="col" style={{ minWidth: 0, flex: 1 }}>
        <div className="topbar__title">{title}</div>
        {sub && <div className="topbar__sub">{sub}</div>}
      </div>
      {right}
      {(window.SLATE && window.SLATE.SIM_DAY) && (
        <span className="simclock" title="Current sim date — tonight's live slate">
          <span className="dot" />
          <span className="simclock__lbl hide-mobile">Slate&nbsp;</span>
          <span className="num">{window.SLATE.SIM_DAY}</span>
        </span>
      )}
      {window.SLATE && <span className="topbar__bal" title="Your bankroll"><span className="topbar__bal-lbl">Balance</span><span className="num">{window.SLATE.money(window.SLATE.WALLET)}</span></span>}
      <CurrencySelector />
      <span className="avatar">Y</span>
    </header>
  );
}

/* ---------- HOW IT WORKS — collapsible per-mode instructions ---------- */
function HowTo({ k }) {
  const d = ((window.SLATE && window.SLATE.HOWTO) || {})[k];
  const sk = 'o27.capspace.howto.' + k;
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(sk) !== '0'; } catch (e) { return true; } });
  if (!d) return null;
  function toggle() { const n = !open; setOpen(n); try { localStorage.setItem(sk, n ? '1' : '0'); } catch (e) {} }
  return (
    <div className="card mb-12" style={{ overflow: 'hidden' }}>
      <button onClick={toggle} style={{ display: 'flex', width: '100%', justifyContent: 'space-between', alignItems: 'center', background: 'none', border: 0, padding: '12px 14px', cursor: 'pointer', font: 'inherit', color: 'inherit' }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 800 }}><Icon name="info" size={16} /> How it works</span>
        <Icon name="chev" size={16} style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .15s', opacity: .55 }} />
      </button>
      {open && (
        <div style={{ padding: '0 14px 14px' }}>
          {d.tagline && <p className="muted" style={{ margin: '0 0 8px', fontWeight: 700, fontSize: '.85rem' }}>{d.tagline}</p>}
          <ol style={{ margin: 0, paddingLeft: 18, display: 'grid', gap: 6 }}>
            {d.steps.map((s, i) => <li key={i} style={{ fontSize: '.85rem', lineHeight: 1.45 }}>{s}</li>)}
          </ol>
        </div>
      )}
    </div>
  );
}

/* ---------- POSITION FILTER — tappable position chips ---------- */
function PosFilter({ value, onChange, positions }) {
  const ps = positions || ['C', '1B', '2B', '3B', 'SS', 'OF'];
  return (
    <div className="chips mb-12" style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
      <span className="dim" style={{ fontSize: '.74rem', fontWeight: 700 }}>Filter:</span>
      {ps.map(p => {
        const on = value === p;
        return <button key={p} onClick={() => onChange(on ? null : p)} style={{ fontSize: '.76rem', fontWeight: 800, padding: '4px 10px', borderRadius: 12, cursor: 'pointer', background: on ? 'var(--ink)' : 'var(--card-2)', color: on ? '#fff' : 'var(--ink-3)', border: '1px solid var(--line)' }}>{p}</button>;
      })}
      {value && <button onClick={() => onChange(null)} style={{ fontSize: '.74rem', fontWeight: 700, padding: '4px 8px', borderRadius: 12, cursor: 'pointer', background: 'none', border: 0, color: 'var(--brand)' }}>Clear</button>}
    </div>
  );
}

/* ---------- RECENT LIST — collapsible history (shows N, expands rest) ---------- */
function shortDate(s) {
  if (!s) return '—';
  const parts = String(s).split('-');
  if (parts.length < 3) return s;
  const mo = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][(+parts[1]) - 1];
  return mo ? mo + ' ' + (+parts[2]) : s;
}

function RecentList({ title, meta, items, limit = 5, renderRow }) {
  const [open, setOpen] = useState(false);
  if (!items || !items.length) return null;
  const shown = open ? items : items.slice(0, limit);
  const extra = items.length - limit;
  return (
    <>
      <div className="section-head mt-24"><h2>{title}</h2>{meta}</div>
      <div className="card" style={{ overflow: 'hidden' }}>
        {shown.map(renderRow)}
        {extra > 0 && (
          <button className="recent__more" onClick={() => setOpen(o => !o)}>
            {open ? 'Show less' : 'Show ' + extra + ' more'}
            <Icon name="chev" size={15} style={{ transform: open ? 'rotate(-90deg)' : 'rotate(90deg)', transition: 'transform .15s' }} />
          </button>
        )}
      </div>
    </>
  );
}

/* ---------- PAGED LIST — paginate long pools (no endless scroll) ---------- */
function PagedList({ items, perPage = 25, resetKey, renderRow, empty }) {
  const [page, setPage] = useState(0);
  useEffect(() => { setPage(0); }, [resetKey]);
  if (!items || items.length === 0) return empty || null;
  const pages = Math.max(1, Math.ceil(items.length / perPage));
  const p = Math.min(page, pages - 1);
  const slice = items.slice(p * perPage, p * perPage + perPage);
  return (
    <>
      {slice.map(renderRow)}
      {pages > 1 && (
        <div className="pager">
          <button className="pager__btn" disabled={p <= 0} onClick={() => setPage(p - 1)} aria-label="Previous page"><Icon name="back" size={16} /> Prev</button>
          <span className="pager__info">Page <b>{p + 1}</b> / {pages} · {items.length} players</span>
          <button className="pager__btn" disabled={p >= pages - 1} onClick={() => setPage(p + 1)} aria-label="Next page">Next <Icon name="chev" size={16} /></button>
        </div>
      )}
    </>
  );
}

/* eligible-position label, e.g. "SS/2B" or "1B/OF+2" for super-utility */
function posLabel(p) {
  const e = (p.posEligible && p.posEligible.length) ? p.posEligible : [p.pos];
  if (e.length <= 2) return e.join('/');
  return e.slice(0, 2).join('/') + '+' + (e.length - 2);
}

Object.assign(window, { Icon, Btn, Tag, Chip, PlayerMark, Spark, AppShell, TopBar, CurrencySelector, CurrencyCtx, SpaceMascot, BetaSeal, HowTo, PosFilter, RecentList, shortDate, PagedList, posLabel });
