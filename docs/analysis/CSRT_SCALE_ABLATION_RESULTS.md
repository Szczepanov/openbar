# CSRT fixed-size diagnostic

2026-10-07. Bounded follow-up to #106/#108 using the [frozen plan](../plans/CSRT_SCALE_ABLATION_PLAN.md).
On nine retained 33-frame injected-translation development sequences, setting OpenCV CSRT
`number_of_scales=1` kept every returned box at its initial dimensions and reduced the equal-case
mean seed-excluded injected-displacement error. It **failed** the predeclared decision rule and
is not a tracker fix or candidate selection.

| Case | Default mean / max px | Fixed-size mean / max px | Paired mean change px | High-confidence errors >3 px default → fixed-size |
| --- | ---: | ---: | ---: | ---: |
| Clean translation | 1.640 / 4.521 | 1.746 / 4.956 | +0.107 | 7 → 6 |
| Snatch translation | 3.733 / 9.280 | 2.505 / 6.196 | -1.227 | 22 → 12 |
| Squat translation | 13.866 / 24.440 | 3.444 / 5.596 | -10.422 | 16 → 16 |
| Clean, whole background | 2.026 / 6.004 | 2.055 / 5.170 | +0.028 | 7 → 7 |
| Snatch, whole background | 4.200 / 6.863 | 2.966 / 6.222 | -1.235 | 22 → 14 |
| Squat, whole background | 5.849 / 12.624 | 3.094 / 5.596 | -2.755 | 28 → 17 |
| Clean, stationary background | 2.737 / 4.859 | 2.739 / 4.937 | +0.002 | 12 → 11 |
| Snatch, stationary background | 6.477 / 11.318 | 4.835 / 10.492 | -1.641 | 27 → 18 |
| Squat, stationary background | 10.492 / 15.715 | 5.082 / 6.908 | -5.410 | 18 → 31 |

All nine runs retained **32/32** post-seed tracked observations in both configurations and paired
on all 32 timestamps. The predeclared rule required at least six strict improvements (passed), an
improvement in each stationary-background case (failed: the clean result changed by +0.002 px),
and no increase in high-confidence wrong observations (failed: stationary squat rose 18 → 31).
The decision is **REJECT_CONFIGURATION**. The equal-case mean displacement error was 5.669 →
3.163 px; high-confidence wrong observations summed to 159 → 132 across the nine cases. These
descriptive aggregates do not replace the per-case decision rule.

## Execution integrity

The first pass is retained as preliminary evidence because independent review found its runner did
not pin the registration file hash separately from the registration contents. That pass was never
used for the final decision. Its runner, registration, inputs and outputs are preserved under the
private handoff, and `amendment.json` records the issue and hashes.

The official pass and its repeat used a fresh freeze tied to the amendment and required the
registration SHA-256 on each run. The baseline stream exactly matched historical predictions on
all nine cases, including confidence and provenance. All 86 frozen source/input hashes were still
unchanged after each run. The official comparison and repeat match across **56 runtime-free JSON
artifacts**; decoder timestamps match the 33-sample injected program. OpenCV 4.12.0 and NumPy
2.2.6 were reused from the retained CPU environment. The existing research suite passed **101**
tests; validation passed **211** tests and all **12** mapped schemas; focused runner tests passed.

## Limits and decision

This changes the scale-search response, which can also affect location learning. Constant returned
box size does not prove the error reduction came only from removing size drift. The sequences use
injected synthetic translations, confirmed seed appearance and artificial whole/stationary
background constructions. This result establishes neither natural-footage absolute plate-centre
accuracy, a physical scale correction, nor velocity accuracy.

Keep the result as a rejected development diagnostic. Do not change CSRT defaults, select a
production tracker/filter, or loosen #57/#58/#53/#59 gates from these data. Continue with existing
reporting; further tracker selection still requires its prescribed held-out annotations and a
separate independent physical-reference study for kinematic accuracy.
