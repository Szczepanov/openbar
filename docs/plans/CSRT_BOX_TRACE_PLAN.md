# Frozen CSRT box trace

2026-10-05; follows merged #105. Diagnose before changing any producer.
One retained failing case: squat pure whole-frame translation, 33 frames, confirmed seed
and actual PTS from the original controlled-motion registration. No new media or labels.

1. Verify the original artifact inventory and input/helper hashes. Reuse `track.track` in
   the existing OpenCV 4.12.0 / NumPy 2.2.6 environment with identical parameters.
2. Wrap its existing CSRT constructor observationally: forward each `init` and `update`
   unchanged while recording raw returned boxes and success flags. Keep the predictor
   untouched and require its complete runtime-free prediction to equal the retained one.
3. Align boxes to actual decoded PTS. Decompose seed-relative centre residual per axis into
   top-left residual plus half the width/height change plus the fixed seed-box rounding
   offset. Verify their sum against every emitted centre. Retain seed-box-relative residual
   separately so rounding cannot be called drift. Lost output remains coordinate-free.
4. Report first >3 px error, high-confidence wrong support, returned box-size range, location
   versus size terms at first error/max error/final frame, and equality of independent
   pixel-centre conversion/scoring. Terms are algebraic decomposition, not a causal proof
   that disabling scale updates would fix tracking. Do not read proprietary implementations.
5. Repeat once and require byte-identical box/prediction/derived reports; retain original
   input hashes and runnable decomposition/loss/finite/input-preservation checks.

Stop after this trace and directly justified root-cause fix, if any. A returned wrong box
without a wrapper/conversion defect is an algorithm limitation, not a reason to tune CSRT.
Only a proven shared implementation defect permits a minimal fix plus regression and
affected existing real-video reruns. Preserve #103 destination cleanup/retry semantics.

Then freeze one separate paired target/background-motion experiment before generating or
scoring it. No new human work, environment installation, held-out access, parameter search,
production tracker/filter selection or physical velocity claim.
