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
  const [liveContestId, setLiveContestId] = useState(null);
  const [cur, setCur] = useState(() => {
    // CapSpace defaults to USD and keeps its OWN currency preference, so it
    // never inherits the engine's canonical-guilder default.
    try { const v = localStorage.getItem('o27.capspace.currency'); return VALID_MODES.includes(v) ? v : 'usd'; }
    catch (e) { return 'usd'; }
  });

  // make the chosen mode visible to the global money() formatter for this render
  S.mode = cur;

  function setMode(m) {
    S.mode = m;
    setCur(m);
    try { localStorage.setItem('o27.capspace.currency', m); } catch (e) {}
  }
  // sync if another CapSpace tab changes the preference
  useEffect(() => {
    const onStorage = e => {
      if (e.key === 'o27.capspace.currency' && VALID_MODES.includes(e.newValue)) { S.mode = e.newValue; setCur(e.newValue); }
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

  // Submit the built lineup to the live save, then jump to the live board.
  // Falls back to a contest-less live view if there's no real contest id
  // (e.g. the bundled mock data has no server to post to).
  function submitLineup() {
    const ids = S.SLOTS.map(s => roster[s.key]).filter(Boolean).map(p => p.id);
    const cid = contest && typeof contest.id === 'number' ? contest.id : null;
    if (cid == null) { setLiveContestId(null); nav('live'); return; }
    fetch('/fantasy/api/enter', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contest_id: cid, player_ids: ids }),
    }).then(r => r.json()).then(j => {
      if (j && j.ok) { if (j.balance != null) S.WALLET = j.balance; setLiveContestId(cid); nav('live'); }
      else { window.alert((j && j.error) || 'Could not enter this lineup.'); }
    }).catch(() => { setLiveContestId(cid); nav('live'); });
  }

  const inLineup = drawer.player && Object.values(roster).some(x => x && x.id === drawer.player.id);

  return (
    <CurrencyCtx.Provider value={{ mode: cur, setMode }}>
    <AppShell view={view} onNav={nav} onEnter={() => contest ? nav('builder') : nav('lobby')}>
      {view === 'hub' && <HubScreen onNav={nav} onOpenFormat={openFormat} />}
      {view === 'lobby' && <LobbyScreen onNav={nav} onEnterContest={enterContest} />}
      {view === 'builder' && <BuilderScreen contest={contest} roster={roster} onAdd={addPlayer} onRemove={removeSlot} onOpenPlayer={openPlayer} onEnter={submitLineup} onNav={nav} />}
      {view === 'live' && <LiveScreen roster={roster} contestId={liveContestId} onNav={nav} onOpenPlayer={openPlayer} />}
      {view === 'entries' && <EntriesScreen onNav={nav} onOpenContest={(cid)=>{ setLiveContestId(cid); nav('live'); }} />}
      {view === 'streak' && <StreakScreen onNav={nav} onOpenPlayer={openPlayer} />}
      {view === 'sluggers' && <SluggersScreen onNav={nav} onOpenPlayer={openPlayer} />}
      {view === 'pilots' && <PilotsScreen onNav={nav} onOpenPlayer={openPlayer} />}
      {view === 'categories' && <CategoriesScreen onNav={nav} onOpenPlayer={openPlayer} />}
      {view === 'sportsbook' && <SportsbookScreen onNav={nav} />}
      {view === 'bestball' && <BestBallScreen onNav={nav} onOpenPlayer={openPlayer} />}

      <PlayerDrawer player={drawer.player} open={drawer.open} onClose={() => setDrawer(d => ({ ...d, open: false }))}
        onAdd={view === 'builder' ? addPlayer : null} inLineup={inLineup} />
      <FormatTeaser fmt={teaser} onClose={() => setTeaser(null)} onNav={nav} />
    </AppShell>
    </CurrencyCtx.Provider>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
