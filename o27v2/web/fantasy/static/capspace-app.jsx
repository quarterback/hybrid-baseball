/* ============================================================
   CapSpace — root App + navigation wiring.
   Loaded last; reads window.SLATE (real data injected by the
   blueprint into window.__CAPSPACE_DATA__, else bundled mock).
   ============================================================ */
const { useState, useEffect } = React;
const S = window.SLATE;
const CurrencyCtx = window.CurrencyCtx;
const VALID_MODES = ['guilder', 'usd', 'eur', 'zora'];

const EMPTY_ROSTER = Object.fromEntries(S.SLOTS.map(s => [s.key, null]));

function App() {
  const [view, setView] = useState('hub');
  const [contest, setContest] = useState(null);
  const [roster, setRoster] = useState(EMPTY_ROSTER);
  const [drawer, setDrawer] = useState({ open: false, player: null });
  const [teaser, setTeaser] = useState(null);
  const [cur, setCur] = useState(() => {
    // USD is CapSpace's default — only the engine's stored preference overrides it.
    try { const v = localStorage.getItem('o27.currencyDisplay'); return VALID_MODES.includes(v) ? v : 'usd'; }
    catch (e) { return 'usd'; }
  });

  // make the chosen mode visible to the global money() formatter for this render
  S.mode = cur;

  function setMode(m) {
    S.mode = m;
    setCur(m);
    try { localStorage.setItem('o27.currencyDisplay', m); } catch (e) {}
  }
  // sync if the main O27 app (or another tab) changes the preference
  useEffect(() => {
    const onStorage = e => {
      if (e.key === 'o27.currencyDisplay' && VALID_MODES.includes(e.newValue)) { S.mode = e.newValue; setCur(e.newValue); }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  function nav(v) { setView(v); window.scrollTo(0, 0); document.querySelector('.app__scroll')?.scrollTo(0, 0); }

  function addPlayer(p) {
    setRoster(prev => {
      // already in lineup?
      if (Object.values(prev).some(x => x && x.id === p.id)) return prev;
      const slot = S.SLOTS.find(s => prev[s.key] === null && s.accepts.includes(p.pos));
      if (!slot) return prev;
      return { ...prev, [slot.key]: p };
    });
  }
  function removeSlot(key) { setRoster(prev => ({ ...prev, [key]: null })); }
  function openPlayer(p) { setDrawer({ open: true, player: p }); }
  function enterContest(c) { setContest(c); nav('builder'); }
  function openFormat(f) { setTeaser(f); }

  const inLineup = drawer.player && Object.values(roster).some(x => x && x.id === drawer.player.id);

  return (
    <CurrencyCtx.Provider value={{ mode: cur, setMode }}>
    <AppShell view={view} onNav={nav} onEnter={() => contest ? nav('builder') : nav('lobby')}>
      {view === 'hub' && <HubScreen onNav={nav} onOpenFormat={openFormat} />}
      {view === 'lobby' && <LobbyScreen onNav={nav} onEnterContest={enterContest} />}
      {view === 'builder' && <BuilderScreen contest={contest} roster={roster} onAdd={addPlayer} onRemove={removeSlot} onOpenPlayer={openPlayer} onEnter={() => nav('live')} onNav={nav} />}
      {view === 'live' && <LiveScreen roster={roster} onNav={nav} onOpenPlayer={openPlayer} />}
      {view === 'entries' && <EntriesScreen onNav={nav} />}

      <PlayerDrawer player={drawer.player} open={drawer.open} onClose={() => setDrawer(d => ({ ...d, open: false }))}
        onAdd={view === 'builder' ? addPlayer : null} inLineup={inLineup} />
      <FormatTeaser fmt={teaser} onClose={() => setTeaser(null)} onNav={nav} />
    </AppShell>
    </CurrencyCtx.Provider>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
