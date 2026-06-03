# AAR — CapSpace realism pass (player feedback round)

## Context

After the seven games shipped (see `aar-capspace-games.md`), live play-testing
on a fresh save surfaced a cluster of "this doesn't feel real" issues. This
pass closed them. The throughline: **everything the UI shows should come from
the active save, not bundled demo constants.**

## Findings (the audit)

| Symptom | Root cause |
| --- | --- |
| "Sim day Jun 16" never changed | hardcoded label in the header + live screen |
| Best Ball / Categories: "No players found" | their draft pool was built **only from played-game history**; a fresh save has none |
| Player card showed 20–80 ratings, not stats | the drawer was ratings-first (OOTP-style), and only DFS could open it |
| "Last 5 games" looked fake (J10–J14) | a JS generator (`buildLog`) synthesized logs when no real history existed |
| Fake live leaderboard (saltwind, crore_dreams…) | dead `LEADERBOARD` mock array (LiveScreen already used real data) |
| Wallet stuck at ƒ3.42 Cr | hardcoded JS constant, not tied to any save balance |

## What changed

- **Sim date is real.** The slate date already came from the save
  (`_slate_date`); exposed it as `SLATE_DATE`/`SIM_DAY` and removed the two
  hardcoded "Jun 16" labels. The header now reads e.g. "Apr 6" and tracks the
  loaded save (hides under bundled mock).

- **Best Ball & Categories work on any save.** Both now draft from the active
  roster (`_active_dir` over the `players` table): real season stat lines once
  games are played, a **rating-based projection pre-season**. Razz still
  surfaces the weakest players first. Standings remain history-based (0 until
  games are simmed — you draft pre-season, standings fill as you sim).

- **Stats-first player card, openable everywhere.** New `GET /api/player/<id>`
  returns the season stat line (AVG/OBP/SLG + HR/RBI/R/SB for hitters;
  IP/ERA/WHIP/K/QS for pitchers), real last-5 logs, 20–80 ratings, and an
  almanac link. The drawer fetches by id and **leads with stats**, with a
  **ratings toggle** (defaults to ratings pre-season, the only talent signal
  when there's no stat line) and a **"View full profile in the almanac"**
  link — surfacing the wider game, not siloing players in the fantasy app.
  Players are now clickable from **every** game (Pilots, Sluggers, Go
  Streaking, Categories, Best Ball), not just DFS.

- **Fabricated content removed.** Dropped `buildLog` (no synthesized history —
  the card shows real logs or "No games played yet") and the dead
  `LEADERBOARD` fake-username array.

- **Real wallet economy.** A per-save play-money wallet (`cap_wallet`,
  guilders) seeded at ƒ50 lakh. DFS entry **fees are debited** on entry
  (rejected when short); **winnings are credited once** a contest's slate goes
  final, per entry, ranked against that contest's deterministic field
  (`_field_sample` + `_entry_rank` + the existing `_payout` curve), with a
  `settled` flag so it credits exactly once. Served into the page (`WALLET`)
  and via `GET /api/wallet`; the header updates immediately after an entry
  from the `enter()` response.

## Validation

- Best Ball / Categories pool: **0-game save** → 300/250 (and per-format)
  draftable players, valid drafts; **with history** → real stat lines. Razz
  surfaces low-projection players first.
- Player card: with history → real stats + 5 logs + almanac link; fresh save →
  empty stats, ratings shown, rating-based projection. 404 on unknown id.
- Wallet full cycle on a real slate flow: start 5,000,000 → enter a ƒ1,000
  contest → 4,999,000 → sim the slate final → winnings credited (settled=1,
  payout=100,000) → 5,099,000, reconciling to `start − fee + payout`.
- All seven game endpoints + `/`, `/api/wallet`, `/api/player/<id>` serve 200;
  the page embeds the real `SLATE_DATE` and `WALLET`.

## What was NOT changed / notes

- **Sportsbook keeps its own bankroll** (a self-contained book in "units"),
  separate from the guilder wallet — intentional; unifying scales would rework
  its whole stake UX.
- `WALLET` only re-reads on navigation/reload except right after a DFS entry
  (which updates it from the response); a global wallet store could make every
  balance change instant, but wasn't needed for this pass.
- `pytest` is absent in the sandbox; validation was via the Flask `test_client`
  and direct module calls.
