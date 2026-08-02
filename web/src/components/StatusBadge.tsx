import type { ReactNode } from 'react';
import type { SeverityTone } from '../lib/status';

export interface StatusBadgeProps {
  tone: SeverityTone;
  children: ReactNode;
  title?: string;
}

export function StatusBadge({ tone, children, title }: StatusBadgeProps) {
  return (
    <span className={`badge badge--${tone}`} title={title} role="status">
      {children}
    </span>
  );
}
