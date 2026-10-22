# Floor Heating Lab

`FloorHeatingLab.pushbutton` is a temporary pyRevit test harness for an eventual
standalone Revit add-in. It does not use the loaded rvt-mcp plugin DLL for geometry.

The MCP-enabled Revit process reads
`%LOCALAPPDATA%\RvtMcp\floor-heating-lab\request-<pid>.json` on each click.
The script writes `result-<pid>.json` and `state-<pid>.json`; all files are keyed by
the Revit process id. The result is only saved to JSON; no console output is used.

Requests:

```json
{"action":"inspect"}
{"action":"preview","filled_region_id":12345,"pipe_in_id":23456,"pipe_out_id":34567,"offset_mm":50,"spacing_mm":300}
{"action":"preview","items":[{"filled_region_id":12345,"pipe_in_id":23456,"pipe_out_id":34567}]}
{"action":"clear"}
```

`inspect` reads the active view's FilledRegions and nearby pipe endpoints.
`preview` accepts a single region/pipe pair or an `items` array of up to 10 pairs
with no repeated region or pipe. It creates detail lines in one transaction and
stores the run ID together with every created line ID and, for each one, its
view-space millimetre endpoints in `state-<pid>.json`. A second preview is
refused until `clear`. `clear` verifies the document title and path, the view ID,
that each element is still a `DetailCurve`, that its `OwnerViewId` is still the
active view, and that its `GeometryCurve` endpoints still match the stored
coordinates within `ENDPOINT_TOLERANCE_MM`, before deleting only the previous
run's detail lines in one transaction. Neither action saves the model. The
algorithm is re-read from `geometry.py` on every invocation, so geometry edits
need no Revit restart or pyRevit reload.

The line identity check replaces an earlier design that stamped a run ID into
each line's Comments parameter. `DetailCurve` has no writable
`ALL_MODEL_INSTANCE_COMMENTS`, so that stamping raised
`detail_line_has_no_writable_comments_parameter` and rolled the whole preview
transaction back. Run IDs and geometry now live only in `state-<pid>.json`.

## Supported region shapes

`preview` supports exactly four boundary configurations, distinguished by the number
of orthogonal line edges in the single outer loop:

| Edges | Shape |
| --- | --- |
| 4 | rectangle |
| 6 | rectangle main + one left-upper wing |
| 8 | rectangle main + one top-central nub |
| 12 | rectangle main + a top-central nub **and** a right-side nub |

The 12-edge shape is decomposed by `top_nub_and_right_nub_boxes` into three
boxes — the main rectangle, the top nub, and the right nub — and the spiral is
built on the main rectangle only, with both nub detours layered on afterwards.

Anything else — other edge counts, non-orthogonal edges, non-line edges, more than one
outer loop, several protrusions, or any general polygon fill — is unsupported and
raises `unsupported_shape`. There is no universal polygon decomposition or filling.

`offset_mm` and `spacing_mm` are millimetres. Output is `DetailCurve` detail lines
only; no pipes are created, and no length limit is applied or checked.

`offset_mm` defaults to `200` and `spacing_mm` defaults to `150` when omitted from the
request. Both must still lie in the supported range (`0 ≤ offset ≤ 1000`,
`0 < spacing ≤ 2000`) or the request is rejected with `invalid_offset_or_spacing`.

## Protrusion detours

For the 6-, 8- and 12-edge shapes the main rectangle gets the spiral, and each
protrusion gets **exactly one U-shaped detour** that runs out and back into the
protrusion. The detour is added only when a single U fits inside the
protrusion (the top nub needs `width > 2 * offset` and `height > offset`, the
right nub needs the analogous clearance) and the resulting path stays
continuous and free of self-intersections. The detour delivers exactly **two
vertical lines into the top nub** (at `nub_left + offset` and
`nub_right - offset`) and exactly **two horizontal lines into the right nub**
(at `side[1] + offset` and `side[3] - offset`) — there is no deeper second
detour. The neighbouring inner column (at the second `spacing_mm` row or
column) intentionally stays in the main body so the freed-up space inside
the body still receives one full pass of pipe.

