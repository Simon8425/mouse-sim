import type { Dispatch } from 'react';
import type { ApiClient } from '../api/client';
import type { ProjectAction } from '../state/projectStore';
import { errorMessage, isAbortError } from '../api/errors';

export async function loadBaseline(
  client: ApiClient,
  dispatch: Dispatch<ProjectAction>,
  signal?: AbortSignal,
): Promise<void> {
  dispatch({ type: 'LOAD_BASELINE_START' });
  try {
    const res = await client.getBaseline(signal);
    if (signal?.aborted) return;
    dispatch({
      type: 'LOAD_BASELINE_OK',
      project: res.project,
      name: res.source || 'mouse_baseline',
    });
    dispatch({ type: 'RUN_STUDY' });
  } catch (err: unknown) {
    if (signal?.aborted || isAbortError(err)) return;
    dispatch({ type: 'LOAD_BASELINE_ERROR', message: errorMessage(err) });
  }
}
