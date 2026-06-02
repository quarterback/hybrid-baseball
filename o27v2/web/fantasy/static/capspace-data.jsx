/* ============================================================
   SLATE — mock O27 data layer
   All fictional. Mirrors the real substrate described in the brief:
   per-game stat lines, 20-80 ratings, trade-value→salary, ƒ guilder
   economy with lakh/crore numbering, daily slates from game_date.
   Exposes window.SLATE.
   ============================================================ */
(function () {
  const LAKH = 100000;
  const CRORE = 100 * LAKH;
  // Salary cap in stored guilders. CapSpace runs on small DOLLAR figures
  // (ƒ100 = $1), so ƒ1,00,000 displays as a friendly "$1,000" cap.
  const CAP = 1_00_000;

  // Real save data injected by the blueprint (null ⇒ use bundled mock).
  const DATA = (typeof window !== 'undefined' && window.__CAPSPACE_DATA__) || null;

  // ---- currency engine (mirrors o27v2/currency.py) --------------------
  // All stored amounts are integer GUILDERS; display mode converts on the fly.
  // Rates come from the engine snapshot (window.O27_RATES) when present so the
  // toggle stays authoritative, else fall back to the engine's constants.
  const _R = (typeof window !== 'undefined' && window.O27_RATES) || {};
  const RATES = {
    guilderPerUsd: _R.guilderPerUsd || 100,
    guilderPerEur: _R.guilderPerEur || 108,
    guilderPerZora: _R.guilderPerZora || 1350,
  };
  const ZORA_SYMBOL = _R.zoraSymbol || '₳';

  const CURRENCIES = [
    { mode: 'guilder', symbol: 'ƒ', name: 'Guilder',     code: 'GLD', sub: 'Canonical · lakh / crore' },
    { mode: 'usd',     symbol: '$', name: 'US Dollar',   code: 'USD', sub: 'ƒ100 = $1' },
    { mode: 'eur',     symbol: '€', name: 'Euro',        code: 'EUR', sub: '€1 ≈ ƒ108' },
    { mode: 'zora',    symbol: '₳', name: 'Zaryan Zora', code: 'ZRZ', sub: '1 ₳ ≈ $13.50 · strong' },
  ];

  function _guilderShort(n) {
    if (n >= CRORE) return 'ƒ' + (n / CRORE).toFixed(2).replace(/\.?0+$/, '') + 'Cr';
    if (n >= LAKH)  return 'ƒ' + (n / LAKH).toFixed(1).replace(/\.0$/, '') + 'L';
    return 'ƒ' + n.toLocaleString('en-IN');
  }
  function _western(a, sym) {
    const s = a < 0 ? '-' : ''; a = Math.abs(a);
    if (a >= 1e9) return s + sym + (a / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
    if (a >= 1e6) return s + sym + (a / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    if (a >= 1e4) return s + sym + Math.round(a / 1e3) + 'K';
    return s + sym + Math.round(a).toLocaleString('en-US');
  }
  function _zora(g) {
    const z = g / RATES.guilderPerZora;
    if (z > 0 && z < 1) { const l = Math.round(z * 100); return l <= 0 ? ZORA_SYMBOL + '0' : (l === 1 ? '1 luch' : l + ' luchi'); }
    return ZORA_SYMBOL + Math.round(z).toLocaleString('en-US');
  }
  // mode-aware money formatter — reads the live global display mode
  function money(g) {
    g = Math.round(g);
    const mode = (window.SLATE && window.SLATE.mode) || 'usd';
    if (mode === 'usd')  return _western(g / RATES.guilderPerUsd, '$');
    if (mode === 'eur')  return _western(g / RATES.guilderPerEur, '€');
    if (mode === 'zora') return _zora(g);
    return _guilderShort(g);
  }

  const WALLET = 3.42 * CRORE; // player's guilder balance

  const TEAMS = (DATA && DATA.TEAMS) || {
    TID: { name: 'Tidewater Mariners',  color: 'var(--c-teal)' },
    ANC: { name: 'Saltmarsh Anchors',   color: 'var(--c-blue)' },
    GAL: { name: 'Highgate Galleons',   color: 'var(--c-violet)' },
    KEE: { name: 'Keelhaul Brigands',   color: 'var(--c-coral)' },
    MST: { name: 'Mistral Reapers',     color: 'var(--c-green)' },
    FTH: { name: 'Fathom Leviathans',   color: 'var(--c-pink)' },
    REE: { name: 'Coral Reef Wreckers', color: 'var(--c-lime)' },
    DCK: { name: 'Drydock Riveters',    color: 'var(--c-amber)' },
  };

  // today's slate of sim games (real when injected, else 4 mock games)
  const SLATE_GAMES = (DATA && DATA.SLATE_GAMES) || [
    { away: 'KEE', home: 'TID', time: '6:05' },
    { away: 'GAL', home: 'ANC', time: '6:05' },
    { away: 'FTH', home: 'MST', time: '7:40' },
    { away: 'REE', home: 'DCK', time: '8:10' },
  ];

  function initials(name) {
    const p = name.split(' ');
    return (p[0][0] + (p[p.length - 1][0] || '')).toUpperCase();
  }

  // deterministic pseudo-random from a seed string
  function seeded(seed) {
    let h = 2166136261;
    for (let i = 0; i < seed.length; i++) { h ^= seed.charCodeAt(i); h = Math.imul(h, 16777619); }
    return () => { h += 0x6D2B79F5; let t = h; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }

  // build a 5-game log + form sparkline from the player's projection
  function buildLog(p) {
    const rnd = seeded(p.id);
    const opps = Object.keys(TEAMS).filter(t => t !== p.team);
    const log = [];
    const form = [];
    for (let i = 0; i < 6; i++) {
      const swing = 0.45 + rnd() * 1.25;
      const fp = Math.max(0, Math.round(p.proj * swing * 10) / 10);
      form.push(fp);
      if (i < 5) {
        const opp = opps[Math.floor(rnd() * opps.length)];
        if (p.isPitcher) {
          log.push({
            date: `J${10 + i}`, opp,
            line: `${(4 + Math.floor(rnd() * 5))}.${Math.floor(rnd()*3)} arc · ${Math.floor(rnd()*9+3)}K`,
            fp,
          });
        } else {
          const h = Math.floor(rnd() * 4), hr = rnd() > .78 ? 1 : 0, st = Math.floor(rnd() * 3), rad = ['B', 'B+', 'A', 'A+', 'C'][Math.floor(rnd()*5)];
          log.push({
            date: `J${10 + i}`, opp,
            line: `${h}-${4 + Math.floor(rnd()*2)}${hr ? ` · ${hr}HR` : ''} · ${st} stay · RAD ${rad}`,
            fp,
          });
        }
      }
    }
    return { log, form: form.reverse() };
  }

  // ---- players ----------------------------------------------------------
  // r = ratings 20-80. Hitters: contact, power, eye, stay, speed, field.
  // Pilots: command, stuff, decay (fatigue resist), control, late (arc-3).
  const RAW = (DATA && DATA.PLAYERS) || [
    // pilots
    { id: 'p01', name: 'Calder Voss',     team: 'TID', pos: 'PILOT', salary: 12.6*LAKH, proj: 41.2, own: 28, r: { command: 70, stuff: 66, decay: 62, control: 58, late: 71 } },
    { id: 'p02', name: 'Niko Brandt',     team: 'ANC', pos: 'PILOT', salary: 11.8*LAKH, proj: 38.4, own: 22, r: { command: 64, stuff: 71, decay: 55, control: 60, late: 58 } },
    { id: 'p03', name: 'Émile Thorne',    team: 'MST', pos: 'PILOT', salary: 10.2*LAKH, proj: 34.0, own: 14, r: { command: 60, stuff: 58, decay: 68, control: 66, late: 62 } },
    { id: 'p04', name: 'Dev Okafor',      team: 'REE', pos: 'PILOT', salary: 8.9*LAKH,  proj: 29.6, own: 9,  r: { command: 55, stuff: 62, decay: 50, control: 54, late: 48 } },
    // catchers
    { id: 'h01', name: 'Marek Dolan',     team: 'GAL', pos: 'C',  salary: 9.4*LAKH, proj: 19.8, own: 24, r: { contact: 62, power: 58, eye: 66, stay: 55, speed: 35, field: 70 } },
    { id: 'h02', name: 'Rui Castellan',   team: 'KEE', pos: 'C',  salary: 7.1*LAKH, proj: 14.6, own: 11, r: { contact: 55, power: 50, eye: 60, stay: 48, speed: 40, field: 64 } },
    // 1B
    { id: 'h03', name: 'Bo Halverson',    team: 'DCK', pos: '1B', salary: 12.2*LAKH, proj: 24.5, own: 34, r: { contact: 60, power: 76, eye: 58, stay: 44, speed: 30, field: 52 } },
    { id: 'h04', name: 'Teodor Vale',     team: 'ANC', pos: '1B', salary: 9.0*LAKH,  proj: 18.2, own: 17, r: { contact: 64, power: 62, eye: 62, stay: 58, speed: 38, field: 56 } },
    // 2B
    { id: 'h05', name: 'Kasper Lund',     team: 'TID', pos: '2B', salary: 10.6*LAKH, proj: 21.9, own: 29, r: { contact: 70, power: 48, eye: 64, stay: 72, speed: 66, field: 68 } },
    { id: 'h06', name: 'Idris Pell',      team: 'FTH', pos: '2B', salary: 7.8*LAKH,  proj: 15.9, own: 13, r: { contact: 62, power: 44, eye: 58, stay: 64, speed: 70, field: 62 } },
    // 3B
    { id: 'h07', name: 'Salim Arora',     team: 'MST', pos: '3B', salary: 11.4*LAKH, proj: 23.1, own: 26, r: { contact: 58, power: 72, eye: 60, stay: 50, speed: 44, field: 60 } },
    { id: 'h08', name: 'Wim Daele',       team: 'GAL', pos: '3B', salary: 8.3*LAKH,  proj: 16.4, own: 12, r: { contact: 60, power: 64, eye: 56, stay: 54, speed: 46, field: 58 } },
    // SS
    { id: 'h09', name: 'Otto Reyes',      team: 'KEE', pos: 'SS', salary: 11.0*LAKH, proj: 22.4, own: 31, r: { contact: 66, power: 54, eye: 68, stay: 70, speed: 74, field: 72 } },
    { id: 'h10', name: 'Jonas Vikram',    team: 'REE', pos: 'SS', salary: 8.6*LAKH,  proj: 17.0, own: 15, r: { contact: 62, power: 50, eye: 60, stay: 62, speed: 68, field: 66 } },
    // OF
    { id: 'h11', name: 'Léo Marchetti',   team: 'TID', pos: 'OF', salary: 13.0*LAKH, proj: 26.8, own: 41, r: { contact: 68, power: 74, eye: 70, stay: 60, speed: 72, field: 64 } },
    { id: 'h12', name: 'Anders Holt',     team: 'DCK', pos: 'OF', salary: 9.8*LAKH,  proj: 19.4, own: 19, r: { contact: 64, power: 60, eye: 62, stay: 56, speed: 64, field: 60 } },
    { id: 'h13', name: 'Cyrus Vanterpool',team: 'ANC', pos: 'OF', salary: 8.0*LAKH,  proj: 16.1, own: 14, r: { contact: 60, power: 58, eye: 54, stay: 52, speed: 70, field: 58 } },
    { id: 'h14', name: 'Pax Oduya',       team: 'FTH', pos: 'OF', salary: 7.4*LAKH,  proj: 14.9, own: 10, r: { contact: 58, power: 52, eye: 56, stay: 66, speed: 76, field: 62 } },
    // stay specialists (flex)
    { id: 'h15', name: 'Remy Faulk',      team: 'MST', pos: '2B', salary: 9.2*LAKH,  proj: 18.8, own: 23, r: { contact: 72, power: 40, eye: 70, stay: 78, speed: 68, field: 64 } },
    { id: 'h16', name: 'Tomas Brij',      team: 'REE', pos: 'C',  salary: 6.6*LAKH,  proj: 12.8, own: 8,  r: { contact: 54, power: 46, eye: 58, stay: 60, speed: 42, field: 60 } },
  ];

  const PLAYERS = RAW.map(p => {
    const isPitcher = p.isPitcher !== undefined ? p.isPitcher : p.pos === 'PILOT';
    // Use the real game log/form when injected; otherwise synthesize one.
    const hasReal = Array.isArray(p.log) && p.log.length && Array.isArray(p.form) && p.form.length;
    const built = hasReal ? { log: p.log, form: p.form } : buildLog({ ...p, isPitcher });
    const team = TEAMS[p.team] || { color: 'var(--c-coral)', name: p.team };
    return {
      ...p,
      isPitcher,
      value: p.salary > 0 ? +(p.proj / (p.salary / LAKH)).toFixed(2) : 0, // pts per lakh
      teamColor: team.color,
      teamName: team.name,
      init: initials(p.name),
      opp: oppFor(p.team),
      ...built,
    };
  });

  function oppFor(team) {
    for (const g of SLATE_GAMES) {
      if (g.home === team) return '@ ' + g.away; // simplistic
      if (g.away === team) return '@ ' + g.home;
    }
    return '';
  }

  // roster slots (O27-native: a PILOT + field + a STAY flex)
  const SLOTS = [
    { key: 'PILOT', label: 'PILOT', accepts: ['PILOT'] },
    { key: 'C',     label: 'C',     accepts: ['C'] },
    { key: '1B',    label: '1B',    accepts: ['1B'] },
    { key: '2B',    label: '2B',    accepts: ['2B'] },
    { key: '3B',    label: '3B',    accepts: ['3B'] },
    { key: 'SS',    label: 'SS',    accepts: ['SS'] },
    { key: 'OF',    label: 'OF',    accepts: ['OF'] },
    { key: 'STAY',  label: 'STAY',  accepts: ['C','1B','2B','3B','SS','OF'], flex: true },
  ];

  // ---- the format library ----------------------------------------------
  const FORMATS = [
    { id: 'dfs',     name: 'Daily Slate',      color: 'var(--c-coral)', icon: 'diamond',
      tag: 'live', desc: 'Salary-cap lineups on tonight\u2019s games. Build, lock, climb the board.',
      stat: ['4 games', '$1K cap'], live: true },
    { id: 'stay',    name: '2C League',        color: 'var(--c-teal)', icon: 'rings',
      tag: 'new', desc: 'Draft the second-chance artists. Scores only stays, stay-RBI & RAD grades.',
      stat: ['Season-long', '12 spots'] },
    { id: 'walkback',name: 'Walk-Back',        color: 'var(--c-violet)', icon: 'bolt',
      desc: 'Homers that keep paying. Score the HR plus every Walk-Back run it sets up.',
      stat: ['Weekly', 'Power only'] },
    { id: 'pilot',   name: 'Pilot Room',       color: 'var(--c-blue)', icon: 'anchor',
      desc: 'The finisher game. Score only arc-3 work — Ks, stops and damage in outs 19\u201327.',
      stat: ['Daily', 'Pilots only'] },
    { id: 'skipper', name: 'Skipper',          color: 'var(--c-amber)', icon: 'flag',
      desc: 'Draft managers, not players. Score declared-seconds, shifts and joker efficiency.',
      stat: ['Weekly', '8 skippers'] },
    { id: 'voyage',  name: 'Beat the Voyage',  color: 'var(--c-green)', icon: 'wave',
      desc: 'One pick a day. Clear a multi-hit AB or drive a stay run — or your streak sinks.',
      stat: ['Survivor', 'Streak: 0'] },
    { id: 'hothand', name: 'Hot Hand',         color: 'var(--c-pink)', icon: 'flame',
      desc: 'Ride the heat wave. Streak-weighted scoring rewards catching players mid-surge.',
      stat: ['Weekly', 'Momentum'] },
    { id: 'joker',   name: 'Joker Draft',      color: 'var(--c-lime)', icon: 'spark',
      desc: 'Draft an archetype portfolio — power, speed, contact — score on joker outcomes.',
      stat: ['Side-game', 'Quick'] },
  ];

  // ---- DFS contests for the slate (real when injected) -----------------
  const CONTESTS = (DATA && DATA.CONTESTS) || [
    { id: 'c1', name: 'The Crore Room',     color: 'var(--c-coral)',  badge: 'Cr', fee: 5*LAKH, prize: 50*CRORE, entries: 14820, cap: 20000, top: 12*CRORE,  kind: 'GPP' },
    { id: 'c2', name: 'Tidewater Nightcap', color: 'var(--c-teal)',   badge: 'TN', fee: 1*LAKH, prize: 5*CRORE,  entries: 4210,  cap: 6000,  top: 1.2*CRORE, kind: 'GPP' },
    { id: 'c3', name: 'Double-Up Dockside', color: 'var(--c-blue)',   badge: '2x', fee: 2*LAKH, prize: 3.6*CRORE,entries: 1880,  cap: 2000,  top: 4*LAKH,   kind: 'Double Up' },
    { id: 'c4', name: 'Single Stay Showdown',color: 'var(--c-violet)',badge: 'SS', fee: 50000,  prize: 80*LAKH,  entries: 980,   cap: 1500,  top: 8*LAKH,   kind: 'Head-to-Head' },
    { id: 'c5', name: 'Rookie Reef (Free)',  color: 'var(--c-green)', badge: 'F',  fee: 0,       prize: 25*LAKH,  entries: 6400,  cap: 10000, top: 50000,    kind: 'Freeroll' },
  ];

  // ---- live leaderboard (for the live screen) --------------------------
  const LEADERBOARD = [
    { rank: 1, user: 'saltwind',     pts: 188.4, win: 12*CRORE,  av: 'S' },
    { rank: 2, user: 'crore_dreams', pts: 181.0, win: 6*CRORE,   av: 'C' },
    { rank: 3, user: 'pilotlight',   pts: 176.6, win: 3*CRORE,   av: 'P' },
    { rank: 4, user: 'the_stay_king',pts: 172.2, win: 1.4*CRORE, av: 'T' },
    { rank: 5, user: 'reefwrecker',  pts: 169.8, win: 90*LAKH,   av: 'R' },
    { rank: 6, user: 'YOU',          pts: 164.5, win: 42*LAKH,   av: 'Y', me: true },
    { rank: 7, user: 'fathom_phil',  pts: 161.0, win: 30*LAKH,   av: 'F' },
    { rank: 8, user: 'mistralmax',   pts: 158.7, win: 24*LAKH,   av: 'M' },
    { rank: 9, user: 'galleon_gus',  pts: 155.2, win: 18*LAKH,   av: 'G' },
    { rank: 10,user: 'anchorsaway',  pts: 151.9, win: 12*LAKH,   av: 'A' },
  ];

  // the DFS scoring rule (batter), shown in UI as the "rule"
  const SCORING = [
    { k: 'Single', v: '+4' }, { k: 'Double', v: '+7' }, { k: 'Triple', v: '+10' },
    { k: 'Home Run', v: '+13' }, { k: 'Walk', v: '+2' }, { k: 'RBI', v: '+2' },
    { k: 'Run', v: '+1.5' }, { k: 'Stay', v: '+3' }, { k: 'Stay RBI', v: '+4' },
    { k: 'RAD grade A+', v: '+5' }, { k: 'Strikeout', v: '\u22121.5' },
  ];

  window.SLATE = {
    LAKH, CRORE, CAP, WALLET, money, RATES, CURRENCIES, ZORA_SYMBOL, mode: 'usd',
    TEAMS, SLATE_GAMES, PLAYERS, SLOTS, FORMATS, CONTESTS, LEADERBOARD, SCORING,
  };
})();
