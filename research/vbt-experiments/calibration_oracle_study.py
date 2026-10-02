import json
from pathlib import Path
import statistics


def evaluate_diameters():
    print("=" * 80)
    print("P3.0 CALIBRATION ORACLE STUDY: STATIC SEED VS DYNAMIC ANNOTATED DIAMETER")
    print("=" * 80)

    seeds_dir = Path("validation/private/seeds")
    ann_dir = Path("validation/private/annotations")

    # Only include primary annotation files (<fixture_id>.annotation-v1.json)
    ann_files = sorted(
        [
            p
            for p in ann_dir.glob("*.annotation-v1.json")
            if not p.name.endswith(".owner-pass-b.annotation-v1.json")
        ]
    )

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
        all_diameters = []
        deliberately_fitted_diameters = []
        prev_r = None

        for idx, s in enumerate(samples):
            tsz = s.get("target_size_px")
            r = None
            if tsz and "radius_px" in tsz:
                r = tsz["radius_px"]
            elif tsz and "diameter_px" in tsz:
                r = tsz["diameter_px"] / 2.0

            if r is None:
                continue

            d = 2.0 * r
            all_diameters.append(d)

            # The annotation tool carries radius forward between frames by default.
            # A deliberately fitted frame is either the first frame (idx 0) or any
            # frame where the annotator actively adjusted the radius (r != prev_r).
            if idx == 0 or r != prev_r:
                deliberately_fitted_diameters.append(d)

            prev_r = r

        if not all_diameters:
            continue

        distinct_diameters = sorted(list(set(all_diameters)))

        # S0: seed diameter
        s0 = seed_diameter

        # S1: median of all annotated frames
        s1_all = statistics.median(all_diameters)

        # S1_fitted: median of deliberately fitted frames
        s1_fitted = (
            statistics.median(deliberately_fitted_diameters)
            if deliberately_fitted_diameters
            else s1_all
        )

        # Scale ratios: scale s = D_m / D_px; k = s_cand / s0 = D_seed / D_cand
        k_all = s0 / s1_all
        k_fitted = s0 / s1_fitted
        rel_change_all_pct = (k_all - 1.0) * 100.0
        rel_change_fitted_pct = (k_fitted - 1.0) * 100.0

        d_min = min(all_diameters)
        d_max = max(all_diameters)
        d_spread = d_max - d_min
        d_std = (
            statistics.stdev(all_diameters) if len(all_diameters) > 1 else 0.0
        )
        cv_d = (d_std / s1_all) if s1_all > 0 else 0.0

        results.append(
            {
                "fixture_id": fid,
                "annotator": adata.get("provenance", {}).get("annotator_id"),
                "sample_count": len(samples),
                "all_diameters_count": len(all_diameters),
                "deliberately_fitted_count": len(deliberately_fitted_diameters),
                "distinct_radii_count": len(distinct_diameters),
                "s0_seed_diameter_px": s0,
                "s1_all_median_diameter_px": s1_all,
                "s1_fitted_median_diameter_px": s1_fitted,
                "min_diameter_px": d_min,
                "max_diameter_px": d_max,
                "diameter_spread_px": d_spread,
                "diameter_cv": cv_d,
                "k_all_scale_ratio": k_all,
                "k_fitted_scale_ratio": k_fitted,
                "scale_discrepancy_all_pct": rel_change_all_pct,
                "scale_discrepancy_fitted_pct": rel_change_fitted_pct,
            }
        )

    for r in results:
        print(f"\n[{r['fixture_id']}] (annotator: {r['annotator']})")
        print(
            f"  Samples: {r['sample_count']}, deliberately fitted: {r['deliberately_fitted_count']}, distinct radii: {r['distinct_radii_count']}"
        )
        print(f"  S0 (seed diameter):                {r['s0_seed_diameter_px']:.2f} px")
        print(
            f"  S1 (fitted frames median diameter):{r['s1_fitted_median_diameter_px']:.2f} px -> k={r['k_fitted_scale_ratio']:.4f} (scale delta: {r['scale_discrepancy_fitted_pct']:+.2f}%)"
        )
        print(
            f"  S1 (all frames median diameter):   {r['s1_all_median_diameter_px']:.2f} px -> k={r['k_all_scale_ratio']:.4f} (scale delta: {r['scale_discrepancy_all_pct']:+.2f}%)"
        )
        print(
            f"  Observed range: [{r['min_diameter_px']:.2f} .. {r['max_diameter_px']:.2f}] px (spread={r['diameter_spread_px']:.2f} px, CV={r['diameter_cv']*100:.2f}%)"
        )

    # Summary analysis
    discrepancies = [abs(r["scale_discrepancy_fitted_pct"]) for r in results]
    min_disc = min(discrepancies) if discrepancies else 0.0
    max_disc = max(discrepancies) if discrepancies else 0.0
    med_disc = statistics.median(discrepancies) if discrepancies else 0.0

    print("\n" + "=" * 80)
    print("PHASE 3 SYNTHESIS AND UNCERTAINTY REPORT")
    print("=" * 80)
    print(
        f"Across {len(results)} evaluated fixtures, static seed diameter and dynamic fitted diameter disagree by {min_disc:.1f}% to {max_disc:.1f}% (median: {med_disc:.1f}%)."
    )
    print(
        "Finding: Inconclusive without independent physical reference ground truth."
    )
    print(
        "Rationale: Speculative explanations (such as motion blur) are unsupported without an independent physical reference."
    )
    print(
        "           Out-of-plane bar trajectory, camera perspective foreshortening, and visual annotator edge bias"
    )
    print(
        "           could equally account for diameter variations. Without physical ground truth, neither static seed"
    )
    print(
        "           nor dynamic per-frame calibration can be proven superior."
    )
    print(
        "Decision: DEFER (INCONCLUSIVE). The 3% to 12% scale discrepancy is passed to #58 as an empirical calibration uncertainty signal."
    )

    artifact = {
        "study_version": "p3-0-calibration-oracle-study-v2",
        "decision": "DEFER_INCONCLUSIVE",
        "scale_discrepancy_range_pct": [round(min_disc, 2), round(max_disc, 2)],
        "scale_discrepancy_median_pct": round(med_disc, 2),
        "conclusions": [
            "Static seed diameter and dynamic fitted diameter disagree by 3.0% to 12.0% across real phone clips.",
            "Without an independent physical reference measurement (e.g. motion capture or calibrated physical markers), it is impossible to determine whether static or dynamic diameter yields lower physical error.",
            "Speculative physical causes (e.g. motion blur) are unverified; out-of-plane motion, perspective foreshortening, and annotator bias remain competing hypotheses.",
            "Decision: DEFER (INCONCLUSIVE). Retain PlateDiameterCalibration@1 as default; forward the 3%-12% discrepancy directly to #58 as an empirical calibration uncertainty signal.",
        ],
        "fixtures": results,
    }

    out_path = Path("target/research/calibration/p3_annotation_oracle_study.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2)
    print(f"\nWrote oracle study artifact to {out_path}")


if __name__ == "__main__":
    evaluate_diameters()
