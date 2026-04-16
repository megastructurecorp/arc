# Shared brief — Arc Jam Starlane

This is the worked-example game brief. Every agent in the jam reads
this document. Every agent must follow it to the letter. When in
doubt, the brief wins over any individual agent's opinion.

If you are adapting this recipe for your own jam, replace the
entirety of this file with your own brief. Keep the shape —
one-paragraph pitch, full rules, every edge case answered, complete
data format, complete interface contract.

---

## 1. Pitch

**Arc Jam Starlane** is a text-based single-player space
exploration and trading game, spiritually descended from the 1984
Elite and Ian Bell's later text-only port. You command a single
ship in an **infinite procedural galaxy**: jump to a neighbouring
star system, scan it, trade commodities on its market, sell the
scan data at a buyer system, refuel, jump again. Every system —
name, economy, government, tech level, population, description,
and market — is deterministically generated from the tuple
`(seed, x, y)`. Two players playing with the same seed see the
same galaxy forever.

The game has **two viable progression paths**, both producing
credits, both fun:

- **Commodity trading** — buy a commodity cheap at one system,
  sell it dear at another. The economy modifier tables determine
  profitable routes, the fuel budget limits how far you can chase
  them.
- **Exploration data** — every ship has a scanner. Visit a
  previously-unseen system, spend a turn scanning it, and you
  accumulate a "scan record" you can later sell at any other
  system. HIGH_TECH buyers pay premium for scan data; ANARCHY
  buyers pay very little. Explorers who venture into rarely-visited
  corners of the galaxy get the highest-value scans.

The game is **single-player** in the sense that one ship plays at
a time. The same binary is playable two ways:

1. **By a human** through `cli.py`: a text loop that prints the
   current system, asks for commands, and advances the game state.
2. **By an AI "pilot"** through `pilot_run.py`: a headless loop
   that asks a `Pilot` instance for an action each turn and applies
   it. The playtest agent runs this across hundreds of seeded
   games to balance the economy.

There is **no combat**, no NPC ships, no missions in v1. The whole
game is: find profitable trade routes and valuable scan buyers in
a galaxy you have never seen before, stretched against a fuel
budget that drains as you move. Everything interesting lives in
the content agent's procedural generation and the pilots'
decision heuristics.

## 2. Rules

### 2.1 Setup

- One ship, one player (human or AI pilot).
- The galaxy is infinite: for every integer coordinate `(x, y)`
  the content agent answers two questions deterministically under
  the game seed:
  - `is_system(seed, x, y) -> bool` — is there a star system
    here?
  - `generate_system(seed, x, y) -> SystemInfo` — if yes, what
    are its properties? (raises `ValueError` if no)
- System density is roughly 1 in 8 coordinates (the content agent
  tunes the exact hash threshold).
- The ship starts at the nearest system to `(0, 0)` under the
  game seed. `engine.new_game(seed)` searches outward from the
  origin until it finds a valid system.
- Starting ship: 100 credits, 20/20 fuel (in light-years), 20
  tonnes cargo capacity, empty hold.

### 2.2 Coordinates, distance, and jumps

- Distance between `(x1, y1)` and `(x2, y2)` is Euclidean,
  **rounded up** to the nearest integer:
  `ceil(sqrt((x1-x2)^2 + (y1-y2)^2))`.
- Ships have a **hard maximum jump range of 7 ly** per jump,
  regardless of fuel. A jump beyond 7 ly is illegal even with a
  full tank.
- Each jump consumes `distance_ly` fuel (one fuel = one light
  year).
- The target of a jump must be a system (`is_system(seed, tx,
  ty)` is True). You cannot jump into empty space.
- On a successful jump: fuel is deducted, `(x, y)` updates to the
  target, `day` advances by `distance_ly`, `turn_num` increments
  by 1, and the target is added to `visited`.

### 2.3 Economy and markets

