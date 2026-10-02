import json
import math
import subprocess
from pathlib import Path

def compute_regularity(dt_list):
    if len(dt_list) < 1:
        return None
    sample_count = len(dt_list) + 1
    interval_count = len(dt_list)
    mean_dt = sum(dt_list) / interval_count
    sorted_dt = sorted(dt_list)
    mid = interval_count // 2
    if interval_count % 2 == 1:
        med_dt = sorted_dt[mid]
    else:
        med_dt = (sorted_dt[mid - 1] + sorted_dt[mid]) / 2.0
    min_dt = sorted_dt[0]
    max_dt = sorted_dt[-1]
    abs_devs = [abs(x - med_dt) for x in dt_list]
    max_abs_dev = max(abs_devs)
    max_rel_dev = max_abs_dev / med_dt if med_dt > 0 else 0.0
    var = sum((x - mean_dt) ** 2 for x in dt_list) / interval_count
    std_dev = math.sqrt(var)
    cv = std_dev / mean_dt if mean_dt > 0 else 0.0

    return {
        "sample_count": sample_count,
        "interval_count": interval_count,
        "mean_dt_s": mean_dt,
        "median_dt_s": med_dt,
        "min_dt_s": min_dt,
        "max_dt_s": max_dt,
        "max_abs_dev_s": max_abs_dev,
        "max_rel_dev": max_rel_dev,
        "std_dev_s": std_dev,
        "cv": cv,
    }

