/**
 * Telemetry Log Debugger — exporter.
 *
 * Downloads the full session as JSON or the frame stream as CSV, and copies a
 * single frame to the clipboard as JSON.
 */
import type {
  TelemetryFrame,
  TelemetryLogSession,
} from '../api/telemetryDebuggerContracts';
import { framesToCsv, sessionToJson } from './telemetrySessionBuilder';

function download(filename: string, contents: string, mime: string): void {
  const blob = new Blob([contents], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function exportSessionJson(session: TelemetryLogSession): void {
  download(`${session.session_id}.json`, sessionToJson(session), 'application/json');
}

export function exportFramesCsv(session: TelemetryLogSession): void {
  download(`${session.session_id}.csv`, framesToCsv(session.frames), 'text/csv');
}

export async function copyFrameToClipboard(frame: TelemetryFrame): Promise<void> {
  await navigator.clipboard.writeText(JSON.stringify(frame, null, 2));
}