Every system has a deterministic market — the same seed and the
same `(x, y)` always give the same market quotes. Markets are
**not** affected by what the player buys or sells; supply and
demand are fixed. This keeps v1 simple and still produces
interesting trade routes because *different* systems have
*different* prices.

Each market is a dict `{commodity_id: MarketQuote}`. A
`MarketQuote` has:

- `buy_price: int` — credits per tonne to buy here (the cost the
  ship pays)
- `sell_price: int` — credits per tonne the ship receives if
  selling here; always `<= buy_price` (a ~5% brokerage spread)
- `available: int` — tonnes the market is willing to sell in a
  single visit (a soft upper bound on `buy` actions); demand for
  sells is unbounded
- `legal: bool` — whether the commodity can be bought/sold at
  this government type (v1: all legal; the field is in the
  contract for v2 extension but is always True)

Prices are a pure function of the base price (from
`data/commodities.toml`), the system's economy modifier (from
`data/economy_mods.toml`), a government volatility jitter, and
a per-system hash salt. The content agent owns the exact formula
and must document it in the module docstring.

### 2.4 Actions

On any turn, the current actor (human or pilot) can perform
exactly one of the following:

| Action | Effect | `turn_num` cost |
|---|---|---|
| `jump(tx, ty)` | Jump to system at (tx, ty). See §2.2. | +1 |
| `refuel(ly)` | Buy `ly` units of fuel at the current system. Cost `10 credits/ly`. | +1 |
| `buy(commodity, qty)` | Buy `qty` tonnes of `commodity` at current market. Cost `qty * buy_price`. Cargo grows by `qty`. | +1 |
| `sell(commodity, qty)` | Sell `qty` tonnes from the hold. Credits grow by `qty * sell_price`. | +1 |
| `scan()` | Survey the current system. Adds `(x, y)` to `ship.scans`. Raises if already scanned. See §2.8. | +1 |
| `sell_scan(tx, ty)` | Sell the scan for system `(tx, ty)` at the ship's *current* system. Removes `(tx, ty)` from `ship.scans`, adds `scan_value(seed, target, buyer)` credits. See §2.8. | +1 |
| `end_session()` | Voluntarily end the session. Sets `session_over = True`. | +1 |

Read-only queries (`get_system`, `get_market`, `get_neighbors`,
`peek_scan_value`) do **not** advance `turn_num`. Pilots and CLIs
may call them freely between actions.

Every mutating action can raise `ValueError` if it's illegal
(insufficient credits, insufficient cargo space, out of fuel,
commodity not in market, etc.). The full list of raise
conditions is in §3.1.

### 2.5 Session end conditions

The session ends when any of the following is true:

1. The actor calls `end_session()`.
2. The ship is **stranded**: fuel is 0, no neighboring system is
   reachable on 0 fuel, and credits are insufficient to buy 1 ly
   of fuel at the current system (`credits < 10`). The engine
   detects this automatically at the end of every action and
   sets `session_over = True` with a `stranded = True` flag.
3. `turn_num` exceeds the hard cap of **500 turns**. This is a
   safety rail, not a design target — a typical session is 50 to
   250 turns.

When the session ends, no further actions are legal; they raise
`ValueError`.

### 2.6 Content data format

Arc Jam Starlane is **data-heavy**. The content agent owns six
data files that together define the universe:

#### 2.6.1 `data/syllables.txt`

One syllable per line, ~80 lines of 1-3 character pairs suitable
for procedural name generation, Elite-style. Examples:

```
ar
en
du
ti
lo
qua
ves
bri
```

System names are generated by concatenating 2–4 syllables chosen
under a per-system RNG. Names are ~5–12 characters long. The
content agent owns the exact algorithm and documents it in the
module docstring.

#### 2.6.2 `data/adjectives.txt`

One adjective per line, ~60 lines. Used in descriptions:

```
unremarkable
dense
volcanic
ringed
tidally locked
ice-locked
cloud-shrouded
```

#### 2.6.3 `data/fauna.txt`

One plural noun per line, ~50 lines. Used in the "notable for
its..." descriptions:

