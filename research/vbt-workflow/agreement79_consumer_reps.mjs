// #79 hand-check bridge: the pinned consumer's own OpenBar parser output for one analysis file.
//
// Usage: node --experimental-strip-types agreement79_consumer_reps.mjs <consumer-app-dir> <analysis-path>
//
// Imports the consumer's parser and segmentation rule, decodes the exact file bytes as strict UTF-8,
// calls parseOpenBarAnalysis(text, CONCENTRIC_SEGMENTATION_V2) and writes one JSON line:
// {parserVersion, segmentationRule, frameCount, breaks, reps}. Reps are passed through exactly as
// returned. No other logic: windows, pairing and statistics stay with the consumer.
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

async function main() {
    const [appDir, analysisPath] = process.argv.slice(2);
    if (!appDir || !analysisPath || process.argv.length !== 4) {
        throw new Error('Usage: agreement79_consumer_reps.mjs <consumer-app-dir> <analysis-path>');
    }
    const observations = resolve(appDir, 'src', 'observations');
    const parser = await import(pathToFileURL(resolve(observations, 'openBarAnalysis.ts')).href);
    const segmentation = await import(pathToFileURL(resolve(observations, 'concentricSegmentation.ts')).href);
    const bytes = await readFile(resolve(analysisPath));
    const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    const parsed = parser.parseOpenBarAnalysis(text, segmentation.CONCENTRIC_SEGMENTATION_V2);
    const output = {
        parserVersion: parsed.parserVersion,
        segmentationRule: parsed.segmentationRule,
        frameCount: parsed.frames.length,
        breaks: parsed.breaks,
        reps: parsed.reps,
    };
    process.stdout.write(`${JSON.stringify(output)}\n`);
}

main().catch(error => {
    process.stderr.write(`${error instanceof Error ? error.message : 'consumer parser bridge failed'}\n`);
    process.exitCode = 1;
});
