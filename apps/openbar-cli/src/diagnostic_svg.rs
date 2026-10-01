//! Deterministic SVG diagnostics built exclusively from canonical analysis values.

use openbar_core::analysis::{Analysis, TrackingState};
use openbar_core::trajectory::{KinematicSample, MetricPositionSample};
use std::fmt::Write as _;
use std::path::{Path, PathBuf};

pub(crate) const RENDERER_ID: &str = "diagnostic-svg";
pub(crate) const RENDERER_VERSION: &str = "1";

const W: f64 = 1200.0;
const X: f64 = 50.0;
const PW: f64 = 1100.0;
const PH: f64 = 180.0;

pub(crate) struct SourceFrame {
    pub timestamp_s: f64,
    pub frame_index: u64,
    pub source_path: PathBuf,
    pub png_data_uri: String,
}

pub(crate) fn render_svg(
    analysis: &Analysis,
    analysis_path: &Path,
    frame: Option<&SourceFrame>,
) -> String {
    let mut out = String::with_capacity(48 * 1024);
    writeln!(out, r#"<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1900" viewBox="0 0 1200 1900">"#).unwrap();
    out.push_str(r#"<style>text{font-family:monospace;fill:#111}.t{font-size:25px;font-weight:700}.h{font-size:17px;font-weight:700}.s{font-size:12px}.p{fill:#fafafa;stroke:#333}.g{stroke:#ddd}.r{fill:none;stroke:#1565c0;stroke-width:3}.f{fill:none;stroke:#ef6c00;stroke-width:3;stroke-dasharray:10 7}.seed{fill:none;stroke:#6a1b9a;stroke-width:3}.low{fill:#fff;stroke:#c62828;stroke-width:2}.lost{stroke:#c62828;stroke-width:3}</style>
"#);
    metadata(&mut out, analysis, analysis_path, frame);

    text(&mut out, X, 42.0, "OpenBar diagnostic report", "t");
    text(
        &mut out,
        X,
        66.0,
        &format!(
            "source={} | analysis-v{} | renderer={}@{} | display={}x{} rotation={}°",
            analysis.identity().source_id,
            analysis.schema_version(),
            RENDERER_ID,
            RENDERER_VERSION,
            analysis.video().display_width_px,
            analysis.video().display_height_px,
            analysis.video().source_rotation_deg
        ),
        "s",
    );

    let mut y = spatial(&mut out, analysis, frame, 95.0) + 26.0;
    y = position(&mut out, analysis, Axis::X, y) + 22.0;
    y = position(&mut out, analysis, Axis::Y, y) + 22.0;
    y = velocity(&mut out, analysis, Axis::X, y) + 22.0;
    y = velocity(&mut out, analysis, Axis::Y, y) + 22.0;
    y = confidence(&mut out, analysis, y) + 24.0;
    text(&mut out, X, y, "solid=calibrated/raw; dashed=filtered; red ×=lost; hollow red=low confidence", "s");
    text(&mut out, X, y + 17.0, "Rendering never runs tracking/filtering/calibration/kinematics and never interpolates missing spans.", "s");
    out.push_str("</svg>\n");
    out
}

fn metadata(out: &mut String, analysis: &Analysis, path: &Path, frame: Option<&SourceFrame>) {
    writeln!(out, "<metadata>").unwrap();
    writeln!(out, "renderer={RENDERER_ID}@{RENDERER_VERSION}").unwrap();
    writeln!(out, "analysis_path={}", esc(&path.display().to_string())).unwrap();
    writeln!(out, "source_id={}", esc(&analysis.identity().source_id)).unwrap();
    writeln!(out, "source_sha256={}", analysis.identity().source_sha256.as_deref().unwrap_or("not-recorded")).unwrap();
    writeln!(out, "pipeline={} git_commit={}", esc(&analysis.provenance().pipeline.openbar_version), esc(analysis.provenance().pipeline.git_commit.as_deref().unwrap_or("not-recorded"))).unwrap();
    if let Some(frame) = frame {
        writeln!(out, "source_video={} frame_timestamp_s={:.9} frame_index={}", esc(&frame.source_path.display().to_string()), frame.timestamp_s, frame.frame_index).unwrap();
    }
    writeln!(out, "</metadata>").unwrap();
}

fn spatial(out: &mut String, analysis: &Analysis, frame: Option<&SourceFrame>, top: f64) -> f64 {
    text(out, X, top, "Spatial trajectory overlay", "h");
    let vw = f64::from(analysis.video().display_width_px);
    let vh = f64::from(analysis.video().display_height_px);
    let scale = (PW / vw).min(430.0 / vh);
    let w = vw * scale;
    let h = vh * scale;
    let left = X + (PW - w) / 2.0;
    let y = top + 14.0;
    rect(out, left, y, w, h, "p");
    if let Some(frame) = frame {
        writeln!(out, r#"<image data-layer="source-frame" x="{left:.3}" y="{y:.3}" width="{w:.3}" height="{h:.3}" href="{}" preserveAspectRatio="none"/>"#, frame.png_data_uri).unwrap();
    } else {
        text(out, left + 10.0, y + 20.0, "source frame not supplied", "s");
    }

    for segment in raw_segments(analysis) {
        pixel_path(out, &segment, left, y, scale, "r", "raw-trajectory");
    }
    if let Some(filtered) = analysis.derived().filtered.as_ref() {
        let mpp = analysis.calibration().scale().metres_per_pixel();
        let origin = analysis.manual_seed().target().center();
        for segment in metric_segments(analysis, &filtered.samples) {
            let points = segment.iter().map(|s| (origin.x_px() + s.x_m / mpp, origin.y_px() - s.y_m / mpp)).collect::<Vec<_>>();
            pixel_path(out, &points, left, y, scale, "f", "filtered-trajectory");
        }
    }
    let seed = analysis.manual_seed().target();
    writeln!(out, r#"<circle class="seed" data-layer="manual-seed" cx="{:.3}" cy="{:.3}" r="{:.3}"/>"#, left + seed.center().x_px() * scale, y + seed.center().y_px() * scale, seed.radius_px() * scale).unwrap();

    for sample in analysis.raw_observations() {
        if let Some(m) = sample.measurement {
            let (px, py) = (left + m.x_px * scale, y + m.y_px * scale);
            match sample.tracking_state {
                TrackingState::Tracked => dot(out, px, py, 3.0, "tracked"),
                TrackingState::LowConfidence => writeln!(out, r#"<circle class="low" data-state="low_confidence" cx="{px:.3}" cy="{py:.3}" r="6"/>"#).unwrap(),
                TrackingState::Lost => {}
            }
        }
    }
    let ty = y + h + 16.0;
    line(out, left, ty, left + w, ty, "g");
    for sample in analysis.raw_observations() {
        let px = time_x(analysis, sample.timestamp_s, left, w);
        state(out, px, ty, sample.tracking_state);
    }
    if let Some(frame) = frame {
        text(out, left, ty + 17.0, &format!("source frame {:.3}s / frame {}", frame.timestamp_s, frame.frame_index), "s");
    }
    ty + 22.0
}

fn position(out: &mut String, analysis: &Analysis, axis: Axis, top: f64) -> f64 {
    text(out, X, top, &format!("{} position vs time (m)", axis.name()), "h");
    let y = top + 14.0;
    rect(out, X, y, PW, PH, "p");
    let calibrated = &analysis.derived().calibrated.samples;
    let mut values = calibrated.iter().copied().map(|s| axis.pos(s)).collect::<Vec<_>>();
    if let Some(filtered) = analysis.derived().filtered.as_ref() {
        values.extend(filtered.samples.iter().copied().map(|s| axis.pos(s)));
    }
    let range = range(&values, 0.02);
    for segment in metric_segments(analysis, calibrated) {
        metric_path(out, analysis, &segment, axis, y, range, "r", "calibrated-raw");
    }
    if let Some(filtered) = analysis.derived().filtered.as_ref() {
        for segment in metric_segments(analysis, &filtered.samples) {
            metric_path(out, analysis, &segment, axis, y, range, "f", "filtered");
        }
    } else {
        text(out, X + 10.0, y + 20.0, "No filtered trajectory in canonical analysis.", "s");
    }
    labels(out, analysis, y, range, "m");
    y + PH
}

fn velocity(out: &mut String, analysis: &Analysis, axis: Axis, top: f64) -> f64 {
    text(out, X, top, &format!("{} velocity vs time (m/s)", axis.name()), "h");
    let y = top + 14.0;
    rect(out, X, y, PW, PH, "p");
    let Some(k) = analysis.derived().kinematics.as_ref() else {
        text(out, X + 10.0, y + 20.0, "No kinematic trajectory in canonical analysis.", "s");
        return y + PH;
    };
    let values = k.samples.iter().copied().filter_map(|s| axis.vel(s)).collect::<Vec<_>>();
    if values.is_empty() {
        text(out, X + 10.0, y + 20.0, "No supported velocity samples for this component.", "s");
        return y + PH;
    }
    let limits = range(&values, 0.05);
    let mut d = String::new();
    let mut active = false;
    let mut prev = None;
    for sample in k.samples.iter().copied() {
        let value = axis.vel(sample);
        if prev.is_some_and(|p| lost_between(analysis, p, sample.timestamp_s)) || value.is_none() { active = false; }
        if let Some(value) = value {
            let px = time_x(analysis, sample.timestamp_s, X, PW);
            let py = value_y(value, limits, y);
            write!(d, "{}{px:.3},{py:.3} ", if active {'L'} else {'M'}).unwrap();
            active = true;
        }
        prev = Some(sample.timestamp_s);
    }
    writeln!(out, r#"<path class="r" data-layer="velocity" data-axis="{}" d="{}"/>"#, axis.name().to_ascii_lowercase(), d.trim()).unwrap();
    labels(out, analysis, y, limits, "m/s");
    y + PH
}

fn confidence(out: &mut String, analysis: &Analysis, top: f64) -> f64 {
    text(out, X, top, "Confidence / tracking state vs time", "h");
    let y = top + 14.0;
    rect(out, X, y, PW, PH, "p");
    let mut d = String::new();
    let mut active = false;
    for sample in analysis.raw_observations() {
        if let Some(m) = sample.measurement {
            let px = time_x(analysis, sample.timestamp_s, X, PW);
            let py = value_y(f64::from(m.confidence), (0.0, 1.0), y);
            write!(d, "{}{px:.3},{py:.3} ", if active {'L'} else {'M'}).unwrap();
            active = true;
        } else { active = false; }
    }
    if !d.is_empty() { writeln!(out, r#"<path class="r" data-layer="confidence" d="{}"/>"#, d.trim()).unwrap(); }
    for sample in analysis.raw_observations() {
        let px = time_x(analysis, sample.timestamp_s, X, PW);
        let py = value_y(match sample.tracking_state { TrackingState::Tracked => .95, TrackingState::LowConfidence => .5, TrackingState::Lost => .05 }, (0.0,1.0), y);
        state(out, px, py, sample.tracking_state);
    }
    labels(out, analysis, y, (0.0,1.0), "confidence");
    y + PH
}

#[derive(Clone, Copy)] enum Axis { X, Y }
impl Axis {
    fn name(self) -> &'static str { match self { Self::X => "X", Self::Y => "Y" } }
    fn pos(self, s: MetricPositionSample) -> f64 { match self { Self::X => s.x_m, Self::Y => s.y_m } }
    fn vel(self, s: KinematicSample) -> Option<f64> { match self { Self::X => s.vx_mps, Self::Y => s.vy_mps } }
}

fn raw_segments(analysis: &Analysis) -> Vec<Vec<(f64,f64)>> {
    let mut all=Vec::new(); let mut cur=Vec::new();
    for s in analysis.raw_observations() {
        if s.tracking_state==TrackingState::Lost { if !cur.is_empty(){all.push(std::mem::take(&mut cur));} }
        else if let Some(m)=s.measurement { cur.push((m.x_px,m.y_px)); }
    }
    if !cur.is_empty(){all.push(cur);} all
}

fn metric_segments(analysis:&Analysis, samples:&[MetricPositionSample])->Vec<Vec<MetricPositionSample>>{
    let mut all=Vec::new(); let mut cur=Vec::new(); let mut i=0;
    for r in analysis.raw_observations(){
        if r.tracking_state==TrackingState::Lost { if !cur.is_empty(){all.push(std::mem::take(&mut cur));} continue; }
        if r.measurement.is_some(){ if let Some(s)=samples.get(i).copied(){cur.push(s);} i+=1; }
    }
    if !cur.is_empty(){all.push(cur);} all
}

#[allow(clippy::too_many_arguments)]
fn metric_path(out:&mut String, analysis:&Analysis, samples:&[MetricPositionSample], axis:Axis, top:f64, limits:(f64,f64), class:&str, layer:&str){
    let mut d=String::new();
    for (i,s) in samples.iter().copied().enumerate(){ let px=time_x(analysis,s.timestamp_s,X,PW); let py=value_y(axis.pos(s),limits,top); write!(d,"{}{px:.3},{py:.3} ",if i==0{'M'}else{'L'}).unwrap(); }
    if !d.is_empty(){writeln!(out,r#"<path class="{class}" data-layer="{layer}" data-axis="{}" d="{}"/>"#,axis.name().to_ascii_lowercase(),d.trim()).unwrap();}
}
fn pixel_path(out:&mut String, pts:&[(f64,f64)], left:f64, top:f64, scale:f64, class:&str, layer:&str){
    let mut d=String::new(); for(i,(x,y))in pts.iter().copied().enumerate(){write!(d,"{}{:.3},{:.3} ",if i==0{'M'}else{'L'},left+x*scale,top+y*scale).unwrap();}
    if !d.is_empty(){writeln!(out,r#"<path class="{class}" data-layer="{layer}" d="{}"/>"#,d.trim()).unwrap();}
}
fn labels(out:&mut String, analysis:&Analysis, top:f64, limits:(f64,f64), unit:&str){
    line(out,X,top+PH/2.0,X+PW,top+PH/2.0,"g");
    text(out,X+5.0,top+14.0,&format!("max {:.3} {unit}",limits.1),"s"); text(out,X+5.0,top+PH-5.0,&format!("min {:.3} {unit}",limits.0),"s");
    text(out,X+PW-180.0,top+PH-5.0,&format!("{:.3}s → {:.3}s",analysis.video().trim.start_s,analysis.video().trim.end_s),"s");
}
fn range(v:&[f64], pad:f64)->(f64,f64){ let mut lo=v.iter().copied().fold(0.0,f64::min); let mut hi=v.iter().copied().fold(0.0,f64::max); let p=((hi-lo)*.08).max(pad); lo-=p; hi+=p; (lo,hi) }
fn lost_between(a:&Analysis,s:f64,e:f64)->bool{a.raw_observations().iter().any(|r|r.tracking_state==TrackingState::Lost&&r.timestamp_s>s&&r.timestamp_s<e)}
fn time_x(a:&Analysis,t:f64,left:f64,w:f64)->f64{let span=a.video().trim.end_s-a.video().trim.start_s;if span<=f64::EPSILON{return left+w/2.0;}left+((t-a.video().trim.start_s)/span).clamp(0.0,1.0)*w}
fn value_y(v:f64,(lo,hi):(f64,f64),top:f64)->f64{if hi-lo<=f64::EPSILON{return top+PH/2.0;}top+PH-(v-lo)/(hi-lo)*PH}
fn state(out:&mut String,x:f64,y:f64,s:TrackingState){match s{TrackingState::Tracked=>dot(out,x,y,3.0,"tracked"),TrackingState::LowConfidence=>writeln!(out,r#"<circle class="low" data-state="low_confidence" cx="{x:.3}" cy="{y:.3}" r="5"/>"#).unwrap(),TrackingState::Lost=>cross(out,x,y)}}
fn dot(out:&mut String,x:f64,y:f64,r:f64,state:&str){writeln!(out, "<circle data-state=\"{state}\" cx=\"{x:.3}\" cy=\"{y:.3}\" r=\"{r:.3}\" fill=\"#1565c0\"/>").unwrap();}
fn cross(out:&mut String,x:f64,y:f64){writeln!(out,r#"<g data-state="lost"><line class="lost" x1="{:.3}" y1="{:.3}" x2="{:.3}" y2="{:.3}"/><line class="lost" x1="{:.3}" y1="{:.3}" x2="{:.3}" y2="{:.3}"/></g>"#,x-5.0,y-5.0,x+5.0,y+5.0,x-5.0,y+5.0,x+5.0,y-5.0).unwrap();}
fn text(out:&mut String,x:f64,y:f64,v:&str,class:&str){writeln!(out,r#"<text class="{class}" x="{x:.3}" y="{y:.3}">{}</text>"#,esc(v)).unwrap();}
fn rect(out:&mut String,x:f64,y:f64,w:f64,h:f64,class:&str){writeln!(out,r#"<rect class="{class}" x="{x:.3}" y="{y:.3}" width="{w:.3}" height="{h:.3}"/>"#).unwrap();}
fn line(out:&mut String,x1:f64,y1:f64,x2:f64,y2:f64,class:&str){writeln!(out,r#"<line class="{class}" x1="{x1:.3}" y1="{y1:.3}" x2="{x2:.3}" y2="{y2:.3}"/>"#).unwrap();}
fn esc(v:&str)->String{let mut o=String::new();for c in v.chars(){match c{'&'=>o.push_str("&amp;"),'<'=>o.push_str("&lt;"),'>'=>o.push_str("&gt;"),'"'=>o.push_str("&quot;"),'\''=>o.push_str("&apos;"),_=>o.push(c)}}o}

#[cfg(test)] mod tests{
    use super::*;
    fn golden()->Analysis{Analysis::from_json(include_str!("../../../crates/openbar-core/tests/fixtures/analysis-v1.golden.json")).unwrap()}
    #[test] fn failure_report_keeps_gaps_and_missing_layers_explicit(){let a=golden();let r=render_svg(&a,Path::new("golden.json"),None);assert!(r.contains("data-state=\"lost\""));assert!(r.contains("data-state=\"low_confidence\""));assert!(r.contains("No filtered trajectory"));assert!(r.contains("No kinematic trajectory"));assert!(!r.contains("data-layer=\"filtered-trajectory\""));assert_eq!(r,render_svg(&a,Path::new("golden.json"),None));}
    #[test] fn loss_breaks_raw_trajectory(){assert_eq!(raw_segments(&golden()),vec![vec![(960.0,700.0)],vec![(980.0,660.0)]]);}
    #[test] fn metadata_escapes_xml(){assert_eq!(esc("a&<b>\"c'"),"a&amp;&lt;b&gt;&quot;c&apos;");}
}