```
rock goats
luminous fungi
ice-skating birds
crystal octopi
thought-lobsters
```

#### 2.6.4 `data/remarkable.txt`

One "notable" phrase template per line, ~40 lines. Templates may
contain `{fauna}` as a placeholder:

```
its rapid {fauna}
the famous {fauna} of the inner moons
a lively export trade in {fauna}
its dangerous {fauna}
its wild {fauna} festivals
```

#### 2.6.5 `data/commodities.toml`

Structured commodity catalog. Required fields per commodity:

```toml
[[commodity]]
id           = "FOOD"
name         = "Food"
base_price   = 10            # credits per tonne at a TERRAFORM system
unit_mass    = 1             # tonnes per unit (v1: always 1)
volatility   = 0.15          # max ± jitter as a fraction of base_price

[[commodity]]
id           = "TEXTILES"
name         = "Textiles"
base_price   = 20
unit_mass    = 1
volatility   = 0.10

# ... and so on for COMPUTERS, MACHINERY, ALLOYS, FIREARMS,
# RADIOACTIVES, LIQUOR, LUXURIES, FURS, MINERALS, GOLD,
# PLATINUM, GEM_STONES, ALIEN_ITEMS
```

Minimum catalog: 12 commodities. The content agent may add more.

#### 2.6.6 `data/economy_mods.toml`

Per-economy multipliers for each commodity. Required economies:
`AGRICULTURAL`, `INDUSTRIAL`, `HIGH_TECH`, `EXTRACTION`,
`TERRAFORM`. Example shape:

```toml
[AGRICULTURAL]
FOOD       = 0.5
TEXTILES   = 0.7
FURS       = 0.6
COMPUTERS  = 1.6
MACHINERY  = 1.4
FIREARMS   = 1.3
# ... all commodities, no defaults

[INDUSTRIAL]
FOOD       = 1.4
COMPUTERS  = 0.8
MACHINERY  = 0.7
ALLOYS     = 0.8
# ...
```

Every (economy, commodity) pair must be present. A missing entry
is a content-agent bug — enforce it in a test.

#### 2.6.7 Scan value data

The `scan_value(seed, target, buyer)` function produces the
credit payout for selling a scan. It is a pure function of its
inputs — no hidden state, no randomness outside the seeded
hash. The content agent owns the exact formula but it must be
composed of these inputs:

1. **Target rarity.** Compute a "data value" for the target
   system based on its `SystemInfo`:
   - Base value: `10 + tech_level * 5` (so a tech-1 system is
     worth 15, a tech-15 system is worth 85)
   - Economy bonus: HIGH_TECH ×1.8, EXTRACTION ×1.4,
     INDUSTRIAL ×1.0, TERRAFORM ×1.2, AGRICULTURAL ×0.8
   - Population factor: `1 + (population / 500)` (capped at 2.0)
2. **Buyer multiplier.** Compute a market multiplier based on
   the buyer system's economy:
   - HIGH_TECH ×1.5, EXTRACTION ×1.1, INDUSTRIAL ×1.0,
     TERRAFORM ×0.9, AGRICULTURAL ×0.8
   - Further: if `buyer.government == "ANARCHY"`, multiply by
     0.7 (black-market buyers underpay).
3. **Distance penalty.** If target and buyer are the same
   coordinate, multiply by 0.6 (local data is cheap). Otherwise
   1.0.
4. **Final:** `max(1, int(round(data_value * buyer_mult *
   distance_factor)))`.

These multipliers are starting points the content agent can
tune per report. Document the final values in the module
docstring.

The target credit range across a typical galaxy should be 5
to 200 credits per scan, with a median around 40 and HIGH_TECH
buyers at the top of the band. If your tuning pushes the
median below 10 or above 100, the exploration path becomes
trivially dominated by commodity trading (too low) or trivially
dominant over it (too high).

### 2.7 Determinism requirements

**Everything is a pure function of `seed` and coordinates.**