def probe_clip(media_path):
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=time_base,start_pts:frame=pts",
        "-of", "json", str(media_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {media_path}: {res.stderr}")
    data = json.loads(res.stdout)
    stream = data.get("streams", [{}])[0]
    tb = stream.get("time_base", "1/1000")
    num, den = map(int, tb.split("/"))
    start_pts = stream.get("start_pts")
    if start_pts is not None:
        start_pts = int(start_pts)
    else:
        start_pts = 0

    frames = data.get("frames", [])
    pts_list = [f["pts"] for f in frames if "pts" in f]
    if len(pts_list) < 2:
        return None

    timestamps = [((p - start_pts) * num) / den for p in pts_list]
    tick_deltas = [pts_list[i] - pts_list[i - 1] for i in range(1, len(pts_list))]
    sec_deltas = [timestamps[i] - timestamps[i - 1] for i in range(1, len(timestamps))]

    return {
        "time_base": tb,
        "time_base_num": num,
        "time_base_den": den,
        "pts_list": pts_list,
        "timestamps": timestamps,
        "tick_deltas": tick_deltas,
        "sec_deltas": sec_deltas,
    }

def analyze_dataset():
    manifests = [
        ("validation/fixtures/public/manifest.json", "public"),
        ("validation/private/manifest.json", "private"),
    ]

    fixtures = []
    for mpath, mkind in manifests:
        p = Path(mpath)
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as f:
            mdata = json.load(f)
        for fix in mdata.get("fixtures", []):
            fixtures.append((mkind, fix))

    print(f"Loaded {len(fixtures)} fixtures total across public and private manifests.\n")

    all_results = []

    for mkind, fix in fixtures:
        fid = fix["id"]
        purpose = fix.get("purpose")


        media_rel = fix.get("media", {}).get("repository_path")
        media_path = Path(media_rel)
        if not media_path.exists():
            print(f"Skipping {fid}: media not found at {media_path}")
            continue

        probe = probe_clip(media_path)
        if not probe:
            print(f"Skipping {fid}: could not probe media")
            continue

        # Look for seed
        seed_path = None
        if mkind == "public":
            candidate = Path(f"validation/fixtures/public/seeds/{fid}.manual-target-seed-v1.json")
            if candidate.exists():
                seed_path = candidate
        else:
            candidate = Path(f"validation/private/seeds/{fid}.manual-target-seed-v1.json")
            if candidate.exists():
                seed_path = candidate

        seed_time = None
        if seed_path:
            with seed_path.open(encoding="utf-8") as f:
                sdata = json.load(f)
            seed_time = sdata.get("seed", {}).get("timestamp_s")

        # Look for annotations (lift segment)
        ann_path = None
        if mkind == "public":
            candidate = Path(f"validation/fixtures/public/annotations/{fid}.annotation-v1.json")
            if candidate.exists():
                ann_path = candidate
        else:
            candidate = Path(f"validation/private/annotations/{fid}.annotation-v1.json")
            if candidate.exists():
                ann_path = candidate

        annotated_span = None
        if ann_path:
            with ann_path.open(encoding="utf-8") as f:
                adata = json.load(f)
            samples = adata.get("samples", [])
            if samples:
                t_start = samples[0]["timestamp_s"]
                t_end = samples[-1]["timestamp_s"]
                annotated_span = (t_start, t_end)

        # Entire clip regularity
        all_reg = compute_regularity(probe["sec_deltas"])
        tick_min = min(probe["tick_deltas"])
        tick_max = max(probe["tick_deltas"])
        tick_span = tick_max - tick_min

        # Tracked segment: from seed_time onward (if seed exists)
        tracked_reg = None
        tracked_tick_span = None
        tracked_tick_min = None
        tracked_tick_max = None
        if seed_time is not None:
            # find index closest to seed_time
            idx = 0
            min_diff = float("inf")
            for i, t in enumerate(probe["timestamps"]):
                diff = abs(t - seed_time)
                if diff < min_diff:
                    min_diff = diff
                    idx = i
            sub_sec = probe["sec_deltas"][idx:]
            sub_ticks = probe["tick_deltas"][idx:]
            if len(sub_sec) >= 1:
                tracked_reg = compute_regularity(sub_sec)
                tracked_tick_min = min(sub_ticks)
                tracked_tick_max = max(sub_ticks)
                tracked_tick_span = tracked_tick_max - tracked_tick_min

        # Annotated lift segment
        ann_reg = None
        ann_tick_span = None
        if annotated_span is not None:
            t0, t1 = annotated_span
            sub_sec = []
            sub_ticks = []
            for i in range(len(probe["timestamps"]) - 1):
                t = probe["timestamps"][i]
                if t0 - 0.001 <= t <= t1 + 0.001:
                    sub_sec.append(probe["sec_deltas"][i])
                    sub_ticks.append(probe["tick_deltas"][i])
            if len(sub_sec) >= 1:
                ann_reg = compute_regularity(sub_sec)
                ann_tick_span = max(sub_ticks) - min(sub_ticks)

        # Decompose into contiguous segments where dt is within 1.5x median
        med_tick = sorted(probe["tick_deltas"])[len(probe["tick_deltas"]) // 2]
        contiguous_segments = []
        cur_ticks = []
        cur_sec = []
        for i, dt in enumerate(probe["sec_deltas"]):
            t_val = probe["tick_deltas"][i]
            if abs(t_val - med_tick) <= 2:  # normal frame spacing
                cur_ticks.append(t_val)
                cur_sec.append(dt)
            else:
                if len(cur_sec) >= 2:
                    contiguous_segments.append((compute_regularity(cur_sec), max(cur_ticks) - min(cur_ticks)))
                cur_ticks = []
                cur_sec = []
        if len(cur_sec) >= 2:
            contiguous_segments.append((compute_regularity(cur_sec), max(cur_ticks) - min(cur_ticks)))

        all_results.append({
            "fixture_id": fid,
            "media": media_path.name,
            "purpose": purpose,
            "exercise": fix.get("exercise"),
            "nominal_fps": fix.get("video", {}).get("nominal_fps"),
            "measured_fps": fix.get("video", {}).get("measured_fps"),
            "device_notes": fix.get("source", {}).get("reference", ""),
            "tags": fix.get("conditions", {}).get("challenge_tags", []),
            "time_base": probe["time_base"],
            "all_reg": all_reg,
            "tick_span": tick_span,
            "tick_min": tick_min,
            "tick_max": tick_max,
            "med_tick": med_tick,
            "seed_time": seed_time,
            "tracked_reg": tracked_reg,
            "tracked_tick_span": tracked_tick_span,
            "tracked_tick_min": tracked_tick_min,
            "tracked_tick_max": tracked_tick_max,
            "annotated_span": annotated_span,
            "ann_reg": ann_reg,
            "ann_tick_span": ann_tick_span,
            "contiguous_segments_count": len(contiguous_segments),
            "contiguous_segments": contiguous_segments,
        })

    dev_results = [r for r in all_results if r["purpose"] == "development"]
    val_results = [r for r in all_results if r["purpose"] == "validation"]

    evaluate_cohort("development", dev_results, Path("target/research/butterworth/timestamp_survey_dev.json"))
    evaluate_cohort("validation", val_results, Path("target/research/butterworth/timestamp_survey_val.json"))


def evaluate_cohort(cohort_name: str, cohort_results: list, out_path: Path):
    print("\n" + "=" * 80)
    print(f"{cohort_name.upper()} CLIPS TIMESTAMP REGULARITY SURVEY (P2.0)")
    print("=" * 80)
    for r in cohort_results:
        print(f"\n--- Fixture: {r['fixture_id']} ({r['media']}) ---")
        print(f"  Exercise: {r['exercise']}, Nominal FPS: {r['nominal_fps']}, Measured: {r['measured_fps']}")
        print(f"  Device/Source: {r['device_notes']}")
        print(f"  Tags: {r['tags']}")
        print(f"  Time base: {r['time_base']}")
        ar = r["all_reg"]
        print(f"  [Whole Clip] {ar['sample_count']} frames, median dt: {ar['median_dt_s']:.6f}s ({1.0/ar['median_dt_s']:.2f} Hz)")
        print(f"    Ticks: med={r['med_tick']}, min={r['tick_min']}, max={r['tick_max']}, span={r['tick_span']}")
        print(f"    Deltas: min={ar['min_dt_s']:.6f}s, max={ar['max_dt_s']:.6f}s")
        print(f"    Max rel dev: {ar['max_rel_dev']:.6f} ({ar['max_rel_dev']*100:.3f}%)")
        print(f"    CV (jitter): {ar['cv']:.6f} ({ar['cv']*100:.3f}%)")

        if r["tracked_reg"]:
            tr = r["tracked_reg"]
            print(f"  [Tracked Segment from seed t={r['seed_time']}s] {tr['sample_count']} frames")
            print(f"    Ticks: min={r['tracked_tick_min']}, max={r['tracked_tick_max']}, span={r['tracked_tick_span']}")
            print(f"    Max rel dev: {tr['max_rel_dev']:.6f} ({tr['max_rel_dev']*100:.3f}%)")
            print(f"    CV: {tr['cv']:.6f} ({tr['cv']*100:.3f}%)")

        if r["ann_reg"]:
            an = r["ann_reg"]
            print(f"  [Annotated Lift Interval {r['annotated_span'][0]:.3f}-{r['annotated_span'][1]:.3f}s] {an['sample_count']} frames")
            print(f"    Ticks: span={r['ann_tick_span']}")
            print(f"    Max rel dev: {an['max_rel_dev']:.6f} ({an['max_rel_dev']*100:.3f}%)")
            print(f"    CV: {an['cv']:.6f} ({an['cv']*100:.3f}%)")

        print(f"  [Contiguous Segments without drops (>2 ticks dev)]: {r['contiguous_segments_count']} segments")
        for si, (sreg, sspan) in enumerate(r['contiguous_segments'][:3]):
            print(f"    Seg {si+1}: {sreg['sample_count']} frames, tick_span={sspan}, rel_dev={sreg['max_rel_dev']*100:.3f}%, cv={sreg['cv']*100:.3f}%")

    print("\n" + "=" * 80)
    print(f"RULE APPLICABILITY EVALUATION ON {cohort_name.upper()} CLIPS")
    print("=" * 80)

    rules = [
        ("Strict (exact CFR or container rounding: tick span <= 1)",
         lambda r, seg: (r["tracked_tick_span" if seg else "tick_span"] <= 1)),
        ("Low-jitter 1% (rel_dev <= 0.01)",
         lambda r, seg: ((r["tracked_reg" if seg else "all_reg"]["max_rel_dev"] <= 0.01))),
        ("Low-jitter 5% (rel_dev <= 0.05)",
         lambda r, seg: ((r["tracked_reg" if seg else "all_reg"]["max_rel_dev"] <= 0.05))),
        ("Low-jitter 10% (rel_dev <= 0.10)",
         lambda r, seg: ((r["tracked_reg" if seg else "all_reg"]["max_rel_dev"] <= 0.10))),
        ("Low-jitter CV 1% (cv <= 0.01)",
         lambda r, seg: ((r["tracked_reg" if seg else "all_reg"]["cv"] <= 0.01))),
        ("Low-jitter CV 2% (cv <= 0.02)",
         lambda r, seg: ((r["tracked_reg" if seg else "all_reg"]["cv"] <= 0.02))),
        ("Low-jitter CV 5% (cv <= 0.05)",
         lambda r, seg: ((r["tracked_reg" if seg else "all_reg"]["cv"] <= 0.05))),
    ]

    print(f"\n1. WHOLE CLIPS APPLICABILITY (N = {len(cohort_results)} {cohort_name} clips):")
    for rname, rfunc in rules:
        passed = [r["fixture_id"] for r in cohort_results if rfunc(r, False)]
        pct = len(passed) / len(cohort_results) * 100 if cohort_results else 0.0
        print(f"  {rname:65}: {len(passed)}/{len(cohort_results)} ({pct:5.1f}%) -> {passed}")

    seeded_cohort = [r for r in cohort_results if r["tracked_reg"] is not None]
    print(f"\n2. TRACKED SEGMENTS (SEED TO END) APPLICABILITY (N = {len(seeded_cohort)} seeded {cohort_name} clips):")
    for rname, rfunc in rules:
        passed = [r["fixture_id"] for r in seeded_cohort if rfunc(r, True)]
        pct = len(passed) / len(seeded_cohort) * 100 if seeded_cohort else 0.0
        print(f"  {rname:65}: {len(passed)}/{len(seeded_cohort)} ({pct:5.1f}%) -> {passed}")

    all_contiguous = [seg for r in cohort_results for seg in r["contiguous_segments"]]
    print(f"\n3. CONTIGUOUS DROP-FREE SEGMENTS APPLICABILITY (N = {len(all_contiguous)} segments):")
    for rname, rfunc in [
        ("Strict (tick span <= 1)", lambda s: s[1] <= 1),
        ("Low-jitter 1% (rel_dev <= 0.01)", lambda s: s[0]["max_rel_dev"] <= 0.01),
        ("Low-jitter 5% (rel_dev <= 0.05)", lambda s: s[0]["max_rel_dev"] <= 0.05),
        ("Low-jitter CV 1% (cv <= 0.01)", lambda s: s[0]["cv"] <= 0.01),
    ]:
        passed_c = [s for s in all_contiguous if rfunc(s)]
        pct = len(passed_c) / len(all_contiguous) * 100 if all_contiguous else 0.0
        print(f"  {rname:65}: {len(passed_c)}/{len(all_contiguous)} ({pct:5.1f}%)")

    summary_data = {
        "survey_version": "p2-0-timestamp-regularity-survey-v1",
        "cohort": cohort_name,
        "fixtures_count": len(cohort_results),
        "seeded_fixtures_count": len(seeded_cohort),
        "total_drop_free_contiguous_segments": len(all_contiguous),
        "qualification_caveat": "Note: Measured qualification reflects container-level video frame PTS regularity. Real tracker observation streams contain tracking dropouts; any gap > 1.5x median dt violates the Butterworth regularity threshold and causes segment/run rejection.",
        "results": [
            {
                "fixture_id": r["fixture_id"],
                "media": r["media"],
                "device_notes": r["device_notes"],
                "nominal_fps": r["nominal_fps"],
                "measured_fps": r["measured_fps"],
                "time_base": r["time_base"],
                "all_clip_regularity": r["all_reg"],
                "all_clip_tick_span": r["tick_span"],
                "tracked_regularity": r["tracked_reg"],
                "tracked_tick_span": r["tracked_tick_span"],
                "ann_regularity": r["ann_reg"],
                "ann_tick_span": r["ann_tick_span"],
                "contiguous_drop_free_segments_count": r["contiguous_segments_count"],
            }
            for r in cohort_results
        ]
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)
    print(f"\nWrote {cohort_name} survey artifact to {out_path}")

if __name__ == "__main__":
    analyze_dataset()