If the protrusion is too narrow to host a single U-shape, it is skipped and
the main-rectangle spiral is carried forward unchanged.

The 8-edge shape has one protrusion (the top nub) and the 12-edge shape has two
(the top nub and the right nub), each attempted independently — one detoured
protrusion does not block the other.

## Central U-turn

After the spiral and any protrusion detour are built, an additional **central U-turn**
is folded into the spiral's tiny bridge (the short UP → RIGHT → DOWN connector that
links the inward ring to the outward ring). The bridge's RIGHT segment is extended
by one `spacing_mm` step to the right, dropped down by `bridge_height − spacing_mm`,
run back left to the original DOWN column, and returned DOWN to the original bridge
end. The new segments are kept inside the main rectangle, respect a `spacing_mm`
clearance from the surrounding spirals where possible, and never create a
self-intersection. When the bridge is too short to host the U-turn with the required
clearance, the original bridge is preserved untouched.

## Spiral builder selection

The script picks the builder per region:

- **Centered entry on a 4-edge rectangle** (the entry pair sits in the middle
  band of an edge — within one `spacing_mm` of the edge midpoint) routes to
  `build_centered_snail`, described in the next section. This check runs before
  any other 4-edge handling.
- **Every other region** — 4-edge rectangles with a non-centered entry, and the
  6-, 8- and 12-edge shapes — tries `build_spiral_with_leads` first; if the
  lead-adapter's start/end constraints are not satisfied, it falls back to the
  plain `build_spiral`. The 6-, 8- and 12-edge shapes then apply their
  protrusion detours and the central U-turn on top.
- The 6-edge shape additionally tries `build_left_upper_l_top_right_flip` first
  when `top_right_start_fits_bottom_left_flip` accepts the entry pair.

A centered entry is never quietly routed around by the fallback. If
`build_centered_snail` refuses, the request fails rather than degrading to a
partial route, except where an explicit `allow_partial` is passed.

`build_spiral` still accepts the previously verified reversed and rotated entry
pairs at the legacy `50 mm / 300 mm` defaults; the lead adapter is opt-in via the new
`200 mm / 150 mm` defaults.

## Centered snail

`build_centered_snail` produces a full-coverage route for a rectangle whose pipe
ends enter centered on the bottom or top edge. Request direction is taken from
the optional `spiral_direction` request field (`clockwise` by default,
`counterclockwise` as the alternative).

Both pipe ends start with an initial horizontal run, and both runs point the
**same** direction. The canonical sense of that direction depends on the entry
edge and the requested sense:

| Entry edge | Direction | First horizontal run |
| --- | --- | --- |
| bottom | clockwise | right |
| bottom | counterclockwise | left |
| top | clockwise | right |
| top | counterclockwise | left |

The first two lanes sit exactly at `offset` and at `offset + spacing_mm` from the
entry edge — `bottom + offset` and `bottom + offset + spacing_mm` for a bottom
entry, `top - offset` and `top - offset - spacing_mm` for a top entry.

Top entries are produced by reflecting the canonical layout over the inset
horizontal centre (`y -> bottom + top - y`); the inset rectangle maps onto
itself, only flipped vertically. Counterclockwise adds a reflection over the
inset vertical centre on top of that. Only these reflections distinguish the
four cases, so the bottom clockwise result is the single canonical layout and
the other three are its mirrors.