- `is_system(seed, x, y)` — same inputs, same output, forever.
- `generate_system(seed, x, y)` — same inputs, same output,
  forever, including the full description string.
- `market_for(seed, x, y)` — same inputs, same output, forever,
  including every commodity's prices.
- Game actions never mutate anything outside the `GameState`
  passed in.
- **No module calls `random.*` at module level.** All RNGs are
  `random.Random(seed_tuple)` instances built per query. A
  single call to global `random` anywhere in `content.py` or
  `engine.py` is a bug that breaks benchmark reproducibility.

The playtest agent will verify this by running the same
benchmark twice under the same seed and diffing the reports.
Any difference is a determinism bug and gets reported as a
critical issue.

### 2.8 Exploration data (scans) — the second progression path

Every ship has a basic scanner, always equipped, no upgrade
path in v1. Scanning turns physical exploration into a
tradeable resource and gives pilots a second way to make money
that does not depend on commodity markets.

**Scanning.** `do_scan(state)` records the ship's current
coordinates in `ship.scans`, a `set[tuple[int, int]]`. A system
can only be scanned once per game — `do_scan` raises
`ValueError` if the current coordinate is already in
`ship.scans`. Scanning costs one turn but nothing else (no
credits, no fuel, no cargo).

**Selling scans.** `do_sell_scan(state, tx, ty)` sells the scan
for the system at `(tx, ty)` at the ship's *current* system
(the buyer). The payout is `content.scan_value(seed, (tx, ty),
(ship.x, ship.y))` credits. On success, `(tx, ty)` is removed
from `ship.scans` — each scan pays out exactly once. Raises
`ValueError` if `(tx, ty)` is not in `ship.scans` or if the
session is over.

**Key properties:**

- **Scans are not cargo.** They occupy zero tonnes. A fully
  loaded freighter can still carry unlimited scan data.
- **Scan value is pure.** `scan_value(seed, target, buyer)` is
  deterministic: the same scan sold to the same buyer in the
  same galaxy is always worth the same credits. Buyers in
  different systems pay different amounts.
- **The scan can be sold at any system** (not just HIGH_TECH),
  but HIGH_TECH buyers pay substantially more. Pilots that
  dump scans at the first system they reach will get ~60% of
  the optimal payout; pilots that route through HIGH_TECH
  systems will get ~140%.
- **Selling the scan of the system you are currently standing
  on is legal** — you scan on arrival, then immediately sell
  it to the local buyer. You get the local-system rate. (The
  content agent may tune this to be unfavourable, encouraging
  pilots to travel before selling.)

**Content agent's responsibility.** The exact `scan_value`
formula is owned by the content agent (§2.6.7). The target
(5..200 credits per scan, with HIGH_TECH buyers at the top of
that band) gives pilots a reason to seek out rare or distant
systems.

## 3. Interface contract (v1)

Every agent's module conforms to this. **No code is written
until all four agents have acknowledged this contract on the
`jam-interface` thread** (see `jam-protocol.md`).

### 3.1 `engine.py`

