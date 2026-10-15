# label-triage (research only)

Compares three independent plate-centre methods against each other, lists the frames a human should
label (where they disagree or lose the plate, plus a seeded random sample), and measures how often
their agreement lands within the 3 px gate of existing human labels. **Agreement is not ground
truth.** This tool changes no definition of truth, no schema and no canonical output.

Voters: `bootstapir-affine` (learned point tracking), `lk-affine` (classical optical flow),
`sam2.1-bplus-circle` (learned segmentation plus circle fit). CSRT is excluded: the clean-room
candidate under #57 is CSRT-class, so its errors would correlate. The voters are also #57 bake-off
candidates, so this tool scores none of them.

```bash
# development fixtures only; held-out fixtures are refused with no override
python research/label-triage/triage_cli.py run-voters --repo-root <repo> --manifest <manifest> \
  --fixture <id> --seed <seed.json> --out-dir <dir> --cpu-python <opencv venv> --gpu-python <gpu venv>
python research/label-triage/triage_cli.py table     --job job.json --out-dir out
python research/label-triage/triage_cli.py triage    --job job.json --out-dir out --fraction 0.1 --seed 20261015
python research/label-triage/triage_cli.py calibrate --job job.json --out out/calibration.json
python -m unittest discover -s research/label-triage/tests -p 'test_*.py'
```

Fixed before any data: spread limit 3 px, gate 3 px, random sample 10 % (override with `--fraction`).
The labeller's `triage_list.csv` carries no reasons; they are kept in `triage_reasons.csv`.
A lost or missing voter observation carries no coordinate and is never interpolated.
Calibration reports counts and 95 % Wilson intervals only. Frames of one clip are correlated, so
pooled intervals do not describe unseen clips.
