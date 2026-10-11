// #79 bridge to the pinned consumer's own parsers (hand-check reps mode and inventory preflight mode).
//
// Usage:
//   node --experimental-strip-types agreement79_consumer_reps.mjs <consumer-app-dir> <analysis-path>
//   node --experimental-strip-types agreement79_consumer_reps.mjs preflight <consumer-app-dir> <analysis-path> <wl-csv-path>
//
// Reps mode (hand-check): imports the consumer's parser and segmentation rule, decodes the exact file
// bytes as strict UTF-8, calls parseOpenBarAnalysis(text, CONCENTRIC_SEGMENTATION_V2) and writes one
// JSON line: {parserVersion, segmentationRule, frameCount, breaks, reps}. Reps are passed through exactly
// as returned. No other logic: windows, pairing and statistics stay with the consumer.
//
// Preflight mode (inventory): decodes and parses both files exactly as app/scripts/velocity-agreement-report.mjs
// does for concentric-segmentation-v2 (strict UTF-8, parseWlAnalysisCsv(text, WL_ANALYSIS_CSV_PARSER_V2),
// parseOpenBarAnalysis(text, CONCENTRIC_SEGMENTATION_V2)) and writes ONLY whether each was accepted:
// {"openbar":{"accepted":bool,"error":string|null},"wl":{"accepted":bool,"error":string|null}}.
// It never writes reps, counts or values. A module that cannot be loaded or a file that cannot be read
// exits 1 (infrastructure); only strict decoding and parsing are reported as a rejection.
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

async function consumerModule(appDir, name) {
    return import(pathToFileURL(resolve(appDir, 'src', 'observations', name)).href);
}

function decode(bytes) {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
}

async function decodeFile(path) {
    return decode(await readFile(resolve(path)));
}

async function reps(appDir, analysisPath) {
    const parser = await consumerModule(appDir, 'openBarAnalysis.ts');
    const segmentation = await consumerModule(appDir, 'concentricSegmentation.ts');
    const text = await decodeFile(analysisPath);
    const parsed = parser.parseOpenBarAnalysis(text, segmentation.CONCENTRIC_SEGMENTATION_V2);
    return {
        parserVersion: parsed.parserVersion,
        segmentationRule: parsed.segmentationRule,
        frameCount: parsed.frames.length,
        breaks: parsed.breaks,
        reps: parsed.reps,
    };
}

// Only strict decoding and parsing can reject; the consumer CLI refuses the same bytes the same way.
function verdict(parse) {
    try {
        parse();
        return { accepted: true, error: null };
    } catch (error) {
        const message = error instanceof Error && error.message ? error.message : 'parser rejected the file';
        return { accepted: false, error: message };
    }
}

async function preflight(appDir, analysisPath, wlCsvPath) {
    const openBarParser = await consumerModule(appDir, 'openBarAnalysis.ts');
    const wlParser = await consumerModule(appDir, 'wlAnalysisCsv.ts');
    const segmentation = await consumerModule(appDir, 'concentricSegmentation.ts');
    // Read outside verdict(): an unreadable file is infrastructure (exit 1), never a parser rejection.
    const analysisBytes = await readFile(resolve(analysisPath));
    const wlBytes = await readFile(resolve(wlCsvPath));
    const openbar = verdict(() => {
        const text = decode(analysisBytes);
        openBarParser.parseOpenBarAnalysis(text, segmentation.CONCENTRIC_SEGMENTATION_V2);
    });
    const wl = verdict(() => {
        const text = decode(wlBytes);
        wlParser.parseWlAnalysisCsv(text, wlParser.WL_ANALYSIS_CSV_PARSER_V2);
    });
    return { openbar, wl };
}

async function main() {
    const args = process.argv.slice(2);
    let output;
    if (args.length === 4 && args[0] === 'preflight') {
        output = await preflight(args[1], args[2], args[3]);
    } else if (args.length === 2 && args[0] && args[1]) {
        output = await reps(args[0], args[1]);
    } else {
        throw new Error('Usage: agreement79_consumer_reps.mjs <consumer-app-dir> <analysis-path> | '
            + 'preflight <consumer-app-dir> <analysis-path> <wl-csv-path>');
    }
    process.stdout.write(`${JSON.stringify(output)}\n`);
}

main().catch(error => {
    process.stderr.write(`${error instanceof Error ? error.message : 'consumer parser bridge failed'}\n`);
    process.exitCode = 1;
});
