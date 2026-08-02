export type SeverityTone = 'ok' | 'warn' | 'error' | 'blocker' | 'neutral' | 'info';

export function severityTone(severity: string): SeverityTone {
  switch (severity.toLowerCase()) {
    case 'blocker':
      return 'blocker';
    case 'error':
      return 'error';
    case 'warning':
    case 'warn':
      return 'warn';
    case 'info':
      return 'info';
    default:
      return 'neutral';
  }
}

export function severityLabel(severity: string): string {
  switch (severity.toLowerCase()) {
    case 'blocker':
      return 'Blocker';
    case 'error':
      return 'Error';
    case 'warning':
    case 'warn':
      return 'Warning';
    case 'info':
      return 'Info';
    default:
      return severity;
  }
}

export function lifecycleLabel(state: string): string {
  switch (state.toLowerCase()) {
    case 'completed':
      return 'Completed';
    case 'failed':
      return 'Failed';
    default:
      return state;
  }
}

export function validityLabel(state: string): string {
  switch (state.toLowerCase()) {
    case 'valid':
      return 'Valid';
    case 'approximate':
      return 'Approximate';
    case 'failed':
    case 'invalid':
      return 'Failed';
    case 'inconclusive':
      return 'Inconclusive';
    default:
      return state;
  }
}

export function dispositionLabel(disposition: string): string {
  switch (disposition) {
    case 'exploration_only':
      return 'Exploration only';
    case 'qualification_blocked':
      return 'Qualification blocked';
    case 'qualification_pending_review':
      return 'Pending review';
    case 'qualification_accepted':
      return 'Accepted';
    case 'qualification_rejected':
      return 'Rejected';
    case 'qualification_superseded':
      return 'Superseded';
    default:
      return disposition;
  }
}

export function dispositionTone(disposition: string): SeverityTone {
  switch (disposition) {
    case 'qualification_blocked':
      return 'blocker';
    case 'qualification_pending_review':
      return 'warn';
    case 'qualification_accepted':
      return 'ok';
    case 'qualification_rejected':
      return 'error';
    default:
      return 'neutral';
  }
}

export function modeLabel(mode: string): string {
  switch (mode.toLowerCase()) {
    case 'exploration':
      return 'Exploration';
    case 'qualification':
      return 'Qualification';
    default:
      return mode;
  }
}

export function gateStatusLabel({
  passed,
  evaluable,
  blocker,
}: {
  passed: boolean;
  evaluable: boolean;
  blocker: boolean;
}): string {
  if (blocker) return 'Blocker';
  if (!evaluable) return 'Not evaluable';
  return passed ? 'Passed' : 'Failed';
}

export function validityConfidenceLabel(confidence: string): string {
  if (!confidence) return '—';
  return confidence.charAt(0).toUpperCase() + confidence.slice(1).toLowerCase();
}