The body is a **notched ring walk** over the lattice returned by
`build_notched_ring_path`. Concentric rectangular ring cycles are built for
`r = 0 .. min(nx, ny) // 2 - 1` and consecutive rings are merged by a single
2-opt edge splice: on the bottom side for even `r` at column `nx - 3 - r`, on the
top side for odd `r` at column `r + 1`. Because each splice lands away from the
bottom notch, all rings merge into one cycle. A **single** lattice node is then
deleted — the notch `(k - 1, 0)` on the outer bottom row, together with its two
edges — which opens that cycle into one path with ends `left = (k - 2, 0)` and
`right = (k, 0)`. The walk covers every lattice vertex except the notch itself
(`nx * ny - 1` nodes), each vertex exactly once, with degree 1 at the two ends
and degree 2 everywhere else.

The notch must stay clear of the ring `0` splice column, so `k <= nx - 4`; the
builder rejects `k >= nx - 3` instead of degrading. `ny` does not otherwise
affect the topology, only that both dimensions are even.

Both ends sit on lattice row 0, so both leads drop straight down onto the
corresponding inset edge. The graph's left end maps onto the **left** pipe and
its right end onto the **right** pipe: walking out of the left free end the body
first runs *left* along the outer bottom ring, and walking the chain backwards
from the right free end it first runs *right*. The chain is reversed only when
the caller's start is the right pipe. The column and row grids are built by
piecewise linear interpolation between exact anchors (the inset edges, the
inner and outer pipe-end columns, and the first row at one step), and the builder
searches candidate column counts `k` for one where every grid pitch stays within
`5 mm` of the requested `spacing_mm`. When no candidate satisfies that tolerance —
or the region is too small, the ends are not on a shared row, or the lattice
dimensions are otherwise unusable — the build raises `unsupported_orientation`
rather than emitting a distorted layout.

## Verification status

The earlier 21.10.2026 Revit preview of the 7-region batch (376 detail lines)
was **REJECTED** by the user. The pipe ends fed centred on the box middles
rather than off-centre, so the centred snail routed each entry as two
isolated-looking ring nests instead of one continuous snail, and the protrusion
detours that ran inside the body still left visibly unfilled bands inside the
top and right nubs at the `200 mm / 150 mm` defaults. That earlier rejected
376-line preview is preserved here as historical context only and is **not**
the current state.

A new live preview was then run on the same Revit 2022 / PID 15232 /
active view `BIM_Площадка` / view 1320478. State file audit (the prior run's
detail lines were already deleted by the user before this run, so `existing0`
was the pre-preview baseline): `clear` ran first, resetting state to
`deleted0`; `preview` then built clockwise at `offset_mm = 200`,
`spacing_mm = 150` and LEFT 390 `DetailCurves` across the 7 regions:
6833275 = 49, 6833455 = 47, 6833848 = 59, 6836452 = 47, 6836607 = 91,
6837238 = 49, 6837247 = 48. A read-only audit of the actual Revit
`GeometryCurve` endpoints afterward reported `existing 390 / 390`,
`missing 0`, `mismatched 0`, every full region verified, with
`max_joint_gap_mm = 0` and `disconnected_joint_count = 0`. Current run ID:
`bfeec996-1101-4ade-97a3-5271cb0623f0`.

Offline geometry fixes are still active on top of this run: the geometry
uses single-side ring-merge seams (the `build_notched_ring_path` lattice
merges every ring onto the top side, never alternating to the bottom) and
iterative nested U-pairs inside both nub detour functions (`insert_top_nub`,
`insert_right_nub`) so the maximum feasible ladder of pairs fills the whole
nub instead of one wide detour leaving a middle band unheated.

A full-view capture was written to
`%LOCALAPPDATA%/RvtMcp/captures/capture-20261021-152440-d0dbd444 - План этажа - BIM_Площадка.png`,
but its zoom limits visual inspection and the user's visual approval of the
new 390-line preview is **still pending** — it is **not** claimed here.
Geometry offline PNGs from the test run have been inspected.

The previously verified results — before the 12-edge shape — were, in Revit
2022, view `BIM_Площадка`, PID 66528, with the request/result/state JSON files
confirmed:

- rectangle region — `preview` created 27 detail lines, `clear` removed them
- top-nub region (8 edges) — `preview` created 34 detail lines, `clear` removed them
- left-upper wing region (6 edges) — `preview` created 27 detail lines, `clear`
  removed them

## Tests

`test_floor_heating_lab.py` at the repository root runs without Revit and needs no
pyRevit stubs. It imports `geometry.py` directly. `script.py` is never imported,
because it requires `clr`, `dosymep`, `pyrevit`, and calls `main()` on load;
instead the clear-time validator is extracted from it with `ast` and executed in
an isolated namespace, so the tests exercise the real shipped code. The
validator tests cover exact matches, reversed endpoint order, drift inside and
outside the tolerance, and the "one endpoint matches" case that must not be
accepted. The contract tests assert that no Comments parameter is written, that
every created line is recorded with its ID and endpoints, and that all
validation happens before the delete transaction starts. The centered-snail tests
cover all four entry edge / direction combinations: same-direction initial
horizontal runs, lanes at exactly `offset` and `offset + spacing_mm`, absence of
self-crossings, inset coverage, and the reflected top/bottom transforms.

Run from the repository root — the tests import `geometry.py` by relative path,
so the working directory matters. In PowerShell:

```powershell
cd "C:\Users\Mankaev_r\Desktop\_Тесты\_Кастомные скрипты\RevitCustom"
py -2.7-32 -m unittest test_floor_heating_lab
py -m unittest test_floor_heating_lab
```

`cd` changes the current working directory for the rest of the session, so the
`py` calls that follow resolve the test module relative to the repository root;
without it they fail to find `test_floor_heating_lab`. Neither command touches
Revit, the RVT file, or any JSON request/result/state file — they only import
`geometry.py` and run assertions in-process, so they can be run while Revit is
open without any side effects.

Both commands must report the same result on both interpreters. At the time of
writing both report `Ran 69 tests ... OK`.

Note the interpreters are different runtimes: `py -2.7-32` is CPython 2.7 32-bit,
while `py` is CPython 3. Neither is IronPython. The tests are a pre-check for
geometry code that is syntactically loadable on Python 2.7; the actual Revit
execution — including the 21.10.2026 centered-snail run documented in the
Verification status section — is performed through pyRevit, where the script
runs in the Revit/IronPython process.

The first installation of the button requires one pyRevit Reload. MCP Bridge
reaches this button through `custom_execute_pyrevit_command`. For every other
button the bridge keeps posting the pyRevit command, and a `posted` reply only
means Revit accepted the command, not that the script ran. For
`FloorHeatingLab.pushbutton` the bridge instead executes `script.py` inline in an
isolated namespace that supplies `__revit__`, `__file__`, and `__name__`, then
compares the `result-<pid>.json` stamp taken before and after the run. It replies
`executed` only when the result file was actually refreshed, and fails the
operation when the file is missing or unchanged, so a silent no-op can no longer
be reported as success. The standalone plugin implementation will be based on
geometry proven by this harness.

## Safe cleanup

`clear` is guarded and only ever removes lines from the last `preview` run. It
re-checks the document title and path, the view ID, the element class, the
`OwnerViewId`, and the stored endpoints of every line before deleting anything.
All of those checks run before the delete transaction is opened, so if any check
fails the operation aborts and nothing is deleted. A line that is already gone is
skipped rather than treated as an error.

The endpoint comparison is symmetric: a saved segment matches when the live curve
endpoints match the stored pair in either order, because Revit may report the
line's direction reversed. `ENDPOINT_TOLERANCE_MM` is `0.5 mm`, chosen to sit
above Revit's internal 1/1000 ft (about 0.305 mm) modeling precision so ordinary
create-then-read-back rounding does not block a legitimate `clear`, while staying
far below any deliberate edit (Revit's own snap increment is 1/16 in, about
1.59 mm), so a moved line is still refused.

Do not save the RVT while a preview run is active — clear it first, then review
the remaining lines before saving.