```python
from dataclasses import dataclass, field
from typing import Optional
from content import SystemInfo, MarketQuote

CommodityId = str  # "FOOD" | "COMPUTERS" | ...

@dataclass
class ShipState:
    credits: int = 100
    fuel: int = 20                       # light-years of fuel remaining
    fuel_capacity: int = 20
    cargo_capacity: int = 20             # tonnes
    cargo: dict[CommodityId, int] = field(default_factory=dict)
    scans: set[tuple[int, int]] = field(default_factory=set)  # unique-system scan records
    x: int = 0
    y: int = 0

@dataclass
class GameState:
    seed: int
    ship: ShipState
    turn_num: int = 0                    # 0-indexed; incremented per action
    day: int = 0                         # in-game time, +distance on jump
    visited: set[tuple[int, int]] = field(default_factory=set)
    session_over: bool = False
    stranded: bool = False

MAX_JUMP_LY = 7
REFUEL_COST_PER_LY = 10
TURN_HARD_CAP = 500

def new_game(seed: int) -> GameState:
    """Construct a fresh game. Searches outward from (0,0) using
    content.nearest_system and spawns the ship at that coordinate.
    Raises RuntimeError if no system is found within 20 units of
    the origin (should not happen with reasonable density)."""

def do_jump(state: GameState, tx: int, ty: int) -> GameState:
    """Jump to (tx, ty). Raises ValueError if:
      - (tx, ty) is not a system under this seed
      - distance > MAX_JUMP_LY
      - distance > state.ship.fuel
      - (tx, ty) equals the current position (zero-length jump)
      - session is already over
    On success: deducts fuel, updates (x, y), increments day by
    the distance, increments turn_num, adds the target to visited."""

def do_refuel(state: GameState, ly: int) -> GameState:
    """Refuel `ly` units at the current system's fuel station.
    Cost = ly * REFUEL_COST_PER_LY. Raises ValueError if:
      - ly <= 0
      - ship.fuel + ly > ship.fuel_capacity
      - state.ship.credits < ly * REFUEL_COST_PER_LY
      - session is already over"""

def do_buy(state: GameState, commodity: CommodityId, qty: int) -> GameState:
    """Buy `qty` tonnes of `commodity` at the current market.
    Raises ValueError if:
      - qty <= 0
      - commodity is not in the current market
      - qty > market[commodity].available
      - cargo space after purchase > cargo_capacity
      - credits < qty * buy_price
      - session is already over"""

def do_sell(state: GameState, commodity: CommodityId, qty: int) -> GameState:
    """Sell `qty` tonnes of `commodity` from cargo.
    Raises ValueError if:
      - qty <= 0
      - commodity not in cargo or cargo[commodity] < qty
      - commodity is not in the current market
      - session is already over"""

def do_scan(state: GameState) -> GameState:
    """Scan the ship's current system, adding (ship.x, ship.y) to
    ship.scans. Raises ValueError if:
      - (ship.x, ship.y) is already in ship.scans
      - session is already over"""

def do_sell_scan(state: GameState, tx: int, ty: int) -> GameState:
    """Sell the scan for system (tx, ty) at the ship's current
    system. Removes (tx, ty) from ship.scans and adds
    content.scan_value(seed, (tx, ty), (ship.x, ship.y)) credits.
    Raises ValueError if:
      - (tx, ty) is not in ship.scans
      - session is already over"""

def end_session(state: GameState) -> GameState:
    """Voluntarily end. Sets session_over = True. Increments
    turn_num. Idempotent — calling on an already-ended session
    is a no-op (does not raise)."""

def is_game_over(state: GameState) -> bool:
    """True iff session_over is set OR turn_num >= TURN_HARD_CAP.
    Also sets state.session_over and state.stranded as a side
    effect if stranded-detection triggers (called at the end of
    every mutating action)."""

def get_system(state: GameState) -> SystemInfo:
    """Return the SystemInfo for the ship's current (x, y). Pure
    query — does not advance turn_num. Never raises for a valid
    GameState (the ship's current position is always a system)."""

def get_market(state: GameState) -> dict[CommodityId, "MarketQuote"]:
    """Return the market quotes for the ship's current (x, y).
    Pure query, does not advance turn_num."""

def get_neighbors(state: GameState, max_ly: int = MAX_JUMP_LY) -> list["SystemInfo"]:
    """Return all systems reachable from the current position in
    a single jump of <= max_ly. Pure query, does not advance
    turn_num."""

def peek_scan_value(state: GameState, tx: int, ty: int) -> int:
    """Return what content.scan_value would return for selling the
    scan of (tx, ty) at the ship's current position. Pure query
    — does not require (tx, ty) to be in ship.scans, does not
    mutate anything, does not advance turn_num. Used by pilots
    to compare potential scan payouts without committing."""
```

### 3.2 `content.py`

