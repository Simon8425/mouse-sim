import { describe, it, expect } from 'vitest';
import {
  dispositionLabel,
  dispositionTone,
  validityLabel,
  lifecycleLabel,
  gateStatusLabel,
} from '../lib/status';

describe('status library', () => {
  it('preserves qualification dispositions verbatim', () => {
    expect(dispositionLabel('exploration_only')).toBe('Exploration only');
    expect(dispositionLabel('qualification_pending_review')).toBe('Pending review');
    expect(dispositionLabel('qualification_accepted')).toBe('Accepted');
    expect(dispositionLabel('qualification_blocked')).toBe('Qualification blocked');
    expect(dispositionLabel('qualification_rejected')).toBe('Rejected');

    expect(dispositionTone('qualification_accepted')).toBe('ok');
    expect(dispositionTone('qualification_pending_review')).toBe('warn');
  });

  it('formats validity and gate status labels', () => {
    expect(validityLabel('valid')).toBe('Valid');
    expect(lifecycleLabel('completed')).toBe('Completed');
    expect(gateStatusLabel({ passed: true, evaluable: true, blocker: false })).toBe('Passed');
    expect(gateStatusLabel({ passed: false, evaluable: false, blocker: false })).toBe('Not evaluable');
  });
});
