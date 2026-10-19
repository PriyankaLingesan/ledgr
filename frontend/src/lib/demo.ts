/** Types and hooks for the Demo Learning Guide (only exists for a demonstration organization). */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { request } from './api';
import { useAuth } from './auth';

export interface GuidePersona {
  key: string;
  email: string;
  full_name: string;
  role: string;
  title: string;
  responsibilities: string;
  /** False until the local seed command has given the person a password. */
  active: boolean;
}

export interface GuideExercise {
  id: string;
  /** "state": do something, checked against the books. "answer": find something, checked against the data. */
  kind: 'state' | 'answer';
  completed: boolean;
  facts: Record<string, unknown>;
}

export interface Guide {
  scenario: string;
  organization_name: string;
  as_of: string;
  approval_threshold_minor: number;
  statement: { source_system: string; record_count: number; filename: string };
  ai_available: boolean;
  personas: GuidePersona[];
  exercises: GuideExercise[];
  completed_count: number;
  total_count: number;
}

export interface VerifyResult {
  exercise_id: string;
  passed: boolean;
  message: string;
}

export const demoKeys = { guide: ['demo', 'guide'] as const };

/** True when the signed-in organization is a fictional demonstration. */
export function useIsDemo(): boolean {
  const { user } = useAuth();
  return Boolean(user?.organization_demo_scenario);
}

export function useGuide() {
  const isDemo = useIsDemo();
  return useQuery({
    queryKey: demoKeys.guide,
    queryFn: () => request<Guide>('/demo/guide'),
    enabled: isDemo,
    retry: false,
  });
}

/** Ask the server to check an exercise against the real state of the books. */
export function useVerifyExercise() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, answer }: { id: string; answer?: string }) =>
      request<VerifyResult>(`/demo/guide/${id}/verify`, { method: 'POST', body: { answer: answer ?? null } }),
    onSuccess: () => client.invalidateQueries({ queryKey: demoKeys.guide }),
  });
}