```python
from dataclasses import dataclass, field
from typing import Optional

DATA_DIR = "data"

@dataclass(frozen=True)
class SystemInfo:
    x: int
    y: int
    name: str                    # e.g. "Usenat"
    economy: str                 # "AGRICULTURAL" | "INDUSTRIAL" | "HIGH_TECH" | "EXTRACTION" | "TERRAFORM"
    government: str              # "ANARCHY" | "FEUDAL" | "DEMOCRACY" | "CORPORATE" | "CONFEDERACY"
    tech_level: int              # 1..15
    population: int              # in millions, 1..500
    description: str             # 1–2 sentence procgen flavor

@dataclass(frozen=True)
class MarketQuote:
    commodity: str
    buy_price: int
    sell_price: int              # always <= buy_price
    available: int               # tonnes purchasable this visit
    legal: bool = True

# Pure generators — all deterministic under (seed, x, y)

def is_system(seed: int, x: int, y: int) -> bool:
    """Deterministic: is there a star system at (x, y) under this
    seed? Target density ~1 in 8 coordinates. The content agent
    owns the exact hash function but it must be stable across
    runs (do not use Python's built-in hash())."""

def generate_system(seed: int, x: int, y: int) -> SystemInfo:
    """Return the full SystemInfo for (seed, x, y). Pure function.
    Raises ValueError if is_system(seed, x, y) is False."""

def nearest_system(seed: int, x: int, y: int, max_radius: int = 20) -> Optional[tuple[int, int]]:
    """Return the (x, y) of the nearest system to the given
    coordinates, within max_radius. Used by engine.new_game to
    find the spawn point. Returns None if no system found."""

def neighbors(seed: int, x: int, y: int, max_ly: int = 7) -> list[SystemInfo]:
    """Return all SystemInfo objects within max_ly of (x, y),
    excluding (x, y) itself. Enumerates integer coordinates in
    the ball, filters by is_system, returns populated
    SystemInfos."""

def market_for(seed: int, x: int, y: int) -> dict[str, MarketQuote]:
    """Return the full market for the system at (x, y).
    Deterministic under (seed, x, y). Raises ValueError if
    is_system(seed, x, y) is False."""

def scan_value(
    seed: int,
    target: tuple[int, int],
    buyer: tuple[int, int],
) -> int:
    """Return the credit payout for selling a scan of `target` at
    `buyer`. Pure function, deterministic under all inputs.
    Formula is documented in §2.6.7 — composed of target rarity,
    buyer economy multiplier, and a local-data discount. Typical
    range 5..200 credits per scan. Raises ValueError if either
    coordinate is not a system under `seed`."""

def all_commodities() -> list[str]:
    """Return the list of all commodity ids defined in
    data/commodities.toml."""

def load_commodities() -> dict:
    """Load raw commodity data from the toml file. Exposed for
    tests; other modules should not call this directly."""

def load_economy_mods() -> dict:
    """Load raw economy modifier data. Exposed for tests."""
```

### 3.3 `pilots.py`

