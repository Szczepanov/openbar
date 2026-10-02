import json
from pathlib import Path
import statistics


def evaluate_diameters():
    print("=" * 80)
    print("P3.0 ANNOTATION-ORACLE STUDY: S0 vs S1 vs S2")
    print("=" * 80)

    # Gather all development and validation annotation files that have seeds
    seeds_dir = Path("validation/private/seeds")
    ann_dir = Path("validation/private/annotations")

    ann_files = sorted(ann_dir.glob("*.annotation-v1.json"))

    results = []

    for af in ann_files:
        with af.open(encoding="utf-8") as f:
            adata = json.load(f)

        fid = adata.get("fixture_id")
        seed_path = seeds_dir / f"{fid}.manual-target-seed-v1.json"
        if not seed_path.exists():
            continue

        with seed_path.open(encoding="utf-8") as sf:
            sdata = json.load(sf)

        seed_target = sdata.get("seed", {}).get("target", {})
        seed_radius = seed_target.get("radius_px")
        if seed_radius is None:
            continue
        seed_diameter = 2.0 * seed_radius

        samples = adata.get("samples", [])
        radii = []
        for s in samples:
            tsz = s.get("target_size_px")
            if tsz and "radius_px" in tsz:
                radii.append(tsz["radius_px"])
            elif tsz and "diameter_px" in tsz:
                radii.append(tsz["diameter_px"] / 2.0)

        if not radii:
            continue

        diameters = [2.0 * r for r in radii]
        distinct_diameters = sorted(list(set(diameters)))

        # S0: seed diameter
        s0 = seed_diameter

        # S1: median diameter
        s1 = statistics.median(diameters)

        # S2: trimmed mean (middle 50%) or mean
        sorted_d = sorted(diameters)
        q1 = int(len(sorted_d) * 0.25)
        q3 = int(len(sorted_d) * 0.75)
        if q3 > q1:
            s2 = sum(sorted_d[q1:q3]) / (q3 - q1)
        else:
            s2 = statistics.median(diameters)

        # Relative scale changes:
        # scale s = D_m / D_px
        # scale_ratio k = s_cand / s0 = D_seed / D_cand
        k1 = s0 / s1
        k2 = s0 / s2

        # Diameter spread
        d_min = min(diameters)
        d_max = max(diameters)
        d_spread = d_max - d_min
        d_std = statistics.stdev(diameters) if len(diameters) > 1 else 0.0
        cv_d = (d_std / s1) if s1 > 0 else 0.0

        results.append({
            "fixture_id": fid,
            "annotator": adata.get("provenance", {}).get("annotator_id"),
            "sample_count": len(samples),
            "diameters_count": len(diameters),
            "distinct_count": len(distinct_diameters),
            "s0_seed_diameter_px": s0,
            "s1_median_diameter_px": s1,
            "s2_trimmed_diameter_px": s2,
            "min_diameter_px": d_min,
            "max_diameter_px": d_max,
            "diameter_spread_px": d_spread,
            "diameter_cv": cv_d,
            "k1_scale_ratio": k1,
            "k2_scale_ratio": k2,
            "relative_scale_change_s1_pct": (k1 - 1.0) * 100.0,
            "relative_scale_change_s2_pct": (k2 - 1.0) * 100.0,
        })

    for r in results:
        print(f"\n[{r['fixture_id']}] (annotator: {r['annotator']})")
        print(
            f"  Samples: {r['sample_count']}, distinct radii: {r['distinct_count']}"
        )
        print(f"  S0 (seed diameter):    {r['s0_seed_diameter_px']:.2f} px")
        print(
            f"  S1 (median diameter):  {r['s1_median_diameter_px']:.2f} px -> k1={r['k1_scale_ratio']:.4f} (scale delta: {r['relative_scale_change_s1_pct']:+.2f}%)"
        )
        print(
            f"  S2 (trimmed diameter): {r['s2_trimmed_diameter_px']:.2f} px -> k2={r['k2_scale_ratio']:.4f} (scale delta: {r['relative_scale_change_s2_pct']:+.2f}%)"
        )
        print(
            f"  Range: [{r['min_diameter_px']:.2f} .. {r['max_diameter_px']:.2f}] px (spread={r['diameter_spread_px']:.2f} px, CV={r['diameter_cv']*100:.2f}%)"
        )

    # Serialize
    out_path = Path("target/research/calibration/p3_annotation_oracle_study.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nWrote oracle study artifact to {out_path}")


if __name__ == "__main__":
    evaluate_diameters()
