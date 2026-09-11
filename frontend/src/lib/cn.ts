import clsx, { type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

/**
 * Class name helper.
 *
 * `twMerge` is what makes component overrides predictable: a caller passing
 * `w-56` to a control whose base style is `w-full` gets 56, rather than
 * whichever utility happens to appear later in the generated stylesheet.
 *
 * `text-2xs` is a project-defined step on the font-size scale. Without this
 * declaration tailwind-merge would classify it as a text *colour* and silently
 * drop it whenever a colour was applied alongside it.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: ['2xs'] }],
    },
  },
});

export function cn(...values: ClassValue[]): string {
  return twMerge(clsx(values));
}