```python
from dataclasses import dataclass
from typing import Protocol, Union
from engine import GameState

@dataclass(frozen=True)
class JumpAction:
    x: int
    y: int

@dataclass(frozen=True)
class RefuelAction:
    ly: int

@dataclass(frozen=True)
class BuyAction:
    commodity: str
    qty: int

@dataclass(frozen=True)
class SellAction:
    commodity: str
    qty: int

@dataclass(frozen=True)
class ScanAction:
    pass

@dataclass(frozen=True)
class SellScanAction:
    target_x: int
    target_y: int

@dataclass(frozen=True)
class EndSessionAction:
    pass

Action = Union[
    JumpAction,
    RefuelAction,
    BuyAction,
    SellAction,
    ScanAction,
    SellScanAction,
    EndSessionAction,
]

class Pilot(Protocol):
    name: str

    def choose_action(self, state: GameState) -> Action:
        """Return the next action the pilot wants to take. MUST
        NOT mutate state. MUST return a legal action — the
        pilot_run harness enforces legality by catching
        ValueError and counting it as a pilot failure. Legal
        here means: the action would not immediately raise
        ValueError from the engine given the current state."""

# Concrete pilots this module must export:
#   GreedyTrader  — optimises wealth via commodities: picks the
#                   trade with the highest immediate profit in
#                   reach; jumps toward the best-price neighbour
#                   when the hold is full; refuels on demand.
#                   Ignores scans.
#   ScoutExplorer — optimises wealth via exploration data: scans
#                   every new system on arrival, stockpiles scan
#                   records, actively seeks HIGH_TECH neighbours
#                   to sell scans at the best rate. Trades
#                   commodities only when credits fall below 50
#                   and no scan buyer is within jump range.
#                   Refuels to full at every system.
#   SafeHauler    — minimises bankruptcy risk; trades only FOOD,
#                   TEXTILES, MACHINERY (stable commodities);
#                   refuels to full every turn; scans opportunistically
#                   (if already at an unscanned system) but never
#                   detours for it.
#   BargainHunter — buys only when buy_price < 75% of the
#                   commodity's galaxy-wide mean (derived from
#                   the catalog base_price); sells at first
#                   profitable system. Ignores scans.

ALL_PILOTS: list[Pilot]  # the four above, instantiated
```

### 3.4 `pilot_run.py`

```python
from dataclasses import dataclass
from pilots import Pilot

@dataclass
class PilotRun:
    pilot: str
    seed: int
    turns_played: int
    final_credits: int
    systems_visited: int
    jumps: int
    trades: int                   # commodity buys + sells
    scans_made: int               # scan actions executed
    scans_sold: int               # sell_scan actions executed
    scan_revenue: int             # cumulative credits from sell_scan
    trade_revenue: int            # cumulative credits net from buy/sell
    stranded: bool
    crashed: bool                 # pilot raised or returned illegal action
    crash_reason: Optional[str] = None  # if crashed, the ValueError string

@dataclass
class BenchmarkReport:
    pilots: list[str]
    runs_per_pilot: int
    avg_credits: dict[str, float]
    avg_systems_visited: dict[str, float]
    avg_turns: dict[str, float]
    avg_scans_made: dict[str, float]
    avg_scans_sold: dict[str, float]
    avg_scan_revenue: dict[str, float]
    avg_trade_revenue: dict[str, float]
    stranded_rate: dict[str, float]
    crash_rate: dict[str, float]
    wealth_distribution: dict[str, list[int]]  # per pilot: sorted final_credits

def run_one(pilot: Pilot, seed: int, max_turns: int = 500) -> PilotRun:
    """Play one full session. Builds a new game under `seed`, then
    in a loop asks the pilot for an action and applies it via the
    engine until is_game_over. If the engine raises ValueError
    because the pilot's action was illegal, the run ends with
    crashed=True and the ValueError message in crash_reason."""

def run_benchmark(pilots: list[Pilot], n: int = 100) -> BenchmarkReport:
    """Run each pilot `n` times under seeds 0..n-1, aggregate the
    metrics. The seed range is intentionally shared across pilots
    so they face the same galaxies — this is what makes the
    report comparable."""

def format_report(report: BenchmarkReport) -> str:
    """Return a plain-text report suitable for posting as an
    artifact to jam-reports."""
```

### 3.5 `cli.py`

```python
def main() -> int:
    """Human-playable CLI. On start, asks for a seed (or uses
    a default), constructs a new_game, then loops:

        > help
        Commands: scan, market, neighbors, jump X Y, refuel N,
                  buy COMMODITY N, sell COMMODITY N, status,
                  end, quit

    Reads input() lines, parses into actions, calls the engine,
    prints outcomes. Exits cleanly on `quit` or end of session.
    Used only as a sanity check — not exercised in the benchmark."""
```

## 4. Test expectations (cross-agent)

Each agent writes tests for their own module. Every test file
must pass under `python -m unittest discover tests -v` without
imports from other agents' test files. Specifically:

- **`tests/test_engine.py`** owned by engine agent — covers
  every action in §2.4, every ValueError case in §3.1,
  stranded-detection, the 500-turn cap, a full-session
  determinism test, **and the scan path**:
  - `do_scan` on a fresh system adds `(x, y)` to `ship.scans`
  - `do_scan` on an already-scanned system raises `ValueError`
  - `do_sell_scan(tx, ty)` removes `(tx, ty)` from `ship.scans`
    and adds `scan_value(...)` credits
  - `do_sell_scan` on a coord not in `ship.scans` raises
    `ValueError`
  - scans survive across jumps (they don't reset when you leave)
  - `peek_scan_value` is pure (does not mutate, does not require
    the coord be in `ship.scans`)
- **`tests/test_content.py`** owned by content agent — asserts
  data file loading, generator determinism (`generate_system(1,
  4, 7)` == `generate_system(1, 4, 7)` across two calls), name
  generator variety (100 seeds produce at least 50 distinct
  names), economy mod coverage (every economy has every
  commodity), neighbor density (a 7-radius ball has ≥3 systems
  on average under seed=0), **and scan pricing**:
  - `scan_value` is deterministic under `(seed, target, buyer)`
  - A HIGH_TECH buyer pays strictly more than an AGRICULTURAL
    buyer for the same target scan (for at least 80% of sampled
    targets)
  - Selling a scan at its own coordinate returns less than
    selling it at a distinct buyer (distance discount works)
  - Median `scan_value` across 100 random `(target, buyer)`
    pairs under seed=0 is between 20 and 80 credits (the target
    band)
  - `scan_value` raises `ValueError` if either coordinate is
    not a system
- **`tests/test_pilots.py`** owned by pilots agent — sanity
  checks per pilot on hand-constructed `GameState` objects
  (GreedyTrader prefers the highest-profit trade available,
  ScoutExplorer scans a newly-reached system and pursues
  HIGH_TECH buyers, SafeHauler refuels at every step,
  BargainHunter buys only cheap commodities). Also a "no
  mutation" invariant across all pilots. At minimum:
  - ScoutExplorer, standing on an unscanned system, returns
    `ScanAction()`
  - ScoutExplorer, standing on a scanned system with scan
    records in inventory and a HIGH_TECH neighbour, returns
    `JumpAction` toward the HIGH_TECH neighbour
  - ScoutExplorer, standing on a HIGH_TECH system with scan
    records, returns `SellScanAction(...)` for the most valuable
    record before doing anything else
- **`tests/test_pilot_run.py`** owned by playtest agent — a
  5-pilot-run benchmark completes without error, report shape
  matches `BenchmarkReport`, reproducibility check (same seed
  twice → same report), a deliberately-broken mock pilot is
  handled cleanly (the run sets `crashed=True` instead of
  propagating the exception), **and scan accounting**:
  - `run_one` populates `scans_made`, `scans_sold`,
    `scan_revenue`, and `trade_revenue` correctly for a scripted
    pilot that does two scans and sells one
  - `BenchmarkReport.avg_scan_revenue["ScoutExplorer"]` is
    strictly greater than `avg_scan_revenue["GreedyTrader"]`
    in a 5-seed benchmark (scout actually explores)

## 5. Non-goals

- No combat, no NPC ships, no missions. Encounters may come in
  v2; the v1 surface is strictly explore + trade.
- No multiplayer. One ship per game.
- No time-varying prices, no supply/demand regeneration. Markets
  are fully deterministic under `(seed, x, y)`. Pilots that
  exploit a route do so cleanly; the fuel drain is the limiting
  factor.
- No save/load. A session is ephemeral.
- No graphics. Text I/O only.
- No more than 5 pilots in v1. If one needs to be cut, cut it.
- No rules changes during phase 1 — rules can only be revised
  after the first playtest report in phase 2.
- No hand-authored system lists. Every system is procedurally
  generated from the content agent's data files.